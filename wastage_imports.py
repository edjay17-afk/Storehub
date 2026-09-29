"""Reviewed StoreHub wastage CSV imports; never adjusts stock."""
import csv
import hashlib
import io
import json
import re
import time
import uuid
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from flask import request, jsonify

HEADERS = ['W.R ID','Created Date','Store','Supplier','Product Name','SKU','Category','Quantity','Unit','Cost (PHP)','SubTotal (PHP)','Total (PHP)','Reason','Created By','Notes']
LIMIT = 5 * 1024 * 1024


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS wastage_import_sources(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, filename TEXT NOT NULL, imported TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS wastage_import_previews(token TEXT PRIMARY KEY, payload TEXT NOT NULL, filename TEXT NOT NULL, created REAL NOT NULL, result TEXT);
    ''')


def normalized(value):
    return ' '.join(value.strip().casefold().split())


def number(value, positive=False):
    try:
        n = Decimal(value)
    except InvalidOperation:
        raise ValueError('Quantity and PHP amounts must be valid numbers.') from None
    if not n.is_finite() or n < 0 or n > Decimal('1000000000000') or n.as_tuple().exponent < -6 or (positive and (n <= 0 or n > 1000000000)):
        raise ValueError('Use finite nonnegative amounts and positive quantities with up to 6 decimal places.')
    return n


class WastageImports:
    def __init__(self, app, connection, config, warehouse):
        self.db, self.config, self.warehouse = connection, config, warehouse
        app.add_url_rule('/api/wastage/import-preview', 'wastage_import_preview', self.preview, methods=['POST'])
        app.add_url_rule('/api/wastage/import', 'wastage_import_commit', self.commit, methods=['POST'])

    def guard(self):
        if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
            raise ValueError('Import wastage from the warehouse page.')
        if request.content_length and request.content_length > LIMIT + 65536:
            raise ValueError('Upload a CSV file no larger than 5 MB.')

    def parse(self, raw, db):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('Upload the UTF-8 StoreHub wastage CSV.') from None
        if '\x00' in text:
            raise ValueError('Upload a valid CSV.')
        groups = {}
        try:
            reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
            if not reader.fieldnames or len(reader.fieldnames) != len(HEADERS) or set(reader.fieldnames) != set(HEADERS):
                raise ValueError('Use StoreHub Wastage Records CSV with Include item details enabled and the original PHP columns.')
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError('The CSV has a row with the wrong number of columns.')
                if not any(v.strip() for v in row.values()):
                    continue
                if any(len(v) > 2000 for v in row.values()):
                    raise ValueError('CSV fields must be 2,000 characters or fewer.')
                ref = row['W.R ID'].strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ref):
                    raise ValueError('The CSV contains an invalid wastage record ID.')
                groups.setdefault(ref, []).append({h:row[h].strip() for h in HEADERS})
                if reader.line_num > 10001 or len(groups) > 1000:
                    raise ValueError('Import up to 1,000 wastage records or 10,000 rows.')
        except csv.Error:
            raise ValueError('The CSV contains malformed quoted fields.') from None
        if not groups:
            raise ValueError('The CSV has no wastage records.')
        branches = {normalized(b):b for b in self.config(db)[1] if b != self.warehouse}
        products = [(r['id'], json.loads(r['fields'])) for r in db.execute('SELECT id,fields FROM products')]
        records, skipped = [], []
        for ref, rows in groups.items():
            summaries = [r for r in rows if not r['Product Name']]
            if len(summaries) != 1:
                raise ValueError(f'{ref}: expected one summary row. Export with Include item details enabled.')
            summary = summaries[0]
            branch = branches.get(normalized(summary['Store']))
            if not branch:
                skipped.append(ref)
                continue
            try:
                created = datetime.strptime(summary['Created Date'], '%m/%d/%Y %H:%M')
            except ValueError:
                raise ValueError(f'{ref}: use the original StoreHub Created Date format.') from None
            if created.date() > date.today():
                raise ValueError(f'{ref}: the wastage date cannot be in the future.')
            items = []
            for row in rows:
                if row is summary:
                    continue
                if row['Store'] != summary['Store'] or row['Created Date'] != summary['Created Date']:
                    raise ValueError(f'{ref}: item date or store differs from its summary.')
                candidates = [(pid,p) for pid,p in products if normalized(p.get('SKU','')) == normalized(row['SKU'])] if row['SKU'] else [(pid,p) for pid,p in products if normalized(p.get('Product Name','')) == normalized(row['Product Name'])]
                if len(candidates) != 1:
                    raise ValueError(f'{ref}: "{row["Product Name"]}" cannot be uniquely matched. Add its unique catalog SKU to the CSV and preview again.')
                pid, product = candidates[0]
                qty = number(row['Quantity'], True)
                cost, subtotal = number(row['Cost (PHP)']), number(row['SubTotal (PHP)'])
                items.append(dict(product_id=pid, product_name=row['Product Name'], sku=row['SKU'], quantity=str(qty), cost=str(cost), subtotal=str(subtotal), reason=row['Reason'], unit=row['Unit'], created_by=row['Created By'] or summary['Created By'], notes=row['Notes'], product_signature=[product.get('Product Name',''),product.get('SKU','')]))
            if not items:
                raise ValueError(f'{ref}: no product lines. Download again with Include item details enabled.')
            total = number(summary['Total (PHP)'])
            if abs(sum(Decimal(i['subtotal']) for i in items) - total) > Decimal('0.01'):
                raise ValueError(f'{ref}: item subtotals do not match the record total.')
            fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            source_id = branch + ':' + ref
            old = db.execute('SELECT fingerprint FROM wastage_import_sources WHERE id=?', (source_id,)).fetchone()
            if old and old[0] != fingerprint:
                raise ValueError(f'{ref} was already imported with different source data. Review its existing records before importing a changed export.')
            records.append(dict(id=ref, source_id=source_id, fingerprint=fingerprint, branch=branch, created=created.isoformat(), notes=summary['Notes'], total=str(total), items=items, rows=rows, change='unchanged' if old else 'new'))
        return dict(records=records, skipped=skipped)

    def preview(self):
        self.guard()
        file = request.files.get('file')
        if not file or not (file.filename or '').lower().endswith('.csv'):
            raise ValueError('Choose a StoreHub wastage CSV file.')
        raw = file.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('Upload a CSV file no larger than 5 MB.')
        filename = file.filename.replace('\\','/').rsplit('/',1)[-1][:200]
        with self.db() as db:
            payload = self.parse(raw, db)
            token = uuid.uuid4().hex
            db.execute('DELETE FROM wastage_import_previews WHERE created<?', (time.time()-86400,))
            db.execute('INSERT INTO wastage_import_previews VALUES (?,?,?,?,NULL)', (token,json.dumps(payload),filename,time.time()))
        return jsonify(token=token,filename=filename,**payload)

    def commit(self):
        self.guard()
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or not re.fullmatch(r'[a-f0-9]{32}', str(body.get('token',''))):
            raise ValueError('Preview the CSV before importing.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = db.execute('SELECT * FROM wastage_import_previews WHERE token=?', (body['token'],)).fetchone()
            if not batch or time.time()-batch['created'] > 1800:
                raise ValueError('The preview expired. Preview the CSV again.')
            if batch['result']:
                return jsonify(**json.loads(batch['result']), repeated=True)
            payload, imported, count, unchanged = json.loads(batch['payload']), datetime.now().isoformat(timespec='microseconds'), 0, 0
            branches = self.config(db)[1]
            for record in payload['records']:
                old = db.execute('SELECT fingerprint FROM wastage_import_sources WHERE id=?', (record['source_id'],)).fetchone()
                if old:
                    if old[0] != record['fingerprint']:
                        raise ValueError('Source data changed after preview. Preview again.')
                    unchanged += 1
                    continue
                if record['branch'] not in branches or record['branch'] == self.warehouse:
                    raise ValueError('The branch is no longer available. Preview again.')
                for index, item in enumerate(record['items']):
                    current = db.execute('SELECT fields FROM products WHERE id=?', (item['product_id'],)).fetchone()
                    if not current or [json.loads(current[0]).get(k,'') for k in ['Product Name','SKU']] != item['product_signature']:
                        raise ValueError('A matched product changed. Preview again.')
                    source = dict(record_id=record['id'], filename=batch['filename'], imported=imported, original_created=record['created'], created_by=item['created_by'], reason=item['reason'], unit_cost=item['cost'], subtotal=item['subtotal'], total=record['total'], unit=item['unit'])
                    notes = '\n'.join(v for v in [record['notes'], item['notes']] if v)
                    if len(notes) > 2000:
                        raise ValueError('Combined record/item notes must be 2,000 characters or fewer.')
                    db.execute('''INSERT INTO wastage_records(id,submission_id,waste_date,branch,product_id,product_name,sku,quantity,notes,created,source)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (str(uuid.uuid4()), str(uuid.uuid5(uuid.NAMESPACE_URL, record['source_id']+':'+str(index))),record['created'][:10],record['branch'],item['product_id'],item['product_name'],item['sku'],item['quantity'],notes,imported,json.dumps(source)))
                    count += 1
                db.execute('INSERT INTO wastage_import_sources VALUES (?,?,?,?,?)', (record['source_id'],record['fingerprint'],json.dumps(record),batch['filename'],imported))
            result = dict(ok=True, imported_lines=count, unchanged_records=unchanged, skipped_records=len(payload['skipped']))
            db.execute('UPDATE wastage_import_previews SET result=? WHERE token=?', (json.dumps(result), body['token']))
        return jsonify(result)
