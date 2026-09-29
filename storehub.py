"""Documented partner API only. Remote stock is a snapshot, never a ledger write."""
import base64
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

from flask import jsonify, request


class RemoteError(ValueError):
    def __init__(self, message, uncertain=False, status_code=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward the API token to another host.


class StoreHubClient:
    _queue = threading.Lock()
    _next_call = 0

    def __init__(self, username, token):
        self.authorization = 'Basic ' + base64.b64encode(f'{username}:{token}'.encode()).decode()

    def call(self, path, method='GET', body=None):
        payload = json.dumps(body, allow_nan=False).encode() if body is not None else None
        req = Request('https://api.storehubhq.com' + path, data=payload, method=method,
                      headers={'Authorization': self.authorization, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        with self._queue:
            time.sleep(max(0, StoreHubClient._next_call - time.monotonic()))
            StoreHubClient._next_call = time.monotonic() + 0.6
            try:
                with build_opener(NoRedirect()).open(req, timeout=25) as response:
                    if response.status == 204:
                        return None  # Documented success response for removing a price-book item.
                    raw = response.read()
            except HTTPError as error:
                uncertain = method != 'GET' and (error.code >= 500 or error.code == 408)
                messages = {401: 'StoreHub rejected the account or API token.', 403: 'StoreHub API access is not enabled for this account.', 429: 'StoreHub rate limit reached. Sync is stopped; contact StoreHub if the account remains limited.'}
                raise RemoteError(messages.get(error.code, f'StoreHub returned HTTP {error.code}. Check your account access and API documentation.'), uncertain, error.code) from None
            except (URLError, TimeoutError, OSError):
                raise RemoteError('Could not reach StoreHub. Check the Internet connection and try again.', method != 'GET') from None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise RemoteError('StoreHub returned an invalid response.', method != 'GET') from None


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('StoreHub returned an invalid number.') from None


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', str(value).casefold())


def array(value):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError('StoreHub returned an unexpected list response. Local records were not changed.')
    return value


def identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError('StoreHub returned an invalid identifier.')
    return value


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS sh_products(remote_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_links(local_id TEXT PRIMARY KEY REFERENCES products(id), remote_id TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_stores(remote_id TEXT PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_locations(location TEXT PRIMARY KEY, remote_id TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_inventory(remote_id TEXT, store_id TEXT, quantity TEXT, warning TEXT, ideal TEXT, PRIMARY KEY(remote_id,store_id));
    CREATE TABLE IF NOT EXISTS sh_sales(ref_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_orders(remote_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_publish(local_id TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sh_order_windows(start TEXT, end TEXT, synced TEXT, PRIMARY KEY(start,end));
    CREATE TABLE IF NOT EXISTS sh_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')


class Integration:
    def __init__(self, app, db_factory, local_config, warehouse, removed):
        self.app, self.db, self.config, self.warehouse, self.removed = app, db_factory, local_config, warehouse, removed
        self.lock = threading.Lock()
        self.busy = False
        from purchase_orders import PurchaseOrders
        self.purchase_orders = PurchaseOrders(self)
        for route, method, handler in [('', 'GET', self.status), ('/test', 'POST', self.test), ('/sync', 'POST', self.sync_route), ('/locations', 'POST', self.locations), ('/schedule', 'POST', self.schedule), ('/link', 'POST', self.link), ('/baseline', 'POST', self.baseline), ('/publish-preview', 'POST', self.preview), ('/publish', 'POST', self.publish)]:
            app.add_url_rule('/api/storehub' + route, 'storehub' + (route or '/status'), handler, methods=[method])

    def credentials(self):
        return os.environ.get('STOREHUB_USERNAME', '').strip(), os.environ.get('STOREHUB_API_TOKEN', '').strip()

    def client(self):
        factory = self.app.config.get('STOREHUB_CLIENT_FACTORY')
        if factory:
            return factory()
        username, token = self.credentials()
        if not username or not token:
            raise ValueError('Set up your StoreHub account on this computer, then restart the warehouse app.')
        return StoreHubClient(username, token)

    def body(self):
        # Integration mutations require JSON and same-origin browser requests.
        if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
            raise ValueError('Open this action directly in the warehouse app.')
        if not request.is_json or not isinstance(request.get_json(), dict):
            raise ValueError('Send a JSON object.')
        return request.get_json()

    def setting(self, db, key, default=''):
        row = db.execute('SELECT value FROM sh_settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def save(self, db, key, value):
        db.execute('INSERT OR REPLACE INTO sh_settings VALUES (?,?)', (key, str(value)))

    def allowed_store(self, name):
        return normalized(name) in {normalized(self.warehouse),
                                   normalized('The Daily Centro - Bantayan'),
                                   normalized('The Daily Centro - Getafe'),
                                   normalized('The Daily Centro - Tabunoc')}

    def stock_map(self, db):
        stocks = {}
        for row in db.execute('SELECT l.local_id,m.location,i.quantity,i.warning,i.ideal FROM sh_inventory i JOIN sh_links l ON l.remote_id=i.remote_id JOIN sh_locations m ON m.remote_id=i.store_id JOIN sh_stores s ON s.remote_id=i.store_id'):
            stocks.setdefault(row['local_id'], {})[row['location']] = dict(quantity=row['quantity'], warning=row['warning'], ideal=row['ideal'])
        return stocks

    def snapshot(self, db):
        pending_ids = [r['id'] for r in db.execute('SELECT id,fields FROM products') if json.loads(r['fields']).get('_storehub_baseline_pending')]
        return dict(pendingIds=pending_ids, configured=all(self.credentials()) or bool(self.app.config.get('STOREHUB_CLIENT_FACTORY')), lastSync=self.setting(db, 'last_sync'), error=self.setting(db, 'error'), busy=self.busy, stocks=self.stock_map(db), linked=dict(db.execute('SELECT local_id,remote_id FROM sh_links')))

    def status(self):
        with self.db() as db:
            result = self.snapshot(db)
            locations = self.config(db)[1]
            stores = [dict(id=r['remote_id'], name=r['name']) for r in db.execute('SELECT * FROM sh_stores')
                      if self.allowed_store(r['name'])]
            allowed = {s['id'] for s in stores}
            mapping = {r['location']: r['remote_id'] for r in db.execute('SELECT * FROM sh_locations')
                       if r['remote_id'] in allowed}
            products = {r['id']: json.loads(r['fields']) for r in db.execute('SELECT * FROM products')}
            differences, suggestions = [], []
            # Outstanding local transfers reduce suggestions but never change remote balances.
            pending = {}
            created_reserve = {}
            total_reserve = {}
            for doc in db.execute("SELECT payload,status FROM documents WHERE kind='transfer' AND status IN ('Created','Shipped')"):
                payload = json.loads(doc['payload'])
                for item in payload['items']:
                    key = (item['product_id'], payload['target'])
                    amount = decimal(item['quantity'])
                    pending[key] = pending.get(key, Decimal(0)) + amount
                    total_reserve[item['product_id']] = total_reserve.get(item['product_id'], Decimal(0)) + amount
                    if doc['status'] == 'Created':
                        created_reserve[item['product_id']] = created_reserve.get(item['product_id'], Decimal(0)) + amount
            sales = {}
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            for row in db.execute('SELECT payload FROM sh_sales'):
                sale = json.loads(row[0])
                try:
                    when = datetime.fromisoformat(sale['transactionTime'].replace('Z', '+00:00'))
                except (ValueError, KeyError):
                    continue
                if when.tzinfo is None or when < cutoff or when > datetime.now(timezone.utc) or sale.get('isCancelled') or sale.get('transactionType') not in ['Sale', 'Return']:
                    continue
                direction = -1 if sale.get('transactionType') == 'Return' else 1
                for item in sale.get('items', []):
                    key = (item['productId'], sale['storeId'])
                    sales[key] = sales.get(key, Decimal(0)) + decimal(item['quantity']) * direction
            for pid, stock in result['stocks'].items():
                p = products[pid]
                for location, remote in stock.items():
                    local = p.get(location + '_Quantity')
                    baseline_pending = p.get('_storehub_baseline_pending', False)
                    if not baseline_pending:
                        delta = decimal(local or '0') - decimal(remote['quantity'])
                        if delta:
                            differences.append(dict(productId=pid, name=p['Product Name'], location=location, local=local or '0', remote=remote['quantity'], difference=str(delta)))
                    if location == self.warehouse or p.get('Track Stock Levels') != '1':
                        continue
                    sold = max(Decimal(0), sales.get((result['linked'][pid], mapping[location]), Decimal(0)))
                    target = (sold / 30 * 7).to_integral_value(rounding='ROUND_CEILING')
                    on_hand = decimal(remote['quantity'])
                    outstanding = pending.get((pid, location), Decimal(0))
                    need = max(Decimal(0), target - on_hand - outstanding)
                    if not need:
                        continue
                    warehouse_remote = stock.get(self.warehouse)
                    remote_available = max(Decimal(0), decimal(warehouse_remote['quantity'])) if warehouse_remote else Decimal(0)
                    local_available = Decimal(0) if baseline_pending else max(Decimal(0), decimal(p.get(self.warehouse + '_Quantity') or '0'))
                    reserved = total_reserve.get(pid, Decimal(0))
                    local_reserved = created_reserve.get(pid, Decimal(0))
                    available = min(max(Decimal(0), local_available - local_reserved), max(Decimal(0), remote_available - reserved))
                    suggestions.append(dict(productId=pid, name=p['Product Name'], location=location, onHand=str(on_hand), target=str(target), pending=str(outstanding), sold30Days=str(sold), needed=str(need), available=str(available), suggested=str(min(need, available)), averageDailySales=str((sold / 30).quantize(Decimal('0.01'))), basis='7 days of sales', salesBased=True, baselinePending=baseline_pending))
            # Allocate a shared warehouse balance once across branch suggestions.
            remaining = {}
            suggestions.sort(key=lambda item: (item['productId'], item['location']))
            for item in suggestions:
                pid = item['productId']
                budget = remaining.get(pid, decimal(item['available']))
                item['suggested'] = str(min(decimal(item['needed']), budget))
                remaining[pid] = budget - decimal(item['suggested'])
            jobs = {r['local_id']: dict(status=r['status'], detail=r['detail']) for r in db.execute('SELECT * FROM sh_publish')}
            new = [dict(id=pid, name=p['Product Name'], sku=p['SKU'], barcode=p['Barcode'], cost=p['Cost'], price=p['Tax-Inclusive Price'], quantity=p.get(self.warehouse + '_Quantity', '0'), job=jobs.get(pid)) for pid, p in products.items() if pid.startswith('local-') and pid not in result['linked']]
            unlinked = [{**json.loads(r['payload']), 'id': r['remote_id']} for r in db.execute('SELECT * FROM sh_products WHERE remote_id NOT IN (SELECT remote_id FROM sh_links)')]
            orders = [json.loads(r[0]) for r in db.execute('SELECT payload FROM sh_orders')]
            result.update(locations=locations, mapping=mapping, stores=stores, differences=differences, suggestions=suggestions, newProducts=new, unlinked=unlinked, baselinePending=sum(bool(p.get('_storehub_baseline_pending')) for p in products.values()), purchaseOrders=orders, interval=int(self.setting(db, 'interval', '0')), salesSync=self.setting(db, 'sales_sync'), demandDays=7, salesCount=db.execute('SELECT COUNT(*) FROM sh_sales').fetchone()[0], warnings=json.loads(self.setting(db, 'warnings', '[]')))
        return jsonify(result)

    def test(self):
        self.body()
        stores = array(self.client().call('/stores'))
        return jsonify(ok=True, stores=len([s for s in stores if self.allowed_store(s.get('name', ''))]))

    def dates(self, client, path, key, start=None, end=None):
        # Bounded daily windows; fail visibly instead of accepting the documented 5000-row cap.
        records = {}
        today = datetime.now(timezone.utc).date()
        windows = [(start or today - timedelta(days=30), end or today + timedelta(days=1))]
        while windows:
            start, end = windows.pop()
            params = dict(from_=start.isoformat(), to=end.isoformat())
            params['from'] = params.pop('from_')
            if path == '/transactions':
                params['includeOnline'] = 'true'
            items = array(client.call(path + '?' + urlencode(params)))
            if len(items) >= 5000:
                if (end - start).days <= 1:
                    raise ValueError('A StoreHub daily response reached 5000 records. Sync stopped to avoid incomplete data; request a paginated API from StoreHub.')
                middle = start + (end - start) // 2
                windows.extend([(start, middle), (middle, end)])
                continue
            for item in items:
                record_id = item.get(key)
                if not isinstance(record_id, (str, int)) or not str(record_id):
                    raise ValueError('StoreHub returned a record without its documented identifier.')
                records[str(record_id)] = item
        return list(records.values())

    def sync_route(self):
        self.body()
        self.sync()
        return self.status()

    def sync(self):
        if not self.lock.acquire(blocking=False):
            raise ValueError('A StoreHub operation is already running. Wait for it to finish.')
        self.busy = True
        try:
            client = self.client()
            stores = [s for s in array(client.call('/stores')) if self.allowed_store(s.get('name', ''))]
            for store in stores:
                identity(store.get('id'))
            remote_products = array(client.call('/products'))
            for p in remote_products:
                identity(p.get('id'))
                if not isinstance(p.get('name'), str) or not isinstance(p.get('trackStockLevel'), bool):
                    raise ValueError('StoreHub returned an unexpected product schema.')
            with self.db() as db:
                local_locations = self.config(db)[1]
                mapping = dict(db.execute('SELECT location,remote_id FROM sh_locations'))
                for location in local_locations:
                    candidates = [s for s in stores if normalized(s.get('name')) == normalized(location)]
                    if location not in mapping and len(candidates) == 1 and candidates[0]['id'] not in mapping.values():
                        mapping[location] = candidates[0]['id']
            allowed = {s['id'] for s in stores}
            mapping = {loc: sid for loc, sid in mapping.items() if loc in local_locations and sid in allowed}
            inventory = []
            for sid in mapping.values():
                for stock in array(client.call('/inventory/' + quote(sid, safe=''))):
                    identity(stock.get('productId'))
                    qty = str(decimal(stock.get('quantityOnHand')))
                    warning = None if stock.get('warningStock') is None else str(decimal(stock['warningStock']))
                    ideal = None if stock.get('idealStock') is None else str(decimal(stock['idealStock']))
                    inventory.append((stock['productId'], sid, qty, warning, ideal))
            warnings = []
            # Optional endpoints may not be enabled on every merchant account.
            sales = orders = None
            orders_error = ''
            for path in ['/transactions', '/purchaseOrders']:
                try:
                    items = self.dates(client, path, 'refId' if path == '/transactions' else 'purchaseOrderId')
                    if path == '/transactions':
                        candidate_sales = [s for s in items if s.get('storeId') in mapping.values()]
                        for s in candidate_sales:
                            for item in array(s.get('items', [])):
                                identity(item.get('productId')); decimal(item.get('quantity'))
                        sales = candidate_sales
                    else:
                        orders = [self.purchase_orders.validate(o) for o in items if o.get('targetStoreId') in mapping.values()]
                except ValueError as error:
                    if isinstance(error, RemoteError) and error.status_code == 429:
                        raise
                    warnings.append(f'{path}: {error}')
                    if path == '/purchaseOrders':
                        orders_error = str(error)
            synced = timestamp()
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                headers = self.config(db)[0]
                db.execute('DELETE FROM sh_stores')
                db.executemany('INSERT INTO sh_stores VALUES (?,?)', [(s['id'], s.get('name', '')) for s in stores])
                db.execute('DELETE FROM sh_locations')
                db.executemany('INSERT INTO sh_locations VALUES (?,?)', list(mapping.items()))
                db.execute('DELETE FROM sh_products')
                for remote in remote_products:
                    rid = remote['id']
                    db.execute('INSERT INTO sh_products VALUES (?,?)', (rid, json.dumps(remote)))
                    linked = db.execute('SELECT local_id FROM sh_links WHERE remote_id=?', (rid,)).fetchone()
                    row = db.execute('SELECT * FROM products WHERE id=? OR json_extract(fields,\'$."Product Id"\')=?', (rid, rid)).fetchone() if not linked else db.execute('SELECT * FROM products WHERE id=?', (linked[0],)).fetchone()
                    if not row:
                        # Never silently merge a local draft solely by SKU/barcode.
                        codes = {b.strip() for b in str(remote.get('barcode') or '').split(',') if b.strip()}
                        conflict = any((remote.get('sku') and str(p.get('SKU', '')).casefold() == str(remote['sku']).casefold()) or codes.intersection(b.strip() for b in p.get('Barcode', '').split(',') if b.strip()) for r in db.execute('SELECT fields FROM products') for p in [json.loads(r[0])])
                        if conflict:
                            continue
                        p = dict.fromkeys(headers, '')
                        p.update({'Product Id': rid, 'Inventory Type': 'Simple', '_storehub_baseline_pending': True, '_storehub_api_imported': True})
                        row_id, changed = rid, False
                    else:
                        p = json.loads(row['fields']); row_id, changed = row['id'], bool(row['changed'])
                    if not changed:
                        p.update({'Product Name': remote['name'], 'SKU': str(remote.get('sku') or ''), 'Barcode': str(remote.get('barcode') or ''), 'Category': str(remote.get('category') or ''), 'Cost': str(decimal(remote.get('cost') or 0)), 'Price Type': remote.get('priceType', 'Fixed'), 'Track Stock Levels': '1' if remote['trackStockLevel'] else '0'})
                        # API unitPrice is kept in sh_products. Never substitute it into Tax-Inclusive Price.
                        db.execute('INSERT INTO products VALUES (?,?,0) ON CONFLICT(id) DO UPDATE SET fields=excluded.fields', (row_id, json.dumps(p)))
                    db.execute('INSERT OR IGNORE INTO sh_links VALUES (?,?)', (row_id, rid))
                    db.execute("UPDATE sh_publish SET status='published',detail='Linked to StoreHub',updated=? WHERE local_id=?", (synced, row_id))
                db.execute('DELETE FROM sh_inventory')
                db.executemany('INSERT INTO sh_inventory VALUES (?,?,?,?,?)', inventory)
                if sales is not None:
                    db.execute('DELETE FROM sh_sales')
                    db.executemany('INSERT INTO sh_sales VALUES (?,?)', [(s['refId'], json.dumps(s)) for s in sales])
                    self.save(db, 'sales_sync', synced)
                if orders is not None:
                    today = datetime.now(timezone.utc).date()
                    self.purchase_orders.save_orders(db, orders, today - timedelta(days=30), today, synced)
                elif orders_error:
                    self.save(db, 'orders_error', orders_error)
                self.save(db, 'last_sync', synced); self.save(db, 'error', ''); self.save(db, 'warnings', json.dumps(warnings))
        except Exception as error:
            with self.db() as db:
                self.save(db, 'error', str(error) if isinstance(error, ValueError) else 'Sync failed. Previous stock snapshot and local records were preserved.')
            if not isinstance(error, ValueError):
                raise ValueError('Sync failed. Previous stock snapshot and local records were preserved.') from None
            raise
        finally:
            self.busy = False
            self.lock.release()

    def locations(self):
        body = self.body()
        with self.lock, self.db() as db:
            mapping = body.get('mapping', {})
            allowed = {r['remote_id'] for r in db.execute('SELECT * FROM sh_stores')
                       if self.allowed_store(r['name'])}
            if not isinstance(mapping, dict) or set(mapping) - set(self.config(db)[1]) or any(sid not in allowed for sid in mapping.values()) or len(set(mapping.values())) != len(mapping):
                raise ValueError('Choose a different active StoreHub location for each local location.')
            db.execute('DELETE FROM sh_locations')
            db.executemany('INSERT INTO sh_locations VALUES (?,?)', list(mapping.items()))
            # A mapping change invalidates stock snapshots until refreshed.
            db.execute('DELETE FROM sh_inventory'); db.execute('DELETE FROM sh_sales'); db.execute('DELETE FROM sh_orders'); db.execute('DELETE FROM sh_order_windows')
            self.save(db, 'last_sync', '')
        return jsonify(ok=True)

    def schedule(self):
        body = self.body(); interval = body.get('interval')
        if interval not in [0, 15, 30, 60]:
            raise ValueError('Choose manual, 15, 30 or 60 minute syncing.')
        if interval:
            self.client()
        with self.db() as db:
            self.save(db, 'interval', interval)
        return jsonify(ok=True)

    def link(self):
        body = self.body()
        with self.lock, self.db() as db:
            row = db.execute('SELECT fields FROM products WHERE id=?', (body.get('product_id'),)).fetchone()
            remote = db.execute('SELECT payload FROM sh_products WHERE remote_id=?', (body.get('remote_id'),)).fetchone()
            if not row or not remote:
                raise ValueError('Select a local product and a synced StoreHub product.')
            p, r = json.loads(row[0]), json.loads(remote[0])
            if db.execute('SELECT 1 FROM sh_links WHERE local_id=? OR remote_id=?', (body['product_id'], body['remote_id'])).fetchone():
                raise ValueError('This product is already linked.')
            if not (p['SKU'] and p['SKU'].casefold() == str(r.get('sku') or '').casefold()) and not set(p['Barcode'].split(',')).difference({''}).intersection(str(r.get('barcode') or '').split(',')):
                raise ValueError('The SKU or barcode must match before linking.')
            p['Product Id'] = body['remote_id']
            db.execute('UPDATE products SET fields=? WHERE id=?', (json.dumps(p), body['product_id']))
            db.execute('INSERT INTO sh_links VALUES (?,?)', (body['product_id'], body['remote_id']))
            db.execute("UPDATE sh_publish SET status='published',detail='Reviewed link to StoreHub',updated=? WHERE local_id=?", (timestamp(), body['product_id']))
        return jsonify(ok=True)

    def baseline(self):
        self.body()
        with self.lock, self.db() as db:
            stocks = self.stock_map(db); count = 0
            for row in db.execute('SELECT * FROM products').fetchall():
                p = json.loads(row['fields'])
                if not p.get('_storehub_baseline_pending') or row['changed'] or db.execute('SELECT 1 FROM movements WHERE product_id=?', (row['id'],)).fetchone():
                    continue
                if not all(location in stocks.get(row['id'], {}) for location in self.config(db)[1]):
                    continue
                for loc, stock in stocks[row['id']].items():
                    p[loc + '_Quantity'] = stock['quantity']
                p.pop('_storehub_baseline_pending', None)
                db.execute('UPDATE products SET fields=? WHERE id=?', (json.dumps(p), row['id']))
                db.execute('INSERT INTO movements(created,product_id,location,before_qty,after_qty,reason) VALUES (?,?,?,?,?,?)', (timestamp(), row['id'], self.warehouse, '', p[self.warehouse + '_Quantity'], 'Initial StoreHub snapshot baseline; no remote stock write'))
                count += 1
        return jsonify(initialized=count)

    def publish_payload(self, db, body):
        pid = body.get('product_id', '')
        row = db.execute('SELECT fields FROM products WHERE id=?', (pid,)).fetchone()
        if not row or not pid.startswith('local-'):
            raise ValueError('Only newly created local products can be published.')
        p = json.loads(row[0])
        if p.get('Product Id') or db.execute('SELECT 1 FROM sh_links WHERE local_id=?', (pid,)).fetchone():
            raise ValueError('This product is already in StoreHub.')
        if body.get('unit_price') is None or str(body['unit_price']).strip() == '':
            raise ValueError('Enter the tax-exclusive StoreHub unit price.')
        price, cost = decimal(body['unit_price']), decimal(p['Cost'] or '0')
        if not math.isfinite(float(price)) or not math.isfinite(float(cost)):
            raise ValueError('Price or cost is too large.')
        if price < 0 or cost < 0:
            raise ValueError('Price and cost must be zero or greater.')
        return pid, dict(name=p['Product Name'], sku=p['SKU'], barcode=p['Barcode'], category=p['Category'], priceType='Fixed', unitPrice=float(price), cost=float(cost), trackStockLevel=True)

    def preview(self):
        body = self.body()
        with self.db() as db:
            _, payload = self.publish_payload(db, body)
        return jsonify(payload=payload, note='Creates product details only. Initial warehouse quantity is not sent to StoreHub.')

    def publish(self):
        body = self.body()
        if body.get('confirmed') is not True:
            raise ValueError('Review and confirm the product before publishing.')
        if not self.lock.acquire(blocking=False):
            raise ValueError('A StoreHub operation is already running.')
        self.busy = True
        pid = None
        submitted = False
        try:
            client = self.client()
            with self.db() as db:
                pid, payload = self.publish_payload(db, body)
                job = db.execute('SELECT status FROM sh_publish WHERE local_id=?', (pid,)).fetchone()
                if job and job[0] in ['sending', 'uncertain']:
                    raise ValueError('The previous publish may have succeeded. Sync and review/link the matching StoreHub product before retrying.')
            existing = array(client.call('/products'))
            codes = set(payload['barcode'].split(',')).difference({''})
            if any(str(r.get('sku') or '').casefold() == payload['sku'].casefold() or codes.intersection(str(r.get('barcode') or '').split(',')) for r in existing):
                raise ValueError('This SKU or barcode already exists in StoreHub. Sync, then review and link the existing product.')
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO sh_publish VALUES (?,?,?,?)', (pid, 'sending', 'Publishing product details', timestamp()))
            submitted = True
            remote = client.call('/products', 'POST', payload)
            if not isinstance(remote, dict):
                raise RemoteError('StoreHub returned an unexpected creation response.', True)
            rid = identity(remote.get('id'))
            with self.db() as db:
                p = json.loads(db.execute('SELECT fields FROM products WHERE id=?', (pid,)).fetchone()[0])
                p['Product Id'] = rid
                db.execute('UPDATE products SET fields=? WHERE id=?', (json.dumps(p), pid))
                db.execute('INSERT INTO sh_links VALUES (?,?)', (pid, rid))
                db.execute('INSERT OR REPLACE INTO sh_products VALUES (?,?)', (rid, json.dumps(remote)))
                db.execute('INSERT OR REPLACE INTO sh_publish VALUES (?,?,?,?)', (pid, 'published', 'Product created; stock not sent', timestamp()))
            return jsonify(remoteId=rid, note='Product created in StoreHub. Its warehouse quantity was not sent.')
        except Exception as error:
            if submitted:
                uncertain = not isinstance(error, RemoteError) or error.uncertain
                with self.db() as db:
                    db.execute('INSERT OR REPLACE INTO sh_publish VALUES (?,?,?,?)', (pid, 'uncertain' if uncertain else 'failed', 'Result unknown: sync and review before retrying.' if uncertain else str(error), timestamp()))
            if isinstance(error, ValueError):
                raise
            raise ValueError('Publish result is unknown. Sync and review before retrying.') from None
        finally:
            self.busy = False; self.lock.release()

    def start_scheduler(self):
        def worker():
            last_attempt = time.monotonic()
            while True:
                time.sleep(10)
                with self.db() as db:
                    interval = int(self.setting(db, 'interval', '0'))
                if interval and time.monotonic() - last_attempt >= interval * 60:
                    last_attempt = time.monotonic()
                    try:
                        self.sync()
                    except ValueError:
                        # Fail closed: permission/rate-limit errors should not trigger repeated polling.
                        with self.db() as db:
                            self.save(db, 'interval', '0')
        threading.Thread(target=worker, daemon=True, name='storehub-sync').start()
