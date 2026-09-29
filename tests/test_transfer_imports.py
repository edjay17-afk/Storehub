import csv
import io
import json
import time
import pytest
import app as module
from stock_transfers import HEADERS
from test_workflow import client

BASE='/api/imported-transfers'

def csv_bytes(rows, headers=HEADERS):
    output=io.StringIO(newline='')
    writer=csv.DictWriter(output,fieldnames=headers,quoting=csv.QUOTE_ALL,lineterminator='\r\n')
    writer.writeheader();writer.writerows(rows)
    return ('\ufeff'+output.getvalue()).encode('utf-8')

def report(ident='ST9000', status='Created'):
    common={'S.T ID':ident,'Created Date':'09/28/2026 09:57','Source Store':module.WAREHOUSE,
            'Target Store':'The Daily Centro - Bantayan','Status':status}
    return [dict(common,**{'Total (RM)':'25.50'}),dict(common,**{'No.':'1','Product Name':'Product, "quoted"\nline',
            'SKU':'0000123','Ordered Qty':'1.5','Cost (RM)':'17.00','SubTotal (RM)':'25.50','Unit':'Pack'})]

def preview(client,raw):
    return client.post(BASE+'/preview',data={'file':(io.BytesIO(raw),'transfers.csv')})

def save(client,rows):
    response=preview(client,csv_bytes(rows))
    assert response.status_code==200,response.json
    committed=client.post(BASE+'/import',json={'token':response.json['token']})
    assert committed.status_code==200,committed.json
    return response.json,committed.json

def ledger():
    with module.connection() as db:
        return {table:[tuple(r) for r in db.execute(f'SELECT * FROM {table}')] for table in ['products','documents','movements']}

def test_supplied_report_import_roundtrip_excludes_688_and_preserves_ledger(client):
    before=ledger()
    raw=(module.ROOT/'data/Stock_Transfer_09-28-2026.csv').read_bytes()
    result=preview(client,raw)
    assert result.status_code==200,result.json
    assert result.json['counts']==dict(new=13,updated=0,unchanged=0)
    assert len(result.json['skipped'])==12
    assert client.get(BASE).json['allTotal']==0 # Preview never saves the reports.
    response=client.post(BASE+'/import',json={'token':result.json['token']})
    assert response.status_code==200 and response.json['skipped']==12
    listing=client.get(BASE+'?size=10').json
    assert listing['total']==13 and listing['pages']==2
    assert len(client.get(BASE+'?size=10&page=2').json['orders'])==3
    first=result.json['orders'][0]
    exported=client.get(BASE+'/'+first['id']+'.csv')
    assert exported.data.startswith(b'\xef\xbb\xbf')
    rows=list(csv.DictReader(io.StringIO(exported.data.decode('utf-8-sig'))))
    assert rows==first['rows'] and list(rows[0])==HEADERS
    assert ledger()==before

def test_idempotent_reimport_updates_status_without_duplicates(client):
    rows=report()
    initial,result=save(client,rows)
    repeat=client.post(BASE+'/import',json={'token':initial['token']})
    assert repeat.json==result
    again,result=save(client,rows)
    assert again['counts']['unchanged']==1
    rows=report(status='Shipped')
    for row in rows: row['Shipped Date']='09/29/2026 10:00';row['Sent By']='Sender'
    changed,result=save(client,rows)
    assert changed['orders'][0]['previousStatus']=='Created' and result['counts']['updated']==1
    assert client.get(BASE).json['allTotal']==1
    assert client.get(BASE+'/ST9000').json['rows'][0]['Sent By']=='Sender'
    assert client.get(BASE+'?status=Created').json['total']==0
    assert client.get(BASE,query_string={'q':'0000123'}).json['total']==1
    assert client.get(BASE+'?from=2026-09-29').json['total']==0

@pytest.mark.parametrize('mutation', ['nan','conflicting','duplicate','missing-summary','unknown-store','missing-quantity'])
def test_bad_report_rolls_back_entire_file(client,mutation):
    rows=report('ST9001')
    bad=report('ST9002')
    if mutation=='nan':bad[1]['Ordered Qty']='NaN'
    if mutation=='conflicting':bad[1]['Status']='Received'
    if mutation=='duplicate':bad.append(dict(bad[1]))
    if mutation=='missing-summary':bad=bad[1:]
    if mutation=='unknown-store':bad[0]['Target Store']='Unknown branch'
    if mutation=='missing-quantity':bad[1]['Ordered Qty']=''
    assert preview(client,csv_bytes(rows+bad)).status_code==400
    assert client.get(BASE).json['allTotal']==0

def test_wrong_template_invalid_upload_and_dates_are_rejected(client):
    assert preview(client,b'P.O ID,Ordered Quantity\r\nPO1,3').status_code==400
    assert preview(client,b'\xff\xfe').status_code==400
    assert client.post(BASE+'/import',json={}).status_code==400
    assert client.get(BASE+'?size=100').status_code==400
    assert client.get(BASE+'?from=bad').status_code==400
    assert client.get(BASE+'/missing').status_code==400
    assert client.post(BASE+'/preview',data={'file':(io.BytesIO(csv_bytes(report())),'file.csv')},headers={'Origin':'https://another-site.example'}).status_code==400

def test_reused_reference_and_concurrent_import_require_new_preview(client):
    rows=report();save(client,rows)
    different=report()
    for row in different: row['Created Date']='09/27/2026 09:57'
    assert preview(client,csv_bytes(different)).status_code==400
    pending=preview(client,csv_bytes(report(status='Shipped'))).json
    save(client,report(status='Received'))
    assert client.post(BASE+'/import',json={'token':pending['token']}).status_code==400
    assert client.get(BASE+'/ST9000').json['status']=='Received'

def test_preview_expiry_and_removed_only_report(client):
    rows=report()
    for row in rows:row['Target Store']='688'
    result=preview(client,csv_bytes(rows)).json
    assert result['orders']==[] and result['skipped']==['ST9000']
    token=preview(client,csv_bytes(report())).json['token']
    with module.connection() as db:db.execute('UPDATE transfer_imports SET created=? WHERE token=?',(time.time()-1801,token))
    assert client.post(BASE+'/import',json={'token':token}).status_code==400
    assert client.get(BASE).json['allTotal']==0
