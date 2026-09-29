import csv
import io
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
import app as module
from test_workflow import client, catalog, stock
from test_storehub import remote, sync

BASE='/api/storehub/purchase-orders'


def test_purchase_order_details_and_export_use_employee_name(client, remote):
    sync(client)
    remote.orders = [purchase(remote, completedBy='employee-test')]
    with module.connection() as db:
        module.integration.save(db, 'people_employees', json.dumps(dict(records=[dict(id='employee-test', name='Ana Receiver')], refreshed='')))
    assert refresh(client).status_code == 200
    response = client.get(BASE+'/133')
    assert response.status_code == 200
    assert response.json['completedByName'] == 'Ana Receiver'
    exported = client.get(BASE+'/133.csv')
    assert 'Ana Receiver' in exported.get_data(as_text=True)

def purchase(remote, ident=133, days=0, **extra):
    return dict(purchaseOrderId=ident, targetStoreId='s0', supplierId='supplier-test',
                createdTime=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(),
                status='open', total=24, subTotal=24, tax=0, discount=0,
                notes='Deliver, "carefully"\nWarehouse', orderedItems=[dict(productId=remote.pid,
                orderedQuantity=12, receivedQuantity=35, supplierPrice=2, subTotal=24)], **extra)

def refresh(client, days=30):
    today=datetime.now(timezone.utc).date()
    return client.post(BASE+'/refresh',json={'from':(today-timedelta(days=days)).isoformat(),'to':today.isoformat()})

def test_purchase_details_csv_and_refresh_never_change_local_stock(client,remote):
    remote.orders=[purchase(remote)]
    sync(client)
    before=catalog(client)
    assert refresh(client).status_code==200
    detail=client.post(BASE+'/133/refresh',json={})
    assert detail.status_code==200,detail.json
    item=detail.json['orderedItems'][0]
    assert item['outstanding']=='0' and item['overReceived']=='23'
    assert item['name']==remote.products[0]['name'] and item['sku']==remote.products[0]['sku']
    exported=client.get(BASE+'/133.csv')
    assert exported.status_code==200 and exported.data.startswith(b'\xef\xbb\xbf')
    rows=list(csv.DictReader(io.StringIO(exported.data.decode('utf-8-sig'))))
    with (module.ROOT/'data/Purchase_Orders_09-28-2026.csv').open(encoding='utf-8-sig',newline='') as f:
        assert list(rows[0])==next(csv.reader(f))
    assert rows[0]['P.O ID']=='PO0133' and rows[0]['Notes']==remote.orders[0]['notes']
    assert rows[1]['Received Quantity']=='35' and rows[1]['Ordered Quantity']=='12'
    assert catalog(client)==before
    assert all(call[1]=='GET' for call in remote.calls)

def test_purchase_filters_pagination_and_removed_store_access(client,remote):
    remote.orders=[purchase(remote,i) for i in range(100,123)]
    excluded=purchase(remote,999);excluded['targetStoreId']='excluded'
    remote.orders.append(excluded)
    sync(client)
    page=client.get(BASE+'?size=10&page=2').json
    assert page['total']==23 and page['pages']==3 and len(page['orders'])==10
    assert not page['needsRefresh']
    assert client.get(BASE+'?status=completed').json['total']==0
    assert client.get(BASE+'?store=s1').json['total']==0
    assert client.get(BASE,query_string={'q':remote.products[0]['name']}).json['total']==23
    assert client.get(BASE+'?q=PO0101').json['total']==1
    assert client.get(BASE+'/999').status_code==400
    assert client.get(BASE+'/999.csv').status_code==400
    assert client.get(BASE+'?page=0').status_code==400
    assert client.get(BASE+'?size=100').status_code==400
    assert client.get(BASE+'?from=bad').status_code==400

def test_history_survives_recent_auto_sync_and_failed_refresh(client,remote):
    old=purchase(remote,1,days=70)
    remote.orders=[old]
    sync(client)
    assert refresh(client,90).status_code==200
    recent=purchase(remote,2)
    remote.orders=[recent]
    sync(client)
    assert client.get(BASE+'/1').status_code==200

    assert client.get(BASE+'/2').status_code==200
    invalid=deepcopy(recent);invalid['orderedItems'][0]['receivedQuantity']='NaN'
    remote.orders=[invalid]
    assert refresh(client).status_code==400
    assert client.get(BASE+'/2').json['orderedItems'][0]['receivedQuantity']==35
    remote.orders=[]
    assert refresh(client).status_code==200
    assert client.get(BASE+'/2').status_code==400
    assert client.get(BASE+'/1').status_code==200

def test_auto_sync_purchase_order_failure_preserves_cache_and_shows_warning(client,remote):
    remote.orders=[purchase(remote)]
    sync(client)
    del remote.orders[0]['createdTime']
    state=sync(client)
    assert state['warnings']
    result=client.get(BASE).json
    assert result['total']==1 and 'creation date' in result['error']

def test_detail_response_identity_and_location_checked_before_cache_write(client,remote):
    remote.orders=[purchase(remote)]
    sync(client)
    original=remote.call
    wrong=purchase(remote,134)
    remote.call=lambda path,*args:wrong if path=='/purchaseOrders/133' else original(path,*args)
    assert client.post(BASE+'/133/refresh',json={}).status_code==400
    assert client.get(BASE+'/133').json['id']=='133'
    wrong['purchaseOrderId']=133;wrong['targetStoreId']='excluded'
    assert client.post(BASE+'/133/refresh',json={}).status_code==400
    assert client.get(BASE+'/133').json['targetStoreId']=='s0'

def test_csv_supplier_labels_require_matching_date_and_store(client,remote,tmp_path,monkeypatch):
    remote.orders=[purchase(remote)]
    sync(client)
    root=tmp_path/'source';(root/'data').mkdir(parents=True)
    source=module.ROOT/'data/Purchase_Orders_09-28-2026.csv'
    with source.open(encoding='utf-8-sig',newline='') as f: headers=next(csv.reader(f))
    order=remote.orders[0]
    when=datetime.fromisoformat(order['createdTime']).astimezone(timezone(timedelta(hours=8))).strftime('%m/%d/%Y %H:%M')
    path=root/'data'/source.name
    def write(store):
        with path.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=headers);w.writeheader()
            w.writerow({'P.O ID':'PO0133','Created Date':when,'Target Store':store,'Supplier':'Known supplier','Requested By':'Buyer'})
    monkeypatch.setattr(module.integration.purchase_orders,'root',root)
    write(module.WAREHOUSE)
    assert client.get(BASE+'/133').json['supplierName']=='Known supplier'
    write('Different store')
    assert client.get(BASE+'/133').json['supplierName']==''
