"""StoreHub stock-transfer reports, stored separately from the stock ledger."""
import csv
import hashlib
import io
import json
import re
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from flask import jsonify, request, Response

HEADERS = ['S.T ID','Created Date','Shipped Date','Received Date','Source Store','Target Store','No.',
           'Product Name','SKU','Serial No.','Category','Ordered Qty','Unit','Cost (RM)','SubTotal (RM)',
           'Total (RM)','Status','Sent By','Cancelled By','Cancelled Date','Received By']
MAX_BYTES = 5 * 1024 * 1024
COMMON = ['Created Date','Shipped Date','Received Date','Source Store','Target Store','Status',
          'Sent By','Cancelled By','Cancelled Date','Received By']

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS imported_transfers(id TEXT PRIMARY KEY, payload TEXT NOT NULL,
        fingerprint TEXT NOT NULL, imported TEXT NOT NULL, filename TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS transfer_imports(token TEXT PRIMARY KEY, payload TEXT NOT NULL,
        created REAL NOT NULL, filename TEXT NOT NULL, result TEXT);
    ''')

def normalized(value):
    return re.sub(r'[^a-z0-9]', '', value.casefold())

def date_value(value, required=False):
    if not value and not required:
        return ''
    for fmt in ['%m/%d/%Y %H:%M','%m/%d/%Y %H:%M:%S','%m/%d/%Y']:
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            pass
    raise ValueError('Use StoreHub export dates in MM/DD/YYYY format, with an optional time.')

def amount(value):
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError('Quantity and cost columns must contain valid numbers.') from None
    if not number.is_finite() or number < 0:
        raise ValueError('Quantity and cost columns must be finite and zero or greater.')
    return number

def fingerprint(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()

class StockTransfers:
    def __init__(self, app, connection, config, removed):
        self.db, self.config, self.removed = connection, config, removed
        for path, method, fn in [('', 'GET', self.list),('/preview','POST',self.preview),
                                 ('/import','POST',self.commit),('/<transfer_id>','GET',self.detail),
                                 ('/<transfer_id>.csv','GET',self.export)]:
            app.add_url_rule('/api/imported-transfers'+path,'imported_transfers'+(path or '/list'),fn,methods=[method])

    def origin(self):
        if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
            raise ValueError('Open the import directly in the warehouse app.')

    def parse(self, raw, locations):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('Upload the UTF-8 CSV exported by StoreHub.') from None
        if '\x00' in text:
            raise ValueError('The file is not a valid StoreHub CSV.')
        try:
            reader = csv.DictReader(io.StringIO(text,newline=''), strict=True)
            if not reader.fieldnames or len(reader.fieldnames)!=len(HEADERS) or set(reader.fieldnames)!=set(HEADERS):
                raise ValueError('Upload a Stock Transfer export with the original 21 StoreHub columns, including S.T ID and Ordered Qty.')
            groups = {}
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'CSV row {reader.line_num} has the wrong number of columns.')
                if not any(v.strip() for v in row.values()):
                    continue
                row = {h:row[h] for h in HEADERS}
                reference = row['S.T ID'].strip().upper()
                if not re.fullmatch(r'[A-Z0-9_-]{1,64}',reference):
                    raise ValueError(f'CSV row {reader.line_num} has an invalid transfer ID.')
                row['S.T ID'] = reference
                groups.setdefault(reference,[]).append(row)
                if len(groups)>1000 or reader.line_num>50001:
                    raise ValueError('Import up to 1,000 transfers or 50,000 rows per file.')
        except csv.Error:
            raise ValueError('The CSV contains malformed quoted fields. Export it again from StoreHub.') from None
        if not groups:
            raise ValueError('The CSV contains no stock transfers.')
        available = {normalized(loc):loc for loc in locations}
        orders, skipped = [], []
        for reference, rows in groups.items():
            summaries = [r for r in rows if not r['No.'].strip()]
            if len(summaries)!=1:
                raise ValueError(f'{reference}: expected exactly one transfer summary row.')
            summary = summaries[0]
            source, target = summary['Source Store'].strip(),summary['Target Store'].strip()
            if any(s in self.removed or re.match(r'^688(?:\s|[-_/]|$)',s) for s in [source,target]):
                skipped.append(reference)
                continue
            if normalized(source) not in available or normalized(target) not in available or normalized(source)==normalized(target):
                raise ValueError(f'{reference}: source and target must be different active locations. Store 688 is excluded.')
            created = date_value(summary['Created Date'],True)
            for field in ['Shipped Date','Received Date','Cancelled Date']:
                date_value(summary[field])
            if not summary['Status'].strip():
                raise ValueError(f'{reference}: status is missing.')
            if any(summary[h].strip() for h in ['Product Name','SKU','Serial No.','Category','Ordered Qty','Unit','Cost (RM)','SubTotal (RM)']):
                raise ValueError(f'{reference}: the summary row contains product-line fields.')
            if summary['Total (RM)'].strip():
                amount(summary['Total (RM)'])
            items, numbers = [], set()
            for row in rows:
                if row is summary:
                    continue
                if any(row[h].strip()!=summary[h].strip() for h in COMMON):
                    raise ValueError(f'{reference}: product rows disagree with their transfer summary.')
                line = row['No.'].strip()
                if not line.isdecimal() or int(line)<1 or int(line) in numbers:
                    raise ValueError(f'{reference}: product line numbers must be unique positive integers.')
                numbers.add(int(line))
                if not row['Product Name'].strip() or not row['Ordered Qty'].strip():
                    raise ValueError(f'{reference}: each product line needs a name and quantity.')
                amount(row['Ordered Qty'])
                for field in ['Cost (RM)','SubTotal (RM)','Total (RM)']:
                    if row[field].strip(): amount(row[field])
                items.append(row)
            # Preserve the exported values, including blank units and costs; no product matching or unit conversion.
            items.sort(key=lambda r:int(r['No.']))
            ordered = [summary]+items
            orders.append(dict(id=reference, created=created, source=available[normalized(source)],
                               target=available[normalized(target)], status=summary['Status'].strip(),
                               total=summary['Total (RM)'], rows=ordered, fingerprint=fingerprint(ordered)))
        return dict(orders=orders,skipped=skipped)

    def classify(self, db, orders):
        counts = dict(new=0,updated=0,unchanged=0)
        classified=[]
        for order in orders:
            old = db.execute('SELECT payload,fingerprint FROM imported_transfers WHERE id=?',(order['id'],)).fetchone()
            if old:
                previous=json.loads(old['payload'])
                if any(previous[k]!=order[k] for k in ['created','source','target']):
                    raise ValueError(f"{order['id']}: this ID already belongs to a different transfer. Existing records were preserved.")
            change = 'new' if not old else 'unchanged' if old['fingerprint']==order['fingerprint'] else 'updated'
            counts[change]+=1
            classified.append(dict(order,change=change,previousStatus=json.loads(old['payload'])['status'] if old else ''))
        return counts, classified

    def preview(self):
        self.origin()
        if request.content_length and request.content_length>MAX_BYTES+65536:
            raise ValueError('Upload a CSV file no larger than 5 MB.')
        file=request.files.get('file')
        if not file or not (file.filename or '').lower().endswith('.csv'):
            raise ValueError('Choose a StoreHub Stock Transfer CSV file.')
        raw=file.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES: raise ValueError('Upload a CSV file no larger than 5 MB.')
        filename=file.filename.replace('\\','/').rsplit('/',1)[-1][:200]
        with self.db() as db:
            payload=self.parse(raw,self.config(db)[1])
            counts,orders=self.classify(db,payload['orders'])
            payload['expected']={o['id']:(db.execute('SELECT fingerprint FROM imported_transfers WHERE id=?',(o['id'],)).fetchone() or [None])[0] for o in orders}
            token=uuid.uuid4().hex
            db.execute('DELETE FROM transfer_imports WHERE created<?',(time.time()-86400,))
            db.execute('INSERT INTO transfer_imports VALUES (?,?,?,?,NULL)',(token,json.dumps(payload),time.time(),filename))
        return jsonify(token=token,filename=filename,counts=counts,orders=orders,skipped=payload['skipped'])

    def commit(self):
        self.origin()
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or not re.fullmatch(r'[a-f0-9]{32}',str(body.get('token',''))):
            raise ValueError('Preview the CSV before importing it.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            batch=db.execute('SELECT * FROM transfer_imports WHERE token=?',(body['token'],)).fetchone()
            if not batch or time.time()-batch['created']>1800:
                raise ValueError('The preview expired. Preview the CSV again.')
            if batch['result']:
                return jsonify(json.loads(batch['result']))
            payload=json.loads(batch['payload'])
            for order in payload['orders']:
                current=db.execute('SELECT fingerprint FROM imported_transfers WHERE id=?',(order['id'],)).fetchone()
                if (current[0] if current else None)!=payload['expected'][order['id']]:
                    raise ValueError('A transfer changed after this preview. Preview the CSV again before importing.')
            counts,orders=self.classify(db,payload['orders'])
            imported=datetime.now(timezone.utc).isoformat()
            for order in orders:
                if order['change']=='unchanged': continue
                saved={k:v for k,v in order.items() if k not in ['change','previousStatus']}
                db.execute('INSERT INTO imported_transfers VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,fingerprint=excluded.fingerprint,imported=excluded.imported,filename=excluded.filename',
                           (order['id'],json.dumps(saved),order['fingerprint'],imported,batch['filename']))
            result=dict(ok=True,counts=counts,skipped=len(payload['skipped']),imported=imported)
            db.execute('UPDATE transfer_imports SET result=? WHERE token=?',(json.dumps(result),body['token']))
        return jsonify(result)

    def record(self, row):
        return dict(json.loads(row['payload']),imported=row['imported'],filename=row['filename'])

    def list(self):
        try:
            page=int(request.args.get('page',1));size=int(request.args.get('size',20))
        except ValueError: raise ValueError('Choose a valid transfer page.') from None
        if page<1 or size not in [10,20,50]: raise ValueError('Choose a valid transfer page size.')
        start,end=request.args.get('from',''),request.args.get('to','')
        try:
            if start: date.fromisoformat(start)
            if end: date.fromisoformat(end)
        except ValueError: raise ValueError('Choose valid transfer creation dates.') from None
        if start and end and start>end: raise ValueError('From must be before To.')
        q=request.args.get('q','').strip().casefold();status=request.args.get('status','');location=request.args.get('location','')
        with self.db() as db:
            locations=self.config(db)[1]
            records=[self.record(r) for r in db.execute('SELECT * FROM imported_transfers')]
        records=[o for o in records if o['source'] in locations and o['target'] in locations]
        statuses=sorted(set(o['status'] for o in records))
        filtered=[]
        for o in records:
            day=o['created'][:10]
            if (start and day<start) or (end and day>end) or (status and o['status']!=status) or (location and location not in [o['source'],o['target']]):continue
            search=[o['id'],o['source'],o['target'],o['status']]+[r[h] for r in o['rows'][1:] for h in ['Product Name','SKU']]
            if q and not any(q in s.casefold() for s in search):continue
            filtered.append(o)
        filtered.sort(key=lambda o:(o['created'],o['id']),reverse=True)
        pages=max(1,(len(filtered)+size-1)//size);page=min(page,pages)
        summaries=[dict((k,v) for k,v in o.items() if k not in ['rows','fingerprint'])|{'lines':len(o['rows'])-1} for o in filtered[(page-1)*size:page*size]]
        return jsonify(orders=summaries,total=len(filtered),allTotal=len(records),page=page,pages=pages,statuses=statuses,locations=locations)

    def get(self, transfer_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM imported_transfers WHERE id=?',(transfer_id,)).fetchone()
            if not row: raise ValueError('Imported transfer not found.')
            record=self.record(row)
            if record['source'] not in self.config(db)[1] or record['target'] not in self.config(db)[1]:
                raise ValueError('This transfer is not in your active locations.')
            return record

    def detail(self,transfer_id):
        return jsonify(self.get(transfer_id))

    def export(self,transfer_id):
        record=self.get(transfer_id)
        output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=HEADERS,quoting=csv.QUOTE_ALL,lineterminator='\r\n')
        writer.writeheader();writer.writerows(record['rows'])
        return Response('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="StoreHub-{record["id"]}.csv"'})
