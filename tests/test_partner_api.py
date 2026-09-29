import copy
import json
import uuid
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

import pytest
import app as module
from storehub import StoreHubClient, RemoteError
from test_workflow import client, catalog


CUSTOMER = '11111111-1111-4111-8111-111111111111'
SALE = '22222222-2222-4222-8222-222222222222'


class PartnerRemote:
    def __init__(self):
        self.calls = []
        self.error = None
        self.customers = {CUSTOMER: dict(refId=CUSTOMER, firstName='Ana', lastName='Test',
                                       email='test@example.invalid', phone='0123', address1='Existing address',
                                       tags=['existing'])}
        self.products = [dict(id='p1', name='Product', unitPrice=2.5, priceType='Fixed', trackStockLevel=True),
                         dict(id='p2', name='Variable product', priceType='Variable', trackStockLevel=True)]
        self.stores = [dict(id='s1', name=module.WAREHOUSE), dict(id='s2', name='The Daily Centro - Bantayan'), dict(id='removed', name='688')]
        self.sales = [dict(refId=SALE, invoiceNumber='INV1', storeId='s1', transactionType='Sale',
                          transactionTime='2026-09-01T00:00:00+00:00', isCancelled=False, items=[])]
        self.item = dict(id='item1', productId='p1', unitPrice=2.5, taxCode='', minQuantity=0, maxQuantity=10)
        self.book = dict(id='book1', name='Book', appliedStores=['s1'])

    def call(self, path, method='GET', body=None):
        self.calls.append((path, method, copy.deepcopy(body)))
        p = urlsplit(path).path
        if method != 'GET':
            if self.error:
                raise self.error
            if method == 'DELETE':
                return None
            if p == '/customers':
                self.customers[body['refId']] = dict(body)
                return dict(body)
            if p.startswith('/customers/'):
                ref = p.split('/')[-1]
                self.customers[ref] = dict(body, refId=ref)
                return self.customers[ref]
            if p == '/transactions':
                self.sales.append(dict(body))
                return dict(body)
            if p.endswith('/cancel'):
                return dict(self.sales[0], isCancelled=True, **body)
            if p == '/priceBooks':
                return dict(body, id='newbook')
            if '/items' in p:
                return dict(body, id='item1')
            raise AssertionError(path)
        if p == '/stores':
            return self.stores
        if p == '/products':
            return self.products
        if p.startswith('/products/'):
            return next(x for x in self.products if x['id'] == p.split('/')[-1])
        if p == '/customers':
            return list(self.customers.values())
        if p.startswith('/customers/'):
            return self.customers[p.split('/')[-1]]
        if p == '/transactions':
            return self.sales
        if p == '/employees':
            return [dict(id='e1', firstName='Employee', lastName='Test')]
        if p == '/timesheets':
            return [dict(employeeId='e1', storeId='s1', clockInTime='2026-09-01T00:00:00Z'),
                    dict(employeeId='e2', storeId='removed')]
        if p.startswith('/inventory/'):
            return [dict(productId='p1', quantityOnHand=4, warningStock=2, idealStock=10)]
        if p.endswith('/items'):
            return [self.item]
        if '/items/' in p:
            return self.item
        raise AssertionError(path)


@pytest.fixture
def partner(client):
    remote = PartnerRemote()
    module.app.config['STOREHUB_CLIENT_FACTORY'] = lambda: remote
    yield remote
    module.app.config.pop('STOREHUB_CLIENT_FACTORY', None)


def preview(client, operation, payload, **refs):
    return client.post('/api/storehub/partner/preview', json=dict(operation=operation, payload=payload, **refs))


def commit(client, draft):
    assert draft.status_code == 200, draft.json
    return client.post('/api/storehub/partner/commit', json=dict(token=draft.json['token'], confirmed=True))


@pytest.mark.parametrize('resource,filters,path,count', [
    ('products', {}, '/products', 2), ('product', {'id':'p1'}, '/products/p1', 1),
    ('customers', {}, '/customers', 1), ('customer', {'refId':CUSTOMER}, '/customers/'+CUSTOMER, 1),
    ('customers', {'email':'test@example.invalid','firstName':'Ana'}, '/customers', 1),
    ('employees', {'modifiedSince':'2026-09-01'}, '/employees', 1),
    ('stores', {}, '/stores', 2), ('inventory', {'storeId':'s1'}, '/inventory/s1', 1),
    ('timesheets', {'storeId':'s1','employeeId':'e1'}, '/timesheets', 1),
    ('transactions', {'storeId':'s1','includeOnline':True}, '/transactions', 1),
    ('priceitems', {'bookId':'book1'}, '/priceBooks/book1/items', 1),
    ('priceitem', {'bookId':'book1','itemId':'item1'}, '/priceBooks/book1/items/item1', 1),
])
def test_documented_reads(client, partner, resource, filters, path, count):
    result=client.post('/api/storehub/partner/read',json=dict(resource=resource,filters=filters))
    assert result.status_code==200, result.json
    assert result.json['count']==count
    assert any(urlsplit(p).path==path and method=='GET' for p,method,_ in partner.calls)


def test_customer_update_merges_optional_fields_and_detects_changed_record(client, partner):
    draft=preview(client,'customer.update',{'firstName':'Ann','lastName':'Test'},refId=CUSTOMER)
    assert draft.json['payload']['email']=='test@example.invalid'
    assert draft.json['payload']['address1']=='Existing address'
    assert not any(m!='GET' for _,m,_ in partner.calls)
    partner.customers[CUSTOMER]['phone']='changed'
    assert commit(client,draft).status_code==400
    assert not any(m!='GET' for _,m,_ in partner.calls)
    draft=preview(client,'customer.update',{'firstName':'Ann','lastName':'Test','email':''},refId=CUSTOMER)
    assert commit(client,draft).status_code==200
    assert partner.customers[CUSTOMER]['email']==''
    assert partner.customers[CUSTOMER]['phone']=='changed'
    assert partner.calls[-1][1]=='PUT'


def sale_payload():
    return dict(refId=str(uuid.uuid4()),invoiceNumber='TEST-API',storeId='s1',transactionType='Sale',
                transactionTime='2026-09-01T00:00:00Z',paymentMethod='Cash',subTotal=5,total=5.5,
                tax=1,discount=.5,items=[dict(productId='p1',quantity=2,unitPrice=2.5,subTotal=5,total=5.5,tax=1,discount=.5)])


@pytest.mark.parametrize('operation,payload,refs,method,path', [
    ('customer.create', dict(refId=str(uuid.uuid4()),firstName='New',lastName='Customer',birthday='2000-01-01'), {}, 'POST','/customers'),
    ('pricebook.create', dict(name='Book',appliedStores=['s1']), {}, 'POST','/priceBooks'),
    ('priceitem.create', dict(productId='p1',unitPrice='2.345',minQuantity=0,maxQuantity=10), {'bookId':'book1'}, 'POST','/priceBooks/book1/items'),
    ('priceitem.update', dict(productId='p1',unitPrice=3), {'bookId':'book1','itemId':'item1'}, 'POST','/priceBooks/book1/items/item1'),
    ('priceitem.remove', dict(productId='p1'), {'bookId':'book1','itemId':'item1'}, 'DELETE','/priceBooks/book1/items/item1'),
])
def test_reviewed_writes_are_single_use_and_leave_warehouse_unchanged(client, partner, operation, payload, refs, method, path):
    before=catalog(client)
    draft=preview(client,operation,payload,**refs)
    assert not any(m!='GET' for _,m,_ in partner.calls)
    result=commit(client,draft)
    assert result.status_code==200,result.json
    sent=[r for r in partner.calls if r[1]!='GET']
    assert len(sent)==1 and sent[0][1]==method and urlsplit(sent[0][0]).path==path
    if operation=='priceitem.create':assert sent[0][2]['unitPrice']==2.35
    if method=='DELETE':assert parse_qs(urlsplit(sent[0][0]).query)=={'productId':['p1']}
    assert commit(client,draft).json['repeated'] is True
    assert len([r for r in partner.calls if r[1]!='GET'])==1
    after=catalog(client)
    assert before['products']==after['products'] and before['movements']==after['movements'] and before['documents']==after['documents']


def test_transaction_create_and_cancel(client,partner):
    before=catalog(client)
    draft=preview(client,'transaction.create',sale_payload())
    assert commit(client,draft).status_code==200
    result=client.post('/api/storehub/partner/read',json=dict(resource='transactions',filters={}))
    assert result.status_code==200
    draft=preview(client,'transaction.cancel',{'cancelledTime':'2026-09-01T01:00:00Z'},refId=SALE)
    assert commit(client,draft).status_code==200
    assert partner.calls[-1][0]=='/transactions/'+SALE+'/cancel'
    after=catalog(client)
    assert before['movements']==after['movements'] and before['products']==after['products']


def test_cannot_cancel_return_or_previously_cancelled_sale(client,partner):
    client.post('/api/storehub/partner/read',json=dict(resource='transactions',filters={}))
    for changes in [dict(transactionType='Return'),dict(transactionType='Sale',isCancelled=True)]:
        partner.sales[0].update(changes)
        assert preview(client,'transaction.cancel',{'cancelledTime':'2026-09-01T01:00:00Z'},refId=SALE).status_code==400
    assert not any(m!='GET' for _,m,_ in partner.calls)


@pytest.mark.parametrize('changes', [dict(total=99),dict(discount=99),dict(storeId='removed'),dict(paymentMethod='Other'),dict(refId='bad'),dict(transactionTime='2026-09-01')])
def test_invalid_transactions_never_send(client,partner,changes):
    body=dict(sale_payload(),**changes)
    assert preview(client,'transaction.create',body).status_code==400
    assert not any(m!='GET' for _,m,_ in partner.calls)


def test_pricebooks_never_apply_to_removed_or_all_stores(client,partner):
    for stores in [[],['removed'],['s1','removed']]:
        assert preview(client,'pricebook.create',dict(name='Book',appliedStores=stores)).status_code==400
    assert client.post('/api/storehub/partner/read',json=dict(resource='pricebooks',filters={})).status_code==400


def test_opened_pricebook_ids_are_saved_without_overwriting_created_book_details(client, partner):
    assert client.post('/api/storehub/partner/read', json=dict(resource='priceitems', filters={'bookId': 'book1'})).status_code == 200
    books = client.get('/api/storehub/partner/context').json['books']
    assert books == [dict(id='book1', name='', source='opened')]
    assert all(method == 'GET' for _, method, _ in partner.calls)
    with module.connection() as db:
        db.execute('UPDATE sh_partner_books SET payload=? WHERE id=?',
                   (json.dumps(dict(id='book1', name='Branch specials', appliedStores=['s1'])), 'book1'))
    assert client.post('/api/storehub/partner/read', json=dict(resource='priceitems', filters={'bookId': 'book1'})).status_code == 200
    assert client.get('/api/storehub/partner/context').json['books'][0]['name'] == 'Branch specials'


def test_failed_pricebook_read_does_not_save_id(client, partner, monkeypatch):
    def failed(*args, **kwargs):
        raise RemoteError('Book not found')
    monkeypatch.setattr(partner, 'call', failed)
    assert client.post('/api/storehub/partner/read', json=dict(resource='priceitems', filters={'bookId': 'missing'})).status_code == 400
    assert client.get('/api/storehub/partner/context').json['books'] == []


def test_default_pricebook_reads_all_product_prices_without_inventory_changes(client, partner):
    before = catalog(client)
    partner.products = [dict(id='p'+str(i), name='Product '+str(i), unitPrice=i / 10,
                             priceType='Fixed') for i in range(1371)]
    response = client.post('/api/storehub/partner/read', json=dict(resource='priceitems', filters={'bookId': 'default'}))
    assert response.status_code == 200
    assert response.json['count'] == 1371
    assert response.json['records'] == partner.products
    assert 'tax-exclusive' in response.json['note']
    assert partner.calls == [('/products', 'GET', None)]
    assert client.get('/api/storehub/partner/context').json['books'] == []
    after = catalog(client)
    assert before['products'] == after['products'] and before['movements'] == after['movements']


@pytest.mark.parametrize('operation', ['priceitem.create', 'priceitem.update', 'priceitem.remove'])
def test_default_prices_cannot_be_treated_as_custom_rules(client, partner, operation):
    response = preview(client, operation, {'productId': 'p1', 'unitPrice': 5}, bookId='default', itemId='p1')
    assert response.status_code == 400
    assert 'Default prices' in response.json['error']
    assert partner.calls == []


def test_people_directory_returns_names_only_and_caches_remote_reads(client, partner):
    result = client.get('/api/storehub/partner/people')
    assert result.status_code == 200
    assert result.json['employees'] == [dict(id='e1', name='Employee Test')]
    assert result.json['customers'] == [dict(id=CUSTOMER, name='Ana Test')]
    assert 'test@example.invalid' not in result.get_data(as_text=True)
    assert partner.calls == [('/employees', 'GET', None), ('/customers', 'GET', None)]
    assert client.get('/api/storehub/partner/people').json == result.json
    assert len(partner.calls) == 2


def test_people_directory_keeps_cached_names_when_remote_service_fails(client, partner, monkeypatch):
    client.get('/api/storehub/partner/people')
    with module.connection() as db:
        for kind in ('employees', 'customers'):
            row = db.execute('SELECT value FROM sh_settings WHERE key=?', ('people_'+kind,)).fetchone()
            value = json.loads(row[0]); value['refreshed'] = '2020-01-01T00:00:00+00:00'
            module.integration.save(db, 'people_'+kind, json.dumps(value))
    def unavailable(*args, **kwargs):
        raise RemoteError('Service unavailable')
    monkeypatch.setattr(partner, 'call', unavailable)
    result = client.get('/api/storehub/partner/people').json
    assert result['employees'][0]['name'] == 'Employee Test'
    assert result['customers'][0]['name'] == 'Ana Test'
    assert len(result['warnings']) == 2


def test_uncertain_outcome_blocks_resend_and_duplicate_draft(client,partner):
    body=dict(name='Book',appliedStores=['s1'])
    draft=preview(client,'pricebook.create',body)
    partner.error=RemoteError('Timeout',True)
    assert commit(client,draft).status_code==400
    partner.error=None
    assert commit(client,draft).status_code==400
    assert preview(client,'pricebook.create',body).status_code==400
    history=client.get('/api/storehub/partner/history').json['changes']
    assert history[0]['status']=='uncertain'
    assert len([r for r in partner.calls if r[1]!='GET'])==1


def test_preview_requires_confirmation_same_origin_and_expiry(client,partner):
    body=dict(refId=str(uuid.uuid4()),firstName='New',lastName='Customer')
    assert client.post('/api/storehub/partner/preview',json=dict(operation='customer.create',payload=body),headers={'Origin':'http://untrusted.invalid'}).status_code==400
    draft=preview(client,'customer.create',body)
    assert client.post('/api/storehub/partner/commit',json=dict(token=draft.json['token'])).status_code==400
    with module.connection() as db:
        db.execute("UPDATE sh_partner_drafts SET created='2000-01-01T00:00:00+00:00'")
    assert commit(client,draft).status_code==400
    assert not any(m!='GET' for _,m,_ in partner.calls)


def test_transaction_cap_splits_ranges_and_filters_removed_store(client,partner):
    original=partner.call
    first=True
    def capped(path,method='GET',body=None):
        nonlocal first
        if path.startswith('/transactions?') and first:
            first=False
            return [dict(partner.sales[0],refId='ref'+str(i)) for i in range(5000)]
        return original(path,method,body)
    partner.call=capped
    partner.sales.append(dict(partner.sales[0],refId='removed-sale',storeId='removed'))
    result=client.post('/api/storehub/partner/read',json=dict(resource='transactions',filters={'from':'2026-09-01','to':'2026-09-02'}))
    assert result.status_code==200,result.json
    assert result.json['count']==1
    assert len([p for p,m,_ in partner.calls if p.startswith('/transactions?')])==2


def test_transport_accepts_documented_delete_204():
    class Response:
        status=204
        def __enter__(self):return self
        def __exit__(self,*args):pass
    with patch('storehub.build_opener') as opener,patch('storehub.time.sleep'):
        opener.return_value.open.return_value=Response()
        assert StoreHubClient('test','test').call('/priceBooks/book/items/item?productId=p1','DELETE') is None


def test_price_rule_update_preserves_defaults_and_rechecks_snapshot(client,partner):
    draft=preview(client,'priceitem.update',dict(productId='p1',unitPrice=3),bookId='book1',itemId='item1')
    assert draft.json['payload']['maxQuantity']==10
    assert draft.json['payload']['taxCode']==''
    partner.item['unitPrice']=9
    assert commit(client,draft).status_code==400
    assert not any(m!='GET' for _,m,_ in partner.calls)


def test_wrong_write_response_reference_is_uncertain(client,partner):
    original=partner.call
    def wrong(path,method='GET',body=None):
        result=original(path,method,body)
        return dict(result,refId='wrong-ref') if method=='POST' and path=='/customers' else result
    partner.call=wrong
    draft=preview(client,'customer.create',dict(refId=str(uuid.uuid4()),firstName='New',lastName='Customer'))
    assert commit(client,draft).status_code==400
    assert client.get('/api/storehub/partner/history').json['changes'][0]['status']=='uncertain'
    assert commit(client,draft).status_code==400
    assert len([r for r in partner.calls if r[1]!='GET'])==1


def test_two_previews_of_same_write_cannot_send_twice(client,partner):
    body=dict(name='Same book',appliedStores=['s1'])
    first=preview(client,'pricebook.create',body)
    second=preview(client,'pricebook.create',body)
    assert commit(client,first).status_code==200
    assert commit(client,second).status_code==400
    assert len([r for r in partner.calls if r[1]!='GET'])==1


def test_new_return_uses_separate_reference_and_transaction(client,partner):
    body=dict(sale_payload(),transactionType='Return',saleInvoiceNumber='INV1',returnReason='Damaged item')
    draft=preview(client,'transaction.create',body)
    assert commit(client,draft).status_code==200
    assert partner.calls[-1][2]['transactionType']=='Return'
    assert partner.calls[-1][2]['saleInvoiceNumber']=='INV1'
    assert partner.calls[-1][2]['refId']!=SALE
