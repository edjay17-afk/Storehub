"""Branch batch counts and expiry dates, independent of stock movements."""
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import jsonify, request


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS manual_inventory(
        id TEXT PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE,
        branch TEXT NOT NULL, product_id TEXT NOT NULL REFERENCES products(id),
        product_name TEXT NOT NULL, sku TEXT NOT NULL,
        count_date TEXT NOT NULL, counted_by TEXT NOT NULL, quantity TEXT NOT NULL,
        batch TEXT NOT NULL, expiry_date TEXT NOT NULL, notes TEXT NOT NULL,
        created TEXT NOT NULL, updated_at TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0, deleted_at TEXT)''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS inventory_active_batch
        ON manual_inventory(branch,product_id,batch,expiry_date) WHERE deleted_at IS NULL''')
    db.execute('''CREATE TABLE IF NOT EXISTS manual_inventory_history(
        id INTEGER PRIMARY KEY, record_id TEXT NOT NULL REFERENCES manual_inventory(id),
        action TEXT NOT NULL, saved_at TEXT NOT NULL, payload TEXT NOT NULL)''')


def expiry_status(expiry, quantity, today=None):
    if not expiry:
        return 'No expiry'
    if Decimal(quantity) == 0:
        return 'Empty batch'
    remaining = (date.fromisoformat(expiry) - (today or date.today())).days
    return 'Expired' if remaining < 0 else 'Expiring soon' if remaining <= 30 else 'Fresh'


class ManualInventory:
    def __init__(self, app, connection, config):
        self.connection, self.config = connection, config
        app.add_url_rule('/api/manual-inventory', 'inventory_list', self.list_records)
        app.add_url_rule('/api/manual-inventory', 'inventory_save', self.save, methods=['POST'])
        for action in ('edit', 'delete', 'restore'):
            app.add_url_rule('/api/manual-inventory/<record_id>/' + action,
                             'inventory_' + action, getattr(self, action), methods=['POST'])

    def guard(self):
        if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
            return jsonify(error='Save inventory from the warehouse page.'), 403
        if request.content_length and request.content_length > 16384:
            return jsonify(error='Inventory entry is too large.'), 413

    def fields(self, db, payload):
        branch = str(payload.get('branch', '')).strip()
        if branch not in self.config(db)[1]:
            raise ValueError('Choose an available branch or warehouse.')
        product_id = str(payload.get('product_id', ''))
        row = db.execute('SELECT fields FROM products WHERE id=?', (product_id,)).fetchone()
        if not row:
            raise ValueError('Choose a product from the search results.')
        product = json.loads(row['fields'])
        try:
            count_date = date.fromisoformat(str(payload.get('count_date', '')))
        except ValueError:
            raise ValueError('Choose a valid count date.')
        if count_date > date.today():
            raise ValueError('Count date cannot be in the future.')
        expiry = str(payload.get('expiry_date') or '').strip()
        if expiry:
            try:
                expiry = date.fromisoformat(expiry).isoformat()
            except ValueError:
                raise ValueError('Choose a valid expiration date.')
        try:
            quantity = Decimal(str(payload.get('quantity', '')))
        except InvalidOperation:
            raise ValueError('Enter a valid counted quantity.')
        if not quantity.is_finite() or quantity < 0 or quantity > 1_000_000_000 or quantity.as_tuple().exponent < -6:
            raise ValueError('Quantity must be zero or more, up to 1 billion units and 6 decimal places.')
        text = {}
        for field, maximum in [('counted_by', 120), ('batch', 120), ('notes', 2000)]:
            text[field] = str(payload.get(field) or '').strip()
            if len(text[field]) > maximum:
                raise ValueError(f'{field.replace("_", " ").title()} must be {maximum} characters or fewer.')
        if not text['counted_by']:
            raise ValueError('Enter the name of the person who counted the stock.')
        return dict(branch=branch, product_id=product_id, product_name=product.get('Product Name', ''),
                    sku=product.get('SKU', ''), count_date=count_date.isoformat(),
                    expiry_date=expiry, quantity=format(quantity, 'f'), **text)

    @staticmethod
    def duplicate(db, fields, record_id=''):
        return db.execute('''SELECT id FROM manual_inventory WHERE branch=? AND product_id=?
            AND batch=? AND expiry_date=? AND deleted_at IS NULL AND id<>?''',
            (fields['branch'], fields['product_id'], fields['batch'], fields['expiry_date'], record_id)).fetchone()

    @staticmethod
    def audit(db, record_id, action):
        record = dict(db.execute('SELECT * FROM manual_inventory WHERE id=?', (record_id,)).fetchone())
        db.execute('INSERT INTO manual_inventory_history(record_id,action,saved_at,payload) VALUES(?,?,?,?)',
                   (record_id, action, datetime.now().isoformat(timespec='microseconds'), json.dumps(record)))

    def list_records(self):
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            raise ValueError('Choose a valid page number.')
        today = date.today()
        clauses = ['deleted_at IS NOT NULL' if request.args.get('deleted') == '1' else 'deleted_at IS NULL']
        values = []
        if request.args.get('branch'):
            clauses.append('branch=?')
            values.append(request.args['branch'])
        if request.args.get('search'):
            clauses.append('(instr(lower(product_name),lower(?))>0 OR instr(lower(sku),lower(?))>0 OR instr(lower(batch),lower(?))>0)')
            values.extend([request.args['search']] * 3)
        status = request.args.get('status', '')
        if status == 'expired':
            clauses.append("expiry_date<>'' AND expiry_date<? AND CAST(quantity AS REAL)>0")
            values.append(today.isoformat())
        elif status == 'soon':
            clauses.append('expiry_date BETWEEN ? AND ? AND CAST(quantity AS REAL)>0')
            values.extend([today.isoformat(), (today + timedelta(days=30)).isoformat()])
        elif status == 'none':
            clauses.append("expiry_date=''")
        elif status:
            raise ValueError('Choose a valid expiry filter.')
        where = ' WHERE ' + ' AND '.join(clauses)
        with self.connection() as db:
            branches = self.config(db)[1]
            total = db.execute('SELECT COUNT(*) FROM manual_inventory' + where, values).fetchone()[0]
            pages = max(1, (total + 19) // 20)
            page = min(page, pages)
            records = [dict(r) for r in db.execute('SELECT * FROM manual_inventory' + where +
                " ORDER BY CASE WHEN expiry_date='' OR CAST(quantity AS REAL)=0 THEN 1 ELSE 0 END,expiry_date,count_date DESC,created DESC,id LIMIT 20 OFFSET ?", values + [(page-1)*20])]
            summary = dict(total=0, expired=0, soon=0)
            for row in db.execute('SELECT expiry_date,quantity FROM manual_inventory WHERE deleted_at IS NULL' +
                                  (' AND branch=?' if request.args.get('branch') else ''),
                                  [request.args['branch']] if request.args.get('branch') else []):
                summary['total'] += 1
                label = expiry_status(row['expiry_date'], row['quantity'], today)
                summary['expired'] += label == 'Expired'
                summary['soon'] += label == 'Expiring soon'
        for row in records:
            row['expiry_status'] = expiry_status(row['expiry_date'], row['quantity'], today)
        return jsonify(records=records, total=total, page=page, pages=pages, branches=branches,
                       summary=summary, today=today.isoformat(), submission_id=str(uuid.uuid4()))

    def save(self):
        if (blocked := self.guard()):
            return blocked
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ValueError('Provide an inventory entry.')
        try:
            token = str(uuid.UUID(str(payload.get('submission_id', ''))))
        except ValueError:
            raise ValueError('Reopen the inventory form before saving.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id,deleted_at FROM manual_inventory WHERE submission_id=?', (token,)).fetchone()
            if existing:
                if existing['deleted_at']:
                    return jsonify(error='This entry was deleted. Reopen the form to add a new batch.'), 409
                return jsonify(id=existing['id'], repeated=True)
            fields = self.fields(db, payload)
            if self.duplicate(db, fields):
                return jsonify(error='This batch and expiry already exist for this product at this branch. Edit that record to recount it.'), 409
            now = datetime.now().isoformat(timespec='microseconds')
            record_id = str(uuid.uuid4())
            fields.update(id=record_id, submission_id=token, created=now, updated_at=now)
            keys = list(fields)
            db.execute('INSERT INTO manual_inventory(' + ','.join(keys) + ') VALUES(' + ','.join('?' for _ in keys) + ')', list(fields.values()))
            self.audit(db, record_id, 'create')
        return jsonify(id=record_id, repeated=False), 201

    def mutate(self, record_id, action):
        if (blocked := self.guard()):
            return blocked
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ValueError('Provide an inventory entry.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM manual_inventory WHERE id=?', (record_id,)).fetchone()
            if not row:
                return jsonify(error='Inventory record not found.'), 404
            if not isinstance(payload.get('revision'), int) or isinstance(payload['revision'], bool) or payload['revision'] != row['revision']:
                return jsonify(error='This record changed. Reload the inventory page before trying again.'), 409
            if (action == 'restore') != bool(row['deleted_at']):
                return jsonify(error='This record is already deleted or restored. Reload the page.'), 409
            fields = self.fields(db, payload) if action == 'edit' else {}
            if action in ('edit', 'restore') and self.duplicate(db, fields or row, record_id):
                return jsonify(error='An active entry already exists for this batch and expiry. Edit that record instead.'), 409
            now = datetime.now().isoformat(timespec='microseconds')
            fields.update(updated_at=now, revision=row['revision']+1)
            if action != 'edit':
                fields['deleted_at'] = now if action == 'delete' else None
            db.execute('UPDATE manual_inventory SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', list(fields.values()) + [record_id])
            self.audit(db, record_id, action)
        return jsonify(id=record_id)

    def edit(self, record_id):
        return self.mutate(record_id, 'edit')

    def delete(self, record_id):
        return self.mutate(record_id, 'delete')

    def restore(self, record_id):
        return self.mutate(record_id, 'restore')
