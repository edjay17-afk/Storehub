import csv
import io
import json
from decimal import Decimal

import pytest
import app as module


@pytest.fixture
def client(tmp_path):
    original = module.app.config['DATABASE']
    module.app.config.update(DATABASE=str(tmp_path / 'test.db'), TESTING=True)
    module.initialize()
    with module.app.test_client() as c:
        yield c
    module.app.config['DATABASE'] = original


def catalog(client):
    return client.get('/api/catalog').json


def stock(client, pid, location):
    return Decimal(next(p for p in catalog(client)['products'] if p['id'] == pid)[location + '_Quantity'] or '0')


def add_product(client, qty='20'):
    response = client.post('/api/products', json=dict(name='Test, "Product"', sku='TEST-001', cost='2.345', quantity=qty))
    assert response.status_code == 201
    return response.json['id']


def order(client, kind, items):
    c = catalog(client)
    response = client.post('/api/documents', json=dict(kind=kind, items=items, supplier='Supplier, Inc.', target=c['locations'][1], requested_by='Requester'))
    assert response.status_code == 201
    return response.json['id']


def act(client, did, action, **extra):
    return client.post(f'/api/documents/{did}/action', json=dict(action=action, actor='Receiver', **extra))


def rows(response):
    return list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'))))


def test_seed_and_product_export_preserve_template(client):
    assert len(catalog(client)['products']) == 1361
    with open(module.ROOT / 'data/Products.csv', encoding='utf-8-sig', newline='') as f:
        source = list(csv.reader(f))
    included = [i for i, h in enumerate(source[0]) if not h.startswith('688_')]
    source = [[row[i] for i in included] for row in source]
    assert rows(client.get('/exports/products.csv?scope=all')) == source
    pid = add_product(client)
    exported = rows(client.get('/exports/products.csv'))
    assert exported[:2] == source[:2]
    p = dict(zip(exported[0], exported[2]))
    assert p['Product Name'] == 'Test, "Product"'
    assert p['Product Id'] == ''
    assert p[module.WAREHOUSE + '_Quantity'] == '20'
    assert client.post('/api/products', json=dict(name='Duplicate', sku='TEST-001')).status_code == 400


def test_purchase_partial_receipt_and_csv(client):
    pid = add_product(client, '0')
    did = order(client, 'purchase', [dict(product_id=pid, quantity='10', cost='2.345')])
    assert stock(client, pid, module.WAREHOUSE) == 0
    assert act(client, did, 'receive', quantities={pid: '4'}).status_code == 200
    assert stock(client, pid, module.WAREHOUSE) == 4
    assert catalog(client)['documents'][0]['status'] == 'PartiallyReceived'
    assert act(client, did, 'receive', quantities={pid: '7'}).status_code == 400
    assert stock(client, pid, module.WAREHOUSE) == 4
    assert act(client, did, 'receive', quantities={pid: '6'}).status_code == 200
    assert act(client, did, 'receive', quantities={pid: '1'}).status_code == 400
    assert stock(client, pid, module.WAREHOUSE) == 10
    exported = rows(client.get(f'/exports/documents/{did}.csv'))
    with open(module.ROOT / 'data/Purchase_Orders_09-28-2026.csv', encoding='utf-8-sig', newline='') as f:
        assert exported[0] == next(csv.reader(f))
    summary = dict(zip(exported[0], exported[1]))
    assert summary['Target Store'] == module.WAREHOUSE
    assert summary['Completed By'] == 'Receiver'
    assert summary['Total (RM)'] == '23.45'


def test_transfer_insufficient_stock_atomicity_and_receipt(client):
    pid = add_product(client, '20')
    other = client.post('/api/products', json=dict(name='Second', sku='SECOND', quantity='1')).json['id']
    branch = catalog(client)['locations'][1]
    failed = order(client, 'transfer', [dict(product_id=pid, quantity='5'), dict(product_id=other, quantity='2')])
    assert act(client, failed, 'ship').status_code == 400
    assert stock(client, pid, module.WAREHOUSE) == 20
    assert stock(client, other, module.WAREHOUSE) == 1
    did = order(client, 'transfer', [dict(product_id=pid, quantity='5', cost='2')])
    assert act(client, did, 'receive').status_code == 400
    assert act(client, did, 'ship').status_code == 200
    assert act(client, did, 'ship').status_code == 400
    assert stock(client, pid, module.WAREHOUSE) == 15
    assert stock(client, pid, branch) == 0
    assert act(client, did, 'receive').status_code == 200
    assert act(client, did, 'receive').status_code == 400
    assert stock(client, pid, branch) == 5
    exported = rows(client.get(f'/exports/documents/{did}.csv'))
    with open(module.ROOT / 'data/Stock_Transfer_09-28-2026.csv', encoding='utf-8-sig', newline='') as f:
        assert exported[0] == next(csv.reader(f))
    assert dict(zip(exported[0], exported[1]))['Received By'] == 'Receiver'


def test_validation_counts_and_cancel(client):
    pid = add_product(client)
    assert client.post('/api/receive', json=dict(product_id=pid, mode='add', quantity='NaN', reason='test')).status_code == 400
    assert client.post('/api/receive', json=dict(product_id=pid, mode='count', quantity='0', reason='Physical count')).status_code == 200
    assert stock(client, pid, module.WAREHOUSE) == 0
    did = order(client, 'purchase', [dict(product_id=pid, quantity='2')])
    assert act(client, did, 'cancel').status_code == 200
    assert act(client, did, 'receive', quantities={pid:'2'}).status_code == 400
    assert stock(client, pid, module.WAREHOUSE) == 0


def test_removed_store_and_barcode_identity(client):
    c = catalog(client)
    assert len(c['locations']) == 4
    assert '688' not in c['locations']
    assert all(not any(k.startswith('688_') for k in p) for p in c['products'])
    assert not any(h.startswith('688_') for h in rows(client.get('/exports/products.csv?scope=all'))[0])
    # Original archived quantities remain in the database.
    with module.connection() as db:
        assert '688_Quantity' in json.loads(db.execute('SELECT fields FROM products LIMIT 1').fetchone()[0])
    result = client.post('/api/products', json=dict(name='Multi barcode', sku='MULTI', barcode=' 0012345,0098765 ', quantity='3'))
    assert result.status_code == 201
    pid = result.json['id']
    assert next(p for p in catalog(client)['products'] if p['id'] == pid)['Barcode'] == '0012345,0098765'
    assert client.post('/api/products', json=dict(name='Duplicate barcode', sku='OTHER', barcode='0098765')).status_code == 400
    assert client.post('/api/documents', json=dict(kind='transfer', target='688', requested_by='Tester', items=[dict(product_id=pid, quantity='1')])).status_code == 400
    assert stock(client, pid, module.WAREHOUSE) == 3


def test_deferred_catalog_and_on_demand_history_preserve_records(client):
    pid = add_product(client)
    did = order(client, 'purchase', [dict(product_id=pid, quantity='2', cost='3')])
    full = catalog(client)
    deferred = client.get('/api/catalog?defer=1').json
    assert deferred['products'] == full['products']
    assert deferred['documents'] == [] and deferred['movements'] == []
    assert deferred['summary']['openRequests'] == 1
    assert client.get('/api/local-history/documents').json['documents'] == full['documents']
    assert client.get('/api/local-history/movements').json['movements'] == full['movements']
    assert full['documents'][0]['id'] == did
    assert client.get('/api/local-history/invalid').status_code == 400
