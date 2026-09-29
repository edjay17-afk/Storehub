import json
from datetime import datetime, timezone
from decimal import Decimal
import pytest
import app as module
from storehub import RemoteError
from test_workflow import client, catalog, stock, order, act


class FakeClient:
    def __init__(self, client):
        c = catalog(client)
        p = c['products'][0]
        self.pid = p['id']
        self.stores = [dict(id='s'+str(i), name=l) for i,l in enumerate(c['locations'])] + [dict(id='excluded', name='688')]
        self.products = [dict(id=self.pid, name=p['Product Name'], sku=p['SKU'], barcode=p['Barcode'], cost=4, priceType='Fixed', unitPrice=999, trackStockLevel=True)]
        self.inventory = {s['id']: [dict(productId=self.pid, quantityOnHand=10 if s['name']==module.WAREHOUSE else 1, warningStock=2, idealStock=9)] for s in self.stores}
        self.sales=[]; self.orders=[]; self.calls=[]; self.fail=None; self.post_error=None
    def call(self, path, method='GET', body=None):
        self.calls.append((path,method,body))
        if path==self.fail:
            raise RemoteError('Simulated unavailable service')
        if method=='POST':
            if self.post_error: raise self.post_error
            r=dict(body,id='created-remote'); self.products.append(r)
            return r
        if path=='/stores': return self.stores
        if path=='/products': return self.products
        if path.startswith('/inventory/'): return self.inventory[path.split('/')[-1]]
        if path.startswith('/transactions?'): return self.sales
        if path.startswith('/purchaseOrders?'): return self.orders
        if path.startswith('/purchaseOrders/'):
            return next(o for o in self.orders if str(o['purchaseOrderId']) == path.rsplit('/',1)[1])
        raise AssertionError(path)


@pytest.fixture
def remote(client):
    fake=FakeClient(client)
    module.app.config['STOREHUB_CLIENT_FACTORY']=lambda:fake
    yield fake
    module.app.config.pop('STOREHUB_CLIENT_FACTORY',None)


def sync(client):
    r=client.post('/api/storehub/sync',json={})
    assert r.status_code==200,r.json
    return r.json


def draft(client,sku='LOCAL-SKU',barcode='0012345'):
    r=client.post('/api/products',json=dict(name='Local product',sku=sku,barcode=barcode,quantity='20',cost='2',price='112'))
    assert r.status_code==201,r.json
    return r.json['id']


def test_sync_preserves_stock_csv_price_and_excludes_688(client,remote):
    before=next(p for p in catalog(client)['products'] if p['id']==remote.pid)
    state=sync(client)
    after=next(p for p in catalog(client)['products'] if p['id']==remote.pid)
    assert after['Tax-Inclusive Price']==before['Tax-Inclusive Price']
    assert all(after[l+'_Quantity']==before[l+'_Quantity'] for l in state['locations'])
    assert len(state['mapping'])==4
    assert 'excluded' not in state['mapping'].values()
    assert not any('/inventory/excluded'==c[0] for c in remote.calls)
    assert all(c[1]=='GET' for c in remote.calls)
    assert state['stocks'][remote.pid][module.WAREHOUSE]['quantity']=='10'
    assert 'authorization' not in str(state).lower()


def test_only_daily_centro_stores_are_available_even_before_resync(client, remote):
    sync(client)
    with module.connection() as db:
        db.executemany('INSERT INTO sh_stores VALUES (?,?)', [
            ('digital', 'Digital Store'), ('test', 'Test'), ('noodles', 'Korean Noodles')])
    state = client.get('/api/storehub').json
    assert len(state['stores']) == 4
    assert all(module.integration.allowed_store(s['name']) for s in state['stores'])
    context = client.get('/api/storehub/partner/context').json
    assert {s['id'] for s in context['stores']} == {s['id'] for s in state['stores']}
    response = client.post('/api/storehub/locations', json={'mapping': {module.WAREHOUSE: 'digital'}})
    assert response.status_code == 400
    remote.stores += [dict(id='digital', name='Digital Store'), dict(id='test', name='Test')]
    remote.calls.clear()
    assert len(sync(client)['stores']) == 4
    assert not any(c[0] in ('/inventory/digital', '/inventory/test') for c in remote.calls)


def test_sync_keeps_changed_product_details_and_quantities(client,remote):
    assert client.post('/api/receive',json=dict(product_id=remote.pid,mode='count',quantity='33',reason='Count')).status_code==200
    remote.products[0]['name']='Remote rename'
    before=next(p for p in catalog(client)['products'] if p['id']==remote.pid)
    sync(client)
    after=next(p for p in catalog(client)['products'] if p['id']==remote.pid)
    assert after==before


def test_failed_inventory_sync_keeps_previous_snapshot(client,remote):
    first=sync(client)
    remote.inventory['s0'][0]['quantityOnHand']=100
    remote.fail='/inventory/s1'
    r=client.post('/api/storehub/sync',json={})
    assert r.status_code==400
    state=client.get('/api/storehub').json
    assert state['stocks']==first['stocks'] and state['lastSync']==first['lastSync']


def test_new_import_requires_explicit_baseline_and_complete_csv(client,remote):
    r=dict(id='remote-new',name='New API product',sku='NEW-API',barcode='00009999',trackStockLevel=True,cost=1,unitPrice=5)
    remote.products.append(r)
    for entries in remote.inventory.values(): entries.append(dict(productId=r['id'],quantityOnHand=7))
    state=sync(client)
    assert state['baselinePending']==1
    assert r['id'] in catalog(client)['storehub']['pendingIds']
    assert client.post('/api/receive',json=dict(product_id=r['id'],mode='add',quantity=1,reason='delivery')).status_code==400
    assert client.post('/api/storehub/baseline',json={}).json['initialized']==1
    assert stock(client,r['id'],module.WAREHOUSE)==7
    assert client.post('/api/storehub/baseline',json={}).json['initialized']==0
    export=client.get('/exports/products.csv?scope=all')
    assert export.status_code==200 and 'Full_Catalog_Review.csv' in export.headers['Content-Disposition']
    import csv, io
    report=list(csv.DictReader(io.StringIO(export.data.decode('utf-8-sig'))))
    row=next(p for p in report if p['Product Id']==r['id'])
    assert row['Tax-Inclusive Price']=='' and row['CSV Export Status'].startswith('REVIEW ONLY')
    assert row[module.WAREHOUSE+'_Quantity']=='7'
    ready=client.get('/exports/products.csv?scope=ready')
    assert ready.status_code==200 and 'filename="Products.csv"' in ready.headers['Content-Disposition']
    assert r['id'] not in ready.data.decode('utf-8-sig')
    assert all(c[1]=='GET' for c in remote.calls)


def test_publish_requires_preview_price_and_preserves_stock(client,remote):
    pid=draft(client)
    assert client.post('/api/storehub/publish',json=dict(product_id=pid,unit_price=100)).status_code==400
    assert client.post('/api/storehub/publish-preview',json=dict(product_id=pid)).status_code==400
    preview=client.post('/api/storehub/publish-preview',json=dict(product_id=pid,unit_price=100)).json
    assert preview['payload']['unitPrice']==100
    assert not any('quantity' in k.lower() for k in preview['payload'])
    assert preview['payload']['barcode']=='0012345'
    r=client.post('/api/storehub/publish',json=dict(product_id=pid,unit_price=100,confirmed=True))
    assert r.status_code==200,r.json
    assert stock(client,pid,module.WAREHOUSE)==20
    assert client.post('/api/storehub/publish',json=dict(product_id=pid,unit_price=100,confirmed=True)).status_code==400
    assert len([c for c in remote.calls if c[1]=='POST'])==1
    product=next(p for p in catalog(client)['products'] if p['id']==pid)
    assert product['Product Id']=='created-remote' and product['Tax-Inclusive Price']=='112'


def test_uncertain_publish_blocks_retry_then_links_reviewed_match(client,remote):
    pid=draft(client)
    remote.post_error=RemoteError('Timeout; result unknown',True)
    payload=dict(product_id=pid,unit_price=100,confirmed=True)
    assert client.post('/api/storehub/publish',json=payload).status_code==400
    assert client.post('/api/storehub/publish',json=payload).status_code==400
    assert len([c for c in remote.calls if c[1]=='POST'])==1
    remote.products.append(dict(id='previous-write',name='Local product',sku='LOCAL-SKU',barcode='0012345',cost=2,trackStockLevel=True))
    state=sync(client)
    assert state['unlinked'][0]['id']=='previous-write'
    assert client.post('/api/storehub/link',json=dict(product_id=pid,remote_id='previous-write')).status_code==200
    assert stock(client,pid,module.WAREHOUSE)==20


def test_remote_duplicate_barcode_blocks_post(client,remote):
    pid=draft(client)
    remote.products.append(dict(id='duplicate',name='Other name',sku='OTHER',barcode='0012345',trackStockLevel=True))
    r=client.post('/api/storehub/publish',json=dict(product_id=pid,unit_price=100,confirmed=True))
    assert r.status_code==400 and 'already exists' in r.json['error']
    assert not any(c[1]=='POST' for c in remote.calls)


def test_restock_reservations_returns_and_shared_warehouse_budget(client,remote):
    assert client.post('/api/receive',json=dict(product_id=remote.pid,mode='count',quantity='10',reason='Count')).status_code==200
    tx=dict(storeId='s1',transactionTime=datetime.now(timezone.utc).isoformat(),transactionType='Sale',items=[dict(productId=remote.pid,quantity=30)])
    remote.sales=[dict(tx,refId='sale'),dict(tx,refId='second-branch',storeId='s2',items=[dict(productId=remote.pid,quantity=40)]),dict(tx,refId='return',transactionType='Return',items=[dict(productId=remote.pid,quantity=2)]),dict(tx,refId='cancelled',isCancelled=True)]
    did=order(client,'transfer',[dict(product_id=remote.pid,quantity=3)])
    state=sync(client)
    suggestions=state['suggestions']
    branch=next(r for r in suggestions if r['location']==state['locations'][1])
    assert branch['sold30Days']=='28' and branch['pending']=='3' and branch['target']=='7'
    assert sum(Decimal(r['suggested']) for r in suggestions)<=7
    assert stock(client,remote.pid,module.WAREHOUSE)==10
    assert act(client,did,'ship').status_code==200
    state=client.get('/api/storehub').json
    assert sum(Decimal(r['suggested']) for r in state['suggestions'])<=7


def test_restock_uses_each_branch_sales_and_current_stock_not_ideal_or_warning(client,remote):
    client.post('/api/receive',json=dict(product_id=remote.pid,mode='count',quantity='100',reason='Count'))
    for items in remote.inventory.values():
        items[0].update(quantityOnHand=2,warningStock=1,idealStock=500)
    remote.inventory['s0'][0]['quantityOnHand']=100
    transaction=dict(transactionTime=datetime.now(timezone.utc).isoformat(),transactionType='Sale',items=[dict(productId=remote.pid,quantity=30)])
    remote.sales=[dict(transaction,refId='branch-one',storeId='s1'),dict(transaction,refId='branch-two',storeId='s2',items=[dict(productId=remote.pid,quantity=60)])]
    state=sync(client)
    first=next(r for r in state['suggestions'] if r['location']==state['locations'][1])
    second=next(r for r in state['suggestions'] if r['location']==state['locations'][2])
    assert first['target']=='7' and first['needed']=='5' and first['averageDailySales']=='1.00'
    assert second['target']=='14' and second['needed']=='12'
    assert first['basis']=='7 days of sales' and state['salesSync']


def test_invalid_mapping_and_cross_origin_rejected(client,remote):
    sync(client)
    assert client.post('/api/storehub/locations',json=dict(mapping={module.WAREHOUSE:'excluded'})).status_code==400
    assert client.post('/api/storehub/test',json={},headers={'Origin':'https://another-site.example'}).status_code==400
    assert client.post('/api/storehub/schedule',json={'interval':1}).status_code==400


def test_optional_endpoint_failure_is_visible_without_destroying_stock(client,remote):
    # Every transactions request fails without publishing any write.
    original=remote.call
    def call(path,*args):
        if path.startswith('/transactions?'):raise RemoteError('Endpoint not available',False,403)
        return original(path,*args)
    remote.call=call
    state=sync(client)
    assert state['warnings'] and state['stocks']


def test_capped_daily_response_fails_visibly(client,remote):
    class Capped:
        def call(self,*args):return [dict(refId='tx'+str(i)) for i in range(5000)]
    with pytest.raises(ValueError,match='5000'):
        module.integration.dates(Capped(),'/transactions','refId')


def test_malformed_optional_sales_does_not_poison_saved_snapshot(client,remote):
    sync(client)
    remote.sales=[dict(refId='invalid',storeId='s1',items=[dict(productId=remote.pid,quantity='NaN')])]
    state=sync(client)
    assert state['warnings'] and state['salesCount']==0
    assert client.get('/api/storehub').status_code==200


def test_enabled_scheduler_runs_sync_at_fifteen_minutes(client,remote,monkeypatch):
    import storehub
    assert client.post('/api/storehub/schedule',json={'interval':15}).status_code==200
    workers=[]
    class FakeThread:
        def __init__(self,target,**kwargs): workers.append(target)
        def start(self): pass
    monkeypatch.setattr(storehub.threading,'Thread',FakeThread)
    ticks=iter([0,901,901])
    monkeypatch.setattr(storehub.time,'monotonic',lambda:next(ticks))
    sleeps=[]
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps)>1: raise StopIteration
    monkeypatch.setattr(storehub.time,'sleep',sleep)
    module.integration.start_scheduler()
    with pytest.raises(StopIteration): workers[0]()
    status=client.get('/api/storehub').json
    assert status['lastSync'] and status['interval']==15
    assert remote.calls and all(c[1]=='GET' for c in remote.calls)
