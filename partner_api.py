"""Remaining operations in the supplied StoreHub partner reference (v1.7).

Reads are on demand. Remote writes use a durable, single-use reviewed draft;
they never write the warehouse ledger or silently retry an uncertain result.
"""
import hashlib
import json
import re
import uuid
import people
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote, urlencode

from flask import jsonify, request
from storehub import RemoteError, array, decimal, timestamp


CUSTOMER_FIELDS = ('firstName', 'lastName', 'email', 'phone', 'address1', 'address2',
                   'city', 'state', 'postalCode', 'tags')
CREATE_CUSTOMER_FIELDS = (*CUSTOMER_FIELDS, 'refId', 'birthday', 'memberId', 'createdTime')
MUTATIONS = {'customer.create', 'customer.update', 'transaction.create', 'transaction.cancel',
             'pricebook.create', 'priceitem.create', 'priceitem.update', 'priceitem.remove'}


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS sh_partner_drafts(
        token TEXT PRIMARY KEY, operation TEXT NOT NULL, fingerprint TEXT NOT NULL,
        draft TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
        result TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS sh_partner_books(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_partner_transactions(ref_id TEXT PRIMARY KEY,
        payload TEXT NOT NULL, query TEXT NOT NULL);
    ''')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Enter a valid StoreHub reference.')
    return value


def uid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError):
        raise ValueError('Enter a valid UUID reference.') from None


def iso(value, day=False):
    try:
        parsed = date.fromisoformat(str(value)) if day else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if not day and parsed.tzinfo is None:
            raise ValueError
        return parsed.isoformat()
    except (ValueError, TypeError):
        raise ValueError('Enter a valid date' + (' (YYYY-MM-DD).' if day else ' with a timezone.')) from None


def amount(value, positive=False, signed=False):
    if isinstance(value, bool):
        raise ValueError('Enter a valid number.')
    n = decimal(value)
    if (not signed and n < 0) or (positive and n <= 0) or abs(n) > Decimal('1000000000000'):
        raise ValueError('Enter a valid positive quantity or nonnegative amount.')
    return float(n)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def strings(values, required=()):
    result = {}
    for key, value in values.items():
        if key == 'tags':
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or len(v) > 100 for v in value):
                raise ValueError('Tags must be an array of short strings.')
            result[key] = value
        else:
            if not isinstance(value, str) or len(value) > 2000:
                raise ValueError(f'Enter a valid {key}.')
            result[key] = value.strip()
    if any(not result.get(key) for key in required):
        raise ValueError('First name and last name are required.')
    return result


class PartnerAPI:
    def __init__(self, integration):
        self.sh = integration
        for path, handler in [('context', self.context), ('read', self.read),
                              ('preview', self.preview), ('commit', self.commit), ('history', self.history), ('people', self.people)]:
            integration.app.add_url_rule('/api/storehub/partner/' + path, 'partner_' + path,
                                         handler, methods=['GET' if path in ('context', 'history', 'people') else 'POST'])

    def people(self):
        return jsonify(people.refresh(self.sh))

    def context(self):
        with self.sh.db() as db:
            products = [json.loads(r[0]) for r in db.execute('SELECT payload FROM sh_products')]
            stores = [dict(id=r['remote_id'], name=r['name']) for r in db.execute('SELECT * FROM sh_stores')
                      if self.sh.allowed_store(r['name'])]
            books = [json.loads(r[0]) for r in db.execute('SELECT payload FROM sh_partner_books')]
        return jsonify(products=products, stores=stores, books=books,
                       customerRef=str(uuid.uuid4()), transactionRef=str(uuid.uuid4()))

    def active_stores(self, client):
        return {identifier(s['id']): s for s in array(client.call('/stores'))
                if self.sh.allowed_store(s.get('name', ''))}

    def selected_store(self, value, stores, optional=False):
        if not value and optional:
            return ''
        if identifier(value) not in stores:
            raise ValueError('Choose one of the four Daily Centro stores.')
        return value

    def range(self, values):
        today = datetime.now(timezone.utc).date()
        start = date.fromisoformat(iso(values.get('from') or (today - timedelta(days=30)).isoformat(), True))
        end = date.fromisoformat(iso(values.get('to') or today.isoformat(), True))
        if start > end or (end - start).days > 365:
            raise ValueError('Choose a date range of up to one year, with From before To.')
        return start, end

    def transactions(self, client, values, stores):
        start, end = self.range(values)
        selected = self.selected_store(values.get('storeId'), stores, True)
        params = {k: values[k] for k in ('includeOnline', 'onlineOnly') if k in values}
        if any(not isinstance(v, bool) for v in params.values()):
            raise ValueError('Online filters must be true or false.')
        params = {k: str(v).lower() for k, v in params.items()}
        if selected:
            params['storeId'] = selected
        # Exact UTC instants avoid account-timezone overlaps when splitting capped ranges.
        pending = [(datetime.combine(start, datetime.min.time(), timezone.utc),
                    datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc))]
        result = {}
        calls = 0
        while pending:
            left, right = pending.pop()
            calls += 1
            if calls > 128:
                raise ValueError('Too many records. Choose a shorter transaction date range.')
            query = dict(params, **{'from': left.isoformat(), 'to': (right - timedelta(microseconds=1)).isoformat()})
            rows = array(client.call('/transactions?' + urlencode(query)))
            if len(rows) >= 5000:
                if (right - left).total_seconds() <= 1:
                    raise ValueError('StoreHub reached its 5,000-record limit in one second. Results are incomplete; narrow the range or contact StoreHub.')
                middle = left + (right - left) / 2
                pending.extend([(left, middle), (middle, right)])
                continue
            for row in rows:
                if row.get('storeId') in stores and (not selected or row.get('storeId') == selected):
                    result[identifier(row.get('refId'))] = row
        return list(result.values())

    def read(self):
        body = self.sh.body()
        resource, values = body.get('resource'), body.get('filters', {})
        if not isinstance(values, dict):
            raise ValueError('Enter valid filters.')
        client = self.sh.client()
        note = ''
        if resource == 'customers':
            params = strings({k: values[k] for k in ('firstName', 'lastName', 'email', 'phone') if values.get(k)})
            rows = array(client.call('/customers' + ('?' + urlencode(params) if params else '')))
            if params and len(rows) >= 100:
                note = 'StoreHub limits customer search to 100 matches. Narrow your search to see other customers.'
        elif resource in ('customer', 'product'):
            key = 'refId' if resource == 'customer' else 'id'
            rows = [client.call(('/customers/' if resource == 'customer' else '/products/') + quote(identifier(values.get(key)), safe=''))]
        elif resource == 'products':
            rows = array(client.call('/products'))
        elif resource == 'employees':
            params = {'modifiedSince': iso(values['modifiedSince'], True)} if values.get('modifiedSince') else {}
            rows = array(client.call('/employees' + ('?' + urlencode(params) if params else '')))
        elif resource == 'stores':
            rows = list(self.active_stores(client).values())
        elif resource == 'inventory':
            stores = self.active_stores(client)
            sid = self.selected_store(values.get('storeId'), stores)
            rows = array(client.call('/inventory/' + quote(sid, safe='')))
        elif resource == 'timesheets':
            stores = self.active_stores(client)
            start, end = self.range(values)
            sid = self.selected_store(values.get('storeId'), stores, True)
            params = {'from': start.isoformat(), 'to': (end + timedelta(days=1)).isoformat()}
            if sid:
                params['storeId'] = sid
            if values.get('employeeId'):
                params['employeeId'] = identifier(values['employeeId'])
            rows = [r for r in array(client.call('/timesheets?' + urlencode(params)))
                    if r.get('storeId') in stores and (not sid or r.get('storeId') == sid)]
        elif resource == 'transactions':
            rows = self.transactions(client, values, self.active_stores(client))
            with self.sh.db() as db:
                db.executemany('INSERT OR REPLACE INTO sh_partner_transactions VALUES (?,?,?)',
                               [(r['refId'], json.dumps(r), json.dumps(values)) for r in rows])
        elif resource in ('priceitems', 'priceitem'):
            bid = identifier(values.get('bookId'))
            if bid == 'default':
                if resource == 'priceitem':
                    raise ValueError('Default prices are product prices, not custom pricing rules. Open the Default price list.')
                rows = array(client.call('/products'))
                note = 'Current default product prices from StoreHub. Prices are tax-exclusive; the API does not provide tax-inclusive selling prices.'
            else:
                path = '/priceBooks/' + quote(bid, safe='') + '/items'
                if resource == 'priceitem':
                    path += '/' + quote(identifier(values.get('itemId')), safe='')
                value = client.call(path)
                rows = array(value) if resource == 'priceitems' else [value]
                array(rows)
                with self.sh.db() as db:
                    # Remember a successfully opened ID; the PDF has no book-list endpoint.
                    db.execute('INSERT OR IGNORE INTO sh_partner_books VALUES (?,?)',
                               (bid, json.dumps(dict(id=bid, name='', source='opened'))))
        else:
            raise ValueError('Choose a documented StoreHub resource.')
        array(rows)
        return jsonify(records=rows, count=len(rows), note=note, readAt=timestamp())

    def customer(self, body, create, client, ref):
        allowed = CREATE_CUSTOMER_FIELDS if create else (*CUSTOMER_FIELDS, 'modifiedTime')
        if any(k not in allowed for k in body):
            raise ValueError('This customer field is not documented for this operation.')
        before = None if create else client.call('/customers/' + quote(identifier(ref), safe=''))
        if before is not None and not isinstance(before, dict):
            raise ValueError('StoreHub returned an invalid customer.')
        merged = {} if create else {k: before[k] for k in CUSTOMER_FIELDS if k in before}
        merged.update(body)
        result = strings(merged, ('firstName', 'lastName'))
        if create:
            result['refId'] = uid(result.get('refId'))
        for field in ('createdTime', 'modifiedTime'):
            if result.get(field):
                result[field] = iso(result[field])
        if result.get('birthday'):
            result['birthday'] = iso(result['birthday'], True)
        return result, before

    def priceitem(self, body, client):
        if set(body) - {'productId', 'taxCode', 'unitPrice', 'minQuantity', 'maxQuantity'}:
            raise ValueError('This price-book field is not documented.')
        result = {'productId': identifier(body.get('productId'))}
        product = client.call('/products/' + quote(result['productId'], safe=''))
        if not isinstance(product, dict) or product.get('id') != result['productId']:
            raise ValueError('Choose a valid StoreHub product.')
        if 'taxCode' in body:
            result.update(strings({'taxCode': body['taxCode']}))
        for key in ('unitPrice', 'minQuantity', 'maxQuantity'):
            if key in body:
                result[key] = amount(body[key])
        if 'unitPrice' in result:
            result['unitPrice'] = float(decimal(body['unitPrice']).quantize(Decimal('.01'), rounding='ROUND_HALF_UP'))
        if 'minQuantity' in result and 'maxQuantity' in result and result['maxQuantity'] <= result['minQuantity']:
            raise ValueError('Maximum quantity must be greater than minimum quantity; leave it blank for no limit.')
        return result

    def transaction(self, body, client, stores):
        required = {'refId', 'invoiceNumber', 'storeId', 'transactionType', 'transactionTime',
                    'paymentMethod', 'total', 'subTotal', 'discount', 'items'}
        optional = {'employeeId', 'customerRefId', 'tax', 'roundedAmount', 'comment',
                    'returnReason', 'saleInvoiceNumber', 'tableId'}
        if required - set(body) or set(body) - required - optional:
            raise ValueError('Provide all documented transaction fields; unsupported fields are not accepted.')
        result = dict(body)
        result['refId'] = uid(body['refId'])
        result['storeId'] = self.selected_store(body['storeId'], stores)
        result['transactionTime'] = iso(body['transactionTime'])
        if body['transactionType'] not in ('Sale', 'Return') or body['paymentMethod'] not in ('Cash', 'CreditCard'):
            raise ValueError('Choose Sale or Return, and Cash or CreditCard.')
        for field in ('invoiceNumber', 'comment', 'returnReason', 'saleInvoiceNumber', 'tableId'):
            if field in body:
                result.update(strings({field: body[field]}))
        if not result['invoiceNumber']:
            raise ValueError('An invoice number is required.')
        if body.get('employeeId'):
            result['employeeId'] = identifier(body['employeeId'])
        if body.get('customerRefId'):
            result['customerRefId'] = uid(body['customerRefId'])
        items = array(body['items'])
        if not 1 <= len(items) <= 200:
            raise ValueError('Add between 1 and 200 transaction lines.')
        for item in items:
            if {'productId', 'quantity', 'total', 'subTotal', 'discount'} - set(item) or set(item) - {'productId', 'quantity', 'total', 'subTotal', 'discount', 'tax', 'unitPrice'}:
                raise ValueError('Provide documented transaction line fields.')
            item['productId'] = identifier(item['productId'])
            item['quantity'] = amount(item['quantity'], positive=True)
            for key in ('subTotal', 'total', 'discount', 'tax', 'unitPrice'):
                if key in item:
                    item[key] = amount(item[key])
            expected = decimal(item['subTotal']) + decimal(item.get('tax', 0)) - decimal(item['discount'])
            if abs(expected - decimal(item['total'])) > Decimal('.005'):
                raise ValueError('Each line total must equal subtotal + tax - discount.')
        products = {p['id']: p for p in array(client.call('/products'))}
        if any(i['productId'] not in products for i in items):
            raise ValueError('A transaction line references an unavailable StoreHub product.')
        if any(products[i['productId']].get('priceType') == 'Variable' and 'unitPrice' not in i for i in items):
            raise ValueError('Variable-price products require a unit price.')
        for key in ('subTotal', 'total', 'discount', 'tax', 'roundedAmount'):
            if key in body:
                result[key] = amount(body[key], signed=key == 'roundedAmount')
        for key in ('subTotal', 'discount', 'tax'):
            if abs(sum((decimal(i.get(key, 0)) for i in items), Decimal(0)) - decimal(result.get(key, 0))) > Decimal('.005'):
                raise ValueError(f'Transaction {key} must match the sum of its lines.')
        expected = decimal(result['subTotal']) + decimal(result.get('tax', 0)) - decimal(result['discount']) + decimal(result.get('roundedAmount', 0))
        if abs(expected - decimal(result['total'])) > Decimal('.005'):
            raise ValueError('Transaction total must equal subtotal + tax - discount + rounding.')
        result['items'] = items
        return result

    def current_transaction(self, client, ref, stores):
        with self.sh.db() as db:
            row = db.execute('SELECT query FROM sh_partner_transactions WHERE ref_id=?', (ref,)).fetchone()
        if not row:
            raise ValueError('Load this sale in Transactions before cancelling it.')
        matches = [r for r in self.transactions(client, json.loads(row[0]), stores) if r.get('refId') == ref]
        if len(matches) != 1:
            raise ValueError('The sale is no longer in this query. Reload Transactions before cancelling.')
        sale = matches[0]
        if sale.get('transactionType') != 'Sale' or sale.get('isCancelled'):
            raise ValueError('Only an active Sale can be cancelled.')
        return sale

    def build(self, operation, values, client):
        payload = values.get('payload', {})
        if not isinstance(payload, dict):
            raise ValueError('Enter a valid request object.')
        before = None
        stores = None
        ref = values.get('refId')
        notes = []
        if operation.startswith('customer.'):
            creating = operation == 'customer.create'
            payload, before = self.customer(payload, creating, client, ref)
            path, method = ('/customers', 'POST') if creating else ('/customers/' + quote(identifier(ref), safe=''), 'PUT')
            notes.append('Customer updates replace the record. Existing supported optional fields are included unless you explicitly clear them.')
            if before and any(before.get(k) for k in ('birthday', 'memberId')):
                notes.append('The PDF does not list birthday or memberId in Update Customer. These fields are not sent in this update; inspect their values below and verify StoreHub behavior before confirming.')
        elif operation == 'transaction.create':
            stores = self.active_stores(client)
            payload = self.transaction(payload, client, stores)
            path, method = '/transactions', 'POST'
            notes.append('This records a real sale or return in StoreHub and can affect its stock and reports. It does not charge a card or change local warehouse balances. Amounts must be tax-exclusive; discounts are absolute amounts.')
            if payload['transactionType'] == 'Return':
                notes.append('StoreHub does not validate previous returns. Check the original invoice and already returned quantities before confirming.')
        elif operation == 'transaction.cancel':
            stores = self.active_stores(client)
            ref = identifier(ref)
            before = self.current_transaction(client, ref, stores)
            if set(payload) - {'cancelledTime', 'cancelledBy'}:
                raise ValueError('Only cancelledTime and cancelledBy are documented.')
            payload = {'cancelledTime': iso(payload.get('cancelledTime')),
                       **({'cancelledBy': identifier(payload['cancelledBy'])} if payload.get('cancelledBy') else {})}
            path, method = '/transactions/' + quote(ref, safe='') + '/cancel', 'POST'
            notes.append('This cancels a real StoreHub sale and can affect its stock and reports. No local warehouse movement will be created.')
        elif operation == 'pricebook.create':
            stores = self.active_stores(client)
            if set(payload) - {'name', 'appliedStores'}:
                raise ValueError('Only name and appliedStores are documented.')
            applied = payload.get('appliedStores')
            if (not isinstance(applied, list) or not applied
                    or any(not isinstance(sid, str) for sid in applied)
                    or len(applied) != len(set(applied))):
                raise ValueError('Choose at least one Daily Centro store. An empty list means every store in StoreHub.')
            for sid in applied:
                self.selected_store(sid, stores)
            payload = dict(strings({'name': payload.get('name', '')}), appliedStores=applied)
            path, method = '/priceBooks', 'POST'
            notes.append('This creates a StoreHub price book for the selected stores. Its ID is saved here automatically. The PDF has no endpoint to rename or delete the book itself.')
        elif operation.startswith('priceitem.'):
            bid = identifier(values.get('bookId'))
            if bid == 'default':
                raise ValueError('Default prices belong to product records. Custom pricing-rule actions require a separate price book.')
            path = '/priceBooks/' + quote(bid, safe='') + '/items'
            method = 'POST'
            if operation != 'priceitem.create':
                path += '/' + quote(identifier(values.get('itemId')), safe='')
                before = client.call(path)
                if not isinstance(before, dict):
                    raise ValueError('StoreHub returned an invalid price-book item.')
            if operation == 'priceitem.remove':
                pid = identifier(payload.get('productId'))
                if set(payload) != {'productId'} or before.get('productId') != pid:
                    raise ValueError('The product must match the price-book item being removed.')
                path += '?' + urlencode({'productId': pid})
                method, payload = 'DELETE', None
                notes.append('This removes this pricing rule from StoreHub. The change cannot be undone automatically.')
            else:
                if before:
                    payload = {**{k: before[k] for k in ('productId', 'taxCode', 'unitPrice', 'minQuantity', 'maxQuantity')
                                  if before.get(k) is not None}, **payload}
                payload = self.priceitem(payload, client)
                notes.append('This changes StoreHub selling prices for this price book. Unit price is rounded to two decimal places; quantities are unchanged.')
        else:
            raise ValueError('Choose a documented write operation.')
        return dict(operation=operation, path=path, method=method, payload=payload,
                    before=before, reference=ref, bookId=values.get('bookId'),
                    notes=notes)

    def preview(self):
        values = self.sh.body()
        operation = values.get('operation')
        if operation not in MUTATIONS:
            raise ValueError('Choose a documented write operation.')
        draft = self.build(operation, values, self.sh.client())
        fingerprint = digest({k: draft[k] for k in ('operation', 'path', 'payload')})
        token = uuid.uuid4().hex
        with self.sh.db() as db:
            if db.execute("SELECT 1 FROM sh_partner_drafts WHERE fingerprint=? AND status IN ('sending','uncertain')", (fingerprint,)).fetchone():
                raise ValueError('This change has an unknown outcome. Reconcile it in StoreHub before creating another draft.')
            db.execute('INSERT INTO sh_partner_drafts(token,operation,fingerprint,draft,status,created) VALUES (?,?,?,?,?,?)',
                       (token, operation, fingerprint, json.dumps(draft), 'preview', timestamp()))
        return jsonify(token=token, **draft)

    def preflight(self, draft, client):
        operation = draft['operation']
        if operation == 'customer.update' or operation.startswith('priceitem.') and operation != 'priceitem.create':
            current = client.call(draft['path'].split('?')[0])
            if digest(current) != digest(draft['before']):
                raise ValueError('The StoreHub record changed after preview. Load it again and review a new draft.')
        if operation.startswith('transaction.') or operation == 'pricebook.create':
            stores = self.active_stores(client)
            if operation == 'transaction.cancel':
                current = self.current_transaction(client, draft['reference'], stores)
                if digest(current) != digest(draft['before']):
                    raise ValueError('The sale changed after preview. Reload it and review again.')
            elif operation == 'transaction.create':
                self.selected_store(draft['payload']['storeId'], stores)
            else:
                for sid in draft['payload']['appliedStores']:
                    self.selected_store(sid, stores)

    def commit(self):
        values = self.sh.body()
        if values.get('confirmed') is not True:
            raise ValueError('Review and explicitly confirm this StoreHub change.')
        token = identifier(values.get('token'))
        # Also coordinates with sync/product publishing; transport separately throttles all calls.
        if not self.sh.lock.acquire(blocking=False):
            raise ValueError('A StoreHub operation is already running. Wait for it to finish.')
        try:
            with self.sh.db() as db:
                row = db.execute('SELECT * FROM sh_partner_drafts WHERE token=?', (token,)).fetchone()
                if not row:
                    raise ValueError('This preview was not found.')
                if row['status'] == 'done':
                    return jsonify(ok=True, result=json.loads(row['result']), repeated=True)
                if row['status'] != 'preview':
                    raise ValueError('This draft was already attempted. Check its outcome in Recent API changes; it will not be resent.')
                if datetime.now(timezone.utc) - datetime.fromisoformat(row['created']) > timedelta(minutes=15):
                    raise ValueError('This preview expired. Review a new draft.')
                if db.execute("SELECT 1 FROM sh_partner_drafts WHERE fingerprint=? AND token!=? AND status IN ('sending','uncertain','done')", (row['fingerprint'], token)).fetchone():
                    raise ValueError('An identical change was already sent or has an unknown outcome. Check StoreHub before proceeding.')
                draft = json.loads(row['draft'])
            client = self.sh.client()
            # Network reads must not hold a SQLite write lock: local receipts can proceed.
            self.preflight(draft, client)
            with self.sh.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT status,created FROM sh_partner_drafts WHERE token=?', (token,)).fetchone()
                if current['status'] != 'preview':
                    raise ValueError('This draft was already attempted. It will not be resent.')
                if datetime.now(timezone.utc) - datetime.fromisoformat(current['created']) > timedelta(minutes=15):
                    raise ValueError('This preview expired. Review a new draft.')
                if db.execute("SELECT 1 FROM sh_partner_drafts WHERE fingerprint=? AND token!=? AND status IN ('sending','uncertain','done')", (row['fingerprint'], token)).fetchone():
                    raise ValueError('An identical change was already sent or has an unknown outcome. Check StoreHub before proceeding.')
                db.execute("UPDATE sh_partner_drafts SET status='sending' WHERE token=?", (token,))
            # The durable sending state survives a process crash during the network request.
            try:
                result = client.call(draft['path'], draft['method'], draft['payload'])
                if draft['method'] != 'DELETE' and not isinstance(result, dict):
                    raise RemoteError('StoreHub returned an unexpected write response. Verify the result in StoreHub.', True)
                operation = draft['operation']
                if operation.startswith('customer.'):
                    expected = draft['payload'].get('refId') or draft['reference']
                    if str(result.get('refId', '')).casefold() != str(expected).casefold():
                        raise RemoteError('StoreHub returned a different customer reference. Verify the outcome in StoreHub.', True)
                elif operation.startswith('transaction.'):
                    expected = draft['payload'].get('refId') or draft['reference']
                    if str(result.get('refId', '')).casefold() != str(expected).casefold():
                        raise RemoteError('StoreHub returned a different transaction reference. Verify the outcome in StoreHub.', True)
                    if operation == 'transaction.cancel' and result.get('isCancelled') is not True:
                        raise RemoteError('StoreHub did not confirm cancellation. Verify the sale in StoreHub.', True)
                elif operation == 'pricebook.create' or operation in ('priceitem.create', 'priceitem.update'):
                    try:
                        identifier(result.get('id'))
                    except ValueError:
                        raise RemoteError('StoreHub returned no valid created-record ID. Verify the outcome in StoreHub.', True) from None
                    if operation.startswith('priceitem.') and result.get('productId') != draft['payload']['productId']:
                        raise RemoteError('StoreHub returned a different pricing-rule product. Verify the outcome in StoreHub.', True)
            except RemoteError as error:
                status = 'uncertain' if error.uncertain else 'failed'
                with self.sh.db() as db:
                    db.execute('UPDATE sh_partner_drafts SET status=?,error=? WHERE token=?', (status, str(error), token))
                raise ValueError(str(error) + (' The outcome is unknown; this draft will not be resent.' if error.uncertain else '')) from None
            with self.sh.db() as db:
                db.execute("UPDATE sh_partner_drafts SET status='done',result=? WHERE token=?", (json.dumps(result), token))
                if draft['operation'] == 'pricebook.create' and result.get('id'):
                    db.execute('INSERT OR REPLACE INTO sh_partner_books VALUES (?,?)', (identifier(result['id']), json.dumps(result)))
            return jsonify(ok=True, result=result, repeated=False)
        finally:
            self.sh.lock.release()

    def history(self):
        with self.sh.db() as db:
            rows = [dict(r) for r in db.execute("SELECT token,operation,status,created,error FROM sh_partner_drafts WHERE status!='preview' ORDER BY rowid DESC LIMIT 50")]
        return jsonify(changes=rows)
