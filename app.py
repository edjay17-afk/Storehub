import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, Response
import storehub
import stock_transfers
import partner_api
import wastage
import wastage_imports
import notifications
import manual_inventory

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config['DATABASE'] = os.environ.get('WAREHOUSE_DB', str(ROOT / 'data' / 'warehouse.db'))
WAREHOUSE = 'TheDailyCentro Warehouse'
REMOVED_STORES = {'688'}


def connection():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def number(value, positive=False):
    try:
        n = Decimal(str(value or '0'))
    except InvalidOperation:
        raise ValueError('Enter a valid quantity or cost.')
    if not n.is_finite() or n < 0 or (positive and n <= 0):
        raise ValueError('Quantity must be positive.' if positive else 'Value must be zero or greater.')
    return n


def fmt(value):
    return format(value, 'f')


def now():
    return datetime.now().strftime('%m/%d/%Y %H:%M')


def initialize():
    with connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, fields TEXT NOT NULL, changed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY, created TEXT, product_id TEXT, location TEXT, before_qty TEXT, after_qty TEXT, reason TEXT);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        storehub.initialize(db)
        stock_transfers.initialize(db)
        partner_api.initialize(db)
        wastage.initialize(db)
        wastage_imports.initialize(db)
        manual_inventory.initialize(db)
        if not db.execute("SELECT 1 FROM metadata WHERE key='headers'").fetchone():
            with open(ROOT / 'data' / 'Products.csv', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames
                instructions = next(reader)
                for row in reader:
                    db.execute('INSERT INTO products(id,fields) VALUES (?,?)', (row['Product Id'], json.dumps(row)))
            db.execute('INSERT INTO metadata VALUES (?,?)', ('headers', json.dumps(headers)))
            db.execute('INSERT INTO metadata VALUES (?,?)', ('instructions', json.dumps(instructions)))


def config(db):
    headers = json.loads(db.execute("SELECT value FROM metadata WHERE key='headers'").fetchone()[0])
    headers = [h for h in headers if not any(h.startswith(store + '_') for store in REMOVED_STORES)]
    return headers, [h[:-9] for h in headers if h.endswith('_Quantity')]


def product(db, pid):
    r = db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
    if not r:
        raise ValueError('Product was not found.')
    return json.loads(r['fields'])


def change_stock(db, pid, location, quantity, reason, absolute=False):
    if location not in config(db)[1]:
        raise ValueError('This store is no longer available.')
    p = product(db, pid)
    if p.get('_storehub_baseline_pending'):
        raise ValueError('Initialize this new synced product\'s balances in StoreHub connection before recording local stock.')
    if p['Track Stock Levels'] != '1':
        raise ValueError(f"{p['Product Name']} does not track stock.")
    key = location + '_Quantity'
    before = Decimal(p.get(key) or '0')
    after = quantity if absolute else before + quantity
    if after < 0:
        raise ValueError(f"Insufficient stock for {p['Product Name']}: {fmt(before)} available.")
    p[key] = fmt(after)
    db.execute('UPDATE products SET fields=?, changed=1 WHERE id=?', (json.dumps(p), pid))
    db.execute('INSERT INTO movements(created,product_id,location,before_qty,after_qty,reason) VALUES (?,?,?,?,?,?)', (now(), pid, location, fmt(before), fmt(after), reason))


@app.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@app.route('/')
def index():
    return render_template('index.html')


@app.get('/api/catalog')
def catalog():
    with connection() as db:
        headers, locations = config(db)
        products = [dict(id=r['id'], changed=bool(r['changed']), **{h: p.get(h, '') for h in headers}) for r in db.execute('SELECT * FROM products') for p in [json.loads(r['fields'])]]
        deferred = request.args.get('defer') == '1'
        documents = [] if deferred else local_history(db, 'documents')
        movements = [] if deferred else local_history(db, 'movements')
        open_requests = db.execute("SELECT count(*) FROM documents WHERE status NOT IN ('Completed','Received','Cancelled')").fetchone()[0]
        storehub_state = integration.snapshot(db)
    scanner_file = ROOT / 'data' / 'scanner-url.txt'
    scanner_url = scanner_file.read_text(encoding='utf-8').strip() if scanner_file.exists() else ''
    parsed = urlsplit(scanner_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        scanner_url = ''
    return jsonify(products=products, locations=locations, warehouse=WAREHOUSE, documents=documents, movements=movements, scannerUrl=scanner_url, storehub=storehub_state, summary=dict(openRequests=open_requests))


def local_history(db, kind):
    if kind == 'documents':
        return [dict(r, payload=json.loads(r['payload'])) for r in db.execute('SELECT * FROM documents ORDER BY rowid DESC')]
    return [dict(r) for r in db.execute('SELECT m.*, json_extract(p.fields,\'$."Product Name"\') AS name FROM movements m JOIN products p ON p.id=m.product_id ORDER BY m.id DESC LIMIT 100')]


@app.get('/api/local-history/<kind>')
def get_local_history(kind):
    if kind not in ('documents', 'movements'):
        raise ValueError('Choose orders or stock movements.')
    with connection() as db:
        return jsonify(**{kind: local_history(db, kind)})



@app.get('/api/scanner-status')
def scanner_status():
    # Only the LAN app reads this private monitor state. The public scanner has no API.
    status_file = ROOT / 'data' / 'scanner-status.json'
    try:
        state = json.loads(status_file.read_text(encoding='utf-8-sig'))
        checked = datetime.fromisoformat(state['checked'].replace('Z', '+00:00'))
        from datetime import timezone
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        url = urlsplit(state.get('url', ''))
        ready = (state.get('ready') is True and 0 <= age < 90 and url.scheme == 'https'
                 and url.hostname and url.hostname.endswith('.trycloudflare.com')
                 and not url.username and not url.password)
        if ready:
            return jsonify(ready=True, url=state['url'])
        message = state.get('message') if age < 90 and not state.get('ready') else None
    except (OSError, ValueError, KeyError, TypeError):
        message = None
    return jsonify(ready=False, url='', message=message or 'The secure scanner is offline. Start the secure scanner on the warehouse computer.')

@app.post('/api/products')
def create_product():
    body = request.get_json()
    name, sku = str(body.get('name', '')).strip(), str(body.get('sku', '')).strip()
    barcodes = [b.strip() for b in str(body.get('barcode', '')).split(',') if b.strip()]
    if len(barcodes) != len(set(barcodes)):
        raise ValueError('A barcode may only be listed once.')
    if not name or not sku:
        raise ValueError('New products require a name and a unique SKU for StoreHub import.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        headers, locations = config(db)
        for row in db.execute('SELECT fields FROM products'):
            p = json.loads(row[0])
            if p['SKU'].casefold() == sku.casefold():
                raise ValueError('This SKU already exists.')
            existing_barcodes = {b.strip() for b in p['Barcode'].split(',') if b.strip()}
            if existing_barcodes.intersection(barcodes):
                raise ValueError(f"This barcode already belongs to {p['Product Name']}. Receive stock for that product instead.")
        pid = 'local-' + uuid.uuid4().hex
        p = dict.fromkeys(headers, '')
        p.update({'Product Name': name, 'SKU': sku, 'Category': body.get('category', ''), 'Supplier': body.get('supplier', ''), 'Barcode': ','.join(barcodes), 'Cost': fmt(number(body.get('cost'))), 'Tax-Inclusive Price': fmt(number(body.get('price'))), 'Price Type': 'Fixed', 'Inventory Type': 'Simple', 'Track Stock Levels': '1', 'SC/PWD Discount': '0', 'Solo Parent Discount': '0'})
        for location in locations:
            p[location + '_Quantity'] = '0'
        db.execute('INSERT INTO products VALUES (?,?,1)', (pid, json.dumps(p)))
        qty = number(body.get('quantity'))
        if qty:
            change_stock(db, pid, WAREHOUSE, qty, 'Initial warehouse receipt')
    return jsonify(id=pid), 201


@app.post('/api/receive')
def receive():
    b = request.get_json()
    reason = str(b.get('reason', '')).strip()
    if not reason:
        raise ValueError('Enter a receipt reference or count reason.')
    mode = b.get('mode')
    if mode not in ['add', 'count']:
        raise ValueError('Choose receive stock or warehouse count.')
    qty = number(b.get('quantity'), positive=mode == 'add')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        change_stock(db, b['product_id'], WAREHOUSE, qty, reason, absolute=mode == 'count')
    return jsonify(ok=True)


@app.post('/api/documents')
def create_document():
    b = request.get_json()
    kind = b.get('kind')
    if kind not in ['purchase', 'transfer']:
        raise ValueError('Choose a purchase order or stock transfer.')
    if not b.get('requested_by', '').strip():
        raise ValueError('Enter who requested this order.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _, locations = config(db)
        if kind == 'purchase' and not b.get('supplier', '').strip():
            raise ValueError('Purchase orders require a supplier.')
        if kind == 'transfer' and (b.get('target') not in locations or b['target'] == WAREHOUSE):
            raise ValueError('Choose a branch for the stock transfer.')
        items, seen = [], set()
        for line in b.get('items', []):
            p = product(db, line['product_id'])
            if p.get('_storehub_baseline_pending'):
                raise ValueError('Initialize the synced product balances in StoreHub connection first.')
            if p['Track Stock Levels'] != '1':
                raise ValueError('Requests can only contain products that track stock.')
            if line['product_id'] in seen:
                raise ValueError('Combine duplicate product lines.')
            seen.add(line['product_id'])
            items.append(dict(product_id=line['product_id'], quantity=fmt(number(line.get('quantity'), True)), cost=fmt(number(line.get('cost', p['Cost']))), received='0', name=p['Product Name'], sku=p['SKU'], category=p['Category'], unit=p['Base Unit'] or p['Unit']))
        if not items:
            raise ValueError('Add at least one product.')
        count = db.execute('SELECT COUNT(*) FROM documents WHERE kind=?', (kind,)).fetchone()[0] + 1
        did = ('LOCAL-PO' if kind == 'purchase' else 'LOCAL-ST') + f'{count:05d}'
        payload = dict(items=items, supplier=b.get('supplier', '').strip(), target=WAREHOUSE if kind == 'purchase' else b['target'], source=WAREHOUSE, requested_by=b['requested_by'].strip(), notes=b.get('notes', ''), eta=b.get('eta', ''), shipped='', received='')
        db.execute('INSERT INTO documents VALUES (?,?,?,?,?)', (did, kind, 'Open' if kind == 'purchase' else 'Created', now(), json.dumps(payload)))
    return jsonify(id=did), 201


@app.post('/api/documents/<did>/action')
def document_action(did):
    b = request.get_json()
    actor = str(b.get('actor', '')).strip()
    if not actor:
        raise ValueError('Enter the name of the person performing this action.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
        if not row:
            raise ValueError('Document not found.')
        p, action, status = json.loads(row['payload']), b.get('action'), row['status']
        if action == 'cancel' and status in ['Open', 'Created']:
            status = 'Cancelled'
            p['cancelled'] = now()
            p['cancelled_by'] = actor
        elif action == 'ship' and row['kind'] == 'transfer' and status == 'Created':
            for line in p['items']:
                change_stock(db, line['product_id'], WAREHOUSE, -Decimal(line['quantity']), did + ' shipped')
            status, p['shipped'] = 'Shipped', now()
            p['sent_by'] = actor
        elif action == 'receive' and row['kind'] == 'transfer' and status == 'Shipped':
            for line in p['items']:
                change_stock(db, line['product_id'], p['target'], Decimal(line['quantity']), did + ' received')
            status, p['received'] = 'Received', now()
            p['received_by'] = actor
        elif action == 'receive' and row['kind'] == 'purchase' and status in ['Open', 'PartiallyReceived']:
            amounts = b.get('quantities', {})
            if not amounts or set(amounts) - {line['product_id'] for line in p['items']}:
                raise ValueError('Provide receipt quantities for this purchase order.')
            added = Decimal(0)
            for line in p['items']:
                qty = number(amounts.get(line['product_id'], '0'))
                if Decimal(line['received']) + qty > Decimal(line['quantity']):
                    raise ValueError('Receipt exceeds the ordered quantity.')
                if qty:
                    change_stock(db, line['product_id'], WAREHOUSE, qty, did + ' supplier receipt')
                    line['received'] = fmt(Decimal(line['received']) + qty)
                    added += qty
            if not added:
                raise ValueError('Receive at least one unit.')
            status = 'Completed' if all(Decimal(i['received']) == Decimal(i['quantity']) for i in p['items']) else 'PartiallyReceived'
            if status == 'Completed':
                p['received'] = now()
                p['completed_by'] = actor
        else:
            raise ValueError('This action is not available in the current status.')
        db.execute('UPDATE documents SET status=?, payload=? WHERE id=?', (status, json.dumps(p), did))
    return jsonify(ok=True)


def download(headers, rows, filename):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=headers, quoting=csv.QUOTE_ALL, lineterminator='\r\n', extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return Response('\ufeff' + out.getvalue(), content_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@app.get('/exports/products.csv')
def export_products():
    with connection() as db:
        headers, _ = config(db)
        instructions = json.loads(db.execute("SELECT value FROM metadata WHERE key='instructions'").fetchone()[0])
        scope = request.args.get('scope', 'changed')
        if scope not in ('all', 'changed', 'ready'):
            raise ValueError('Choose a valid product export scope.')
        changed_only = scope == 'changed' or (scope == 'ready' and request.args.get('changed') == '1')
        query = 'SELECT fields FROM products' + (' WHERE changed=1' if changed_only else '') + ' ORDER BY rowid'
        products = [json.loads(r[0]) for r in db.execute(query)]
        incomplete = [p for p in products if p.get('_storehub_api_imported')]
        if scope == 'ready':
            products = [p for p in products if not p.get('_storehub_api_imported')]
        elif incomplete:
            # A full catalog remains downloadable without inventing fields needed for
            # StoreHub imports. Explicit report columns distinguish it from that template.
            report_headers = headers + ['CSV Export Status', 'Stock Balance Status']
            report = [dict(p, **{
                'CSV Export Status': 'REVIEW ONLY - missing StoreHub CSV fields' if p.get('_storehub_api_imported') else 'Source CSV fields available',
                'Stock Balance Status': 'Not initialized locally' if p.get('_storehub_baseline_pending') else 'Local snapshot',
            }) for p in products]
            return download(report_headers, report, 'Full_Catalog_Review.csv' if not changed_only else 'Changed_Products_Review.csv')
    return download(headers, [instructions] + products, 'Products.csv')


@app.get('/api/product-export-status')
def product_export_status():
    with connection() as db:
        products = [dict(id=r['id'], changed=bool(r['changed']), fields=json.loads(r['fields'])) for r in db.execute('SELECT * FROM products ORDER BY rowid')]
    changed_only = request.args.get('scope') == 'changed'
    selected = [p for p in products if not changed_only or p['changed']]
    missing = [p for p in selected if p['fields'].get('_storehub_api_imported')]
    return jsonify(total=len(selected), ready=len(selected)-len(missing), missing=[dict(
        id=p['id'], name=p['fields'].get('Product Name', ''), sku=p['fields'].get('SKU', ''),
        baseline_pending=bool(p['fields'].get('_storehub_baseline_pending')),
    ) for p in missing])


@app.get('/exports/documents/<did>.csv')
def export_document(did):
    with connection() as db:
        r = db.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
        if not r:
            raise ValueError('Document not found.')
        p = json.loads(r['payload'])
    purchase = r['kind'] == 'purchase'
    filename = 'Purchase_Orders_09-28-2026.csv' if purchase else 'Stock_Transfer_09-28-2026.csv'
    with open(ROOT / 'data' / filename, encoding='utf-8-sig', newline='') as f:
        headers = next(csv.reader(f))
    common = {'P.O ID' if purchase else 'S.T ID': did, 'Created Date': r['created'], 'Target Store': p['target'], 'Status': r['status']}
    if purchase:
        common.update({'Supplier': p['supplier'], 'Estimated Date of Arrival': p['eta'], 'Completion Date': p['received'], 'Notes': p['notes'], 'Requested By': p['requested_by'], 'Completed By': p.get('completed_by', '')})
    else:
        common.update({'Source Store': WAREHOUSE, 'Shipped Date': p['shipped'], 'Received Date': p['received'], 'Sent By': p.get('sent_by', ''), 'Received By': p.get('received_by', '')})
    if r['status'] == 'Cancelled':
        common.update({'Cancelled Date': p.get('cancelled', ''), 'Cancelled By': p.get('cancelled_by', '')})
    total = sum(Decimal(i['quantity']) * Decimal(i['cost']) for i in p['items'])
    rows = [dict(common, **{'Total (RM)': fmt(total.quantize(Decimal('.01')))})]
    for n, line in enumerate(p['items'], 1):
        row = dict(common, **{'No.': str(n), 'Product Name': line['name'], 'SKU': line['sku'], 'Category': line['category'], 'Unit': line['unit'], 'Cost (RM)': line['cost'], 'SubTotal (RM)': fmt((Decimal(line['quantity']) * Decimal(line['cost'])).quantize(Decimal('.01'))), 'Ordered Quantity' if purchase else 'Ordered Qty': line['quantity']})
        if purchase:
            row['Received Quantity'] = line['received'] if Decimal(line['received']) else ''
        rows.append(row)
    return download(headers, rows, did + '.csv')


integration = storehub.Integration(app, connection, config, WAREHOUSE, REMOVED_STORES)
transfer_reports = stock_transfers.StockTransfers(app, connection, config, REMOVED_STORES)
partner_tools = partner_api.PartnerAPI(integration)
wastage_records = wastage.Wastage(app, connection, config, WAREHOUSE)
wastage_csv = wastage_imports.WastageImports(app, connection, config, WAREHOUSE)
inventory_records = manual_inventory.ManualInventory(app, connection, config)
notifications.register(app, connection, integration)
initialize()
if __name__ == '__main__':
    integration.start_scheduler()
    app.run(host=os.environ.get('WAREHOUSE_HOST', '127.0.0.1'), port=5077, debug=False)
