import csv
import io
import json
from datetime import date
import app as module
from test_workflow import client, add_product, catalog
from wastage_imports import HEADERS


def report(client, **changes):
    pid = add_product(client, '20')
    product = next(p for p in catalog(client)['products'] if p['id']==pid)
    common = {'W.R ID':'WR0001','Created Date':date.today().strftime('%m/%d/%Y')+' 15:23','Store':catalog(client)['locations'][1],'Created By':'Ana Staff'}
    rows = [dict(common, **{'Total (PHP)':'4.00','Notes':'Supplier spoiled'}),dict(common, **{'Product Name':product['Product Name'],'SKU':product['SKU'],'Quantity':'2','Cost (PHP)':'2','SubTotal (PHP)':'4','Reason':'Spoiled'})]
    rows[1].update(changes)
    return rows


def preview(client, rows):
    output=io.StringIO();writer=csv.DictWriter(output,fieldnames=HEADERS);writer.writeheader();writer.writerows(rows)
    return client.post('/api/wastage/import-preview',data={'file':(io.BytesIO(output.getvalue().encode()),'wastage.csv')})


def test_import_preview_idempotency_and_local_edit_preserved_without_stock_changes(client):
    rows=report(client)
    with module.connection() as db:
        before=list(db.execute('SELECT fields FROM products'));movements=db.execute('SELECT COUNT(*) FROM movements').fetchone()[0]
    draft=preview(client,rows);assert draft.status_code==200,draft.json
    assert client.get('/api/wastage').json['total']==0
    result=client.post('/api/wastage/import',json={'token':draft.json['token']});assert result.json['imported_lines']==1
    saved=client.get('/api/wastage').json['records'][0]
    assert saved['source']['record_id']=='WR0001' and saved['source']['created_by']=='Ana Staff'
    assert saved['source']['reason']=='Spoiled' and saved['notes']=='Supplier spoiled'
    assert client.post('/api/wastage/import',json={'token':draft.json['token']}).json['repeated']
    with module.connection() as db:
        assert [r[0] for r in db.execute('SELECT fields FROM products')]==[r[0] for r in before]
        assert db.execute('SELECT COUNT(*) FROM movements').fetchone()[0]==movements
    assert client.post('/api/wastage/'+saved['id']+'/edit',data=dict(revision=0,waste_date=saved['waste_date'],branch=saved['branch'],product_id=saved['product_id'],quantity='3',notes='Local correction',photo_action='keep')).status_code==200
    assert client.get('/api/wastage').json['records'][0]['source']==saved['source']
    assert client.post('/api/wastage/'+saved['id']+'/delete',json={'revision':1}).status_code==200
    second=preview(client,rows);assert second.json['records'][0]['change']=='unchanged'
    assert client.post('/api/wastage/import',json={'token':second.json['token']}).json['imported_lines']==0
    assert client.get('/api/wastage').json['total']==0
    assert client.post('/api/wastage/'+saved['id']+'/restore',json={'revision':2}).status_code==200


def test_changed_source_and_bad_product_or_amount_rejected(client):
    rows=report(client);draft=preview(client,rows).json
    client.post('/api/wastage/import',json={'token':draft['token']})
    rows[1]['Reason']='Damaged'
    assert preview(client,rows).status_code==400
    rows[0]['W.R ID']=rows[1]['W.R ID']='WR0002';rows[1]['SKU']='unknown'
    assert preview(client,rows).status_code==400
    rows[1]['SKU']='TEST-001';rows[1]['Quantity']='NaN'
    assert preview(client,rows).status_code==400


def test_unknown_stores_excluded_and_summary_only_rejected(client):
    rows=report(client)
    assert preview(client,rows[:1]).status_code==400
    for row in rows:row['Store']='688'
    draft=preview(client,rows);assert draft.status_code==200 and draft.json['records']==[] and draft.json['skipped']==['WR0001']


def test_catalog_change_expired_preview_and_cross_origin_blocked(client):
    rows=report(client);draft=preview(client,rows).json
    with module.connection() as db:
        row=db.execute("SELECT id,fields FROM products WHERE json_extract(fields,'$.SKU')='TEST-001'").fetchone();fields=json.loads(row['fields']);fields['SKU']='Changed'
        db.execute('UPDATE products SET fields=? WHERE id=?',(json.dumps(fields),row['id']))
    assert client.post('/api/wastage/import',json={'token':draft['token']}).status_code==400
    assert client.get('/api/wastage').json['total']==0
    assert client.post('/api/wastage/import',json={'token':draft['token']},headers={'Origin':'https://example.com'}).status_code==400
    with module.connection() as db:db.execute('UPDATE wastage_import_previews SET created=0')
    assert client.post('/api/wastage/import',json={'token':draft['token']}).status_code==400
