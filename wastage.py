"""Local wastage records with validated photographic evidence stored in SQLite."""
import io
import json
import uuid
import warnings
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import jsonify, request, Response
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PHOTO = 10 * 1024 * 1024


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS wastage_records(
        id TEXT PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE,
        waste_date TEXT NOT NULL, branch TEXT NOT NULL, product_id TEXT NOT NULL,
        product_name TEXT NOT NULL, sku TEXT NOT NULL, quantity TEXT NOT NULL,
        notes TEXT NOT NULL, created TEXT NOT NULL, photo BLOB, photo_mime TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id))''')
    db.execute('CREATE INDEX IF NOT EXISTS wastage_date ON wastage_records(waste_date DESC, created DESC)')
    columns = {r[1] for r in db.execute('PRAGMA table_info(wastage_records)')}
    for name, definition in [('revision', 'INTEGER NOT NULL DEFAULT 0'), ('updated_at', 'TEXT'), ('deleted_at', 'TEXT'), ('source', "TEXT NOT NULL DEFAULT '{}'")]:
        if name not in columns:
            db.execute(f'ALTER TABLE wastage_records ADD COLUMN {name} {definition}')


def photo_bytes(upload):
    if not upload or not upload.filename:
        return None
    raw = upload.stream.read(MAX_PHOTO + 1)
    if len(raw) > MAX_PHOTO:
        raise ValueError('Photo must be 10 MB or smaller.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in ('JPEG', 'PNG', 'WEBP') or source.width * source.height > 25_000_000:
                    raise ValueError('Use a JPEG, PNG or WebP photo up to 25 megapixels.')
                source.load()
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((2000, 2000))
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=88)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError('This file is not a readable photo. Use JPEG, PNG or WebP.')


class Wastage:
    def __init__(self, app, connection, config, warehouse):
        self.connection, self.config, self.warehouse = connection, config, warehouse
        app.add_url_rule('/api/wastage', 'wastage_list', self.list_records, methods=['GET'])
        app.add_url_rule('/api/wastage', 'wastage_save', self.save, methods=['POST'])
        app.add_url_rule('/api/wastage/<record_id>/photo', 'wastage_photo', self.photo, methods=['GET'])
        app.add_url_rule('/api/wastage/<record_id>/edit', 'wastage_edit', self.edit, methods=['POST'])
        app.add_url_rule('/api/wastage/<record_id>/delete', 'wastage_delete', self.delete, methods=['POST'])
        app.add_url_rule('/api/wastage/<record_id>/restore', 'wastage_restore', self.restore, methods=['POST'])

    def list_records(self):
        try:
            page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            raise ValueError('Enter a valid page number.')
        deleted = request.args.get('deleted') == '1'
        clauses, values = ['deleted_at IS NOT NULL' if deleted else 'deleted_at IS NULL'], []
        for field in ('branch', 'waste_date'):
            value = request.args.get(field, '').strip()
            if value:
                clauses.append(field + '=?')
                values.append(value)
        query = request.args.get('search', '').strip()
        if query:
            clauses.append('(instr(lower(product_name),lower(?))>0 OR instr(lower(sku),lower(?))>0)')
            values.extend([query, query])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connection() as db:
            branches = [l for l in self.config(db)[1] if l != self.warehouse]
            total = db.execute('SELECT count(*) FROM wastage_records' + where, values).fetchone()[0]
            pages = max(1, (total + 19) // 20)
            page = min(page, pages)
            records = [dict(r) for r in db.execute('''SELECT id,waste_date,branch,product_id,
                product_name,sku,quantity,notes,created,revision,updated_at,deleted_at,source,photo IS NOT NULL AS has_photo
                FROM wastage_records''' + where + ' ORDER BY waste_date DESC,created DESC,id DESC LIMIT 20 OFFSET ?', values + [(page-1)*20])]
        for record in records:
            record['source'] = json.loads(record['source'])
        return jsonify(records=records, total=total, page=page, pages=pages,
                       branches=branches, submission_id=str(uuid.uuid4()), today=date.today().isoformat())

    def save(self):
        # Browser forms are same-origin. Reject requests initiated by other websites.
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return jsonify(error='Save wastage from the warehouse page.'), 403
        if request.content_length and request.content_length > MAX_PHOTO + 65536:
            return jsonify(error='Photo must be 10 MB or smaller.'), 413
        form = request.form
        try:
            token = str(uuid.UUID(form.get('submission_id', '')))
        except (ValueError, AttributeError):
            raise ValueError('Reload the wastage page before saving.')
        with self.connection() as db:
            existing = db.execute('SELECT id,deleted_at FROM wastage_records WHERE submission_id=?', (token,)).fetchone()
            if existing:
                if existing['deleted_at']:
                    return jsonify(error='This submission was deleted. Reload the page to create a new record.'), 409
                return jsonify(id=existing['id'], repeated=True)
        try:
            waste_date = date.fromisoformat(form.get('waste_date', ''))
        except ValueError:
            raise ValueError('Select a valid wastage date.')
        if waste_date > date.today():
            raise ValueError('Wastage date cannot be in the future.')
        try:
            qty = Decimal(form.get('quantity', ''))
        except InvalidOperation:
            raise ValueError('Enter a valid wasted quantity.')
        if not qty.is_finite() or qty <= 0 or qty > Decimal('1000000000') or qty.as_tuple().exponent < -6:
            raise ValueError('Quantity must be positive, with up to 6 decimal places and at most 1 billion units.')
        notes = form.get('notes', '').strip()
        if len(notes) > 2000:
            raise ValueError('Notes must be 2,000 characters or fewer.')
        photo = photo_bytes(request.files.get('photo'))
        record_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id,deleted_at FROM wastage_records WHERE submission_id=?', (token,)).fetchone()
            if existing:
                if existing['deleted_at']:
                    return jsonify(error='This submission was deleted. Reload the page to create a new record.'), 409
                return jsonify(id=existing['id'], repeated=True)
            branch = form.get('branch', '')
            if branch not in self.config(db)[1] or branch == self.warehouse:
                raise ValueError('Select an available store branch.')
            row = db.execute('SELECT fields FROM products WHERE id=?', (form.get('product_id', ''),)).fetchone()
            if not row:
                raise ValueError('Select a product from the search results.')
            product = json.loads(row['fields'])
            db.execute('''INSERT INTO wastage_records
                       (id,submission_id,waste_date,branch,product_id,product_name,sku,quantity,notes,created,photo,photo_mime)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (record_id, token, waste_date.isoformat(), branch, form['product_id'],
                        product.get('Product Name', ''), product.get('SKU', ''), format(qty, 'f'), notes,
                        datetime.now().isoformat(timespec='microseconds'), photo, 'image/jpeg' if photo else None))
        return jsonify(id=record_id, repeated=False), 201

    def mutation_guard(self):
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return jsonify(error='Change wastage from the warehouse page.'), 403
        if request.content_length and request.content_length > MAX_PHOTO + 65536:
            return jsonify(error='Photo must be 10 MB or smaller.'), 413

    def current(self, db, record_id, body, deleted=False):
        row = db.execute('SELECT * FROM wastage_records WHERE id=?', (record_id,)).fetchone()
        if not row or bool(row['deleted_at']) != deleted:
            return None, (jsonify(error='Wastage record was not found. Refresh the records.'), 404)
        if str(body.get('revision', '')) != str(row['revision']):
            return None, (jsonify(error='This record changed in another session. Refresh the records before trying again.'), 409)
        return row, None

    def edit(self, record_id):
        blocked = self.mutation_guard()
        if blocked:
            return blocked
        form = request.form
        try:
            waste_date = date.fromisoformat(form.get('waste_date', ''))
        except ValueError:
            raise ValueError('Select a valid wastage date.')
        if waste_date > date.today():
            raise ValueError('Wastage date cannot be in the future.')
        try:
            qty = Decimal(form.get('quantity', ''))
        except InvalidOperation:
            raise ValueError('Enter a valid wasted quantity.')
        if not qty.is_finite() or qty <= 0 or qty > Decimal('1000000000') or qty.as_tuple().exponent < -6:
            raise ValueError('Quantity must be positive, with up to 6 decimal places and at most 1 billion units.')
        notes = form.get('notes', '').strip()
        if len(notes) > 2000:
            raise ValueError('Notes must be 2,000 characters or fewer.')
        action = form.get('photo_action', 'keep')
        if action not in ('keep', 'remove', 'replace'):
            raise ValueError('Choose a valid photo action.')
        photo = photo_bytes(request.files.get('photo')) if action == 'replace' else None
        if action == 'replace' and photo is None:
            raise ValueError('Select a replacement photo.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row, error = self.current(db, record_id, form)
            if error:
                return error
            branch = form.get('branch', '')
            if branch not in self.config(db)[1] or branch == self.warehouse:
                raise ValueError('Select an available store branch.')
            product_row = db.execute('SELECT fields FROM products WHERE id=?', (form.get('product_id', ''),)).fetchone()
            if not product_row:
                raise ValueError('Select a product from the search results.')
            product = json.loads(product_row['fields'])
            if action == 'keep':
                photo = row['photo']
            db.execute('''UPDATE wastage_records SET waste_date=?,branch=?,product_id=?,product_name=?,sku=?,
                       quantity=?,notes=?,photo=?,photo_mime=?,updated_at=?,revision=revision+1 WHERE id=?''',
                       (waste_date.isoformat(), branch, form['product_id'], product.get('Product Name', ''),
                        product.get('SKU', ''), format(qty, 'f'), notes, photo, 'image/jpeg' if photo else None,
                        datetime.now().isoformat(timespec='microseconds'), record_id))
        return jsonify(id=record_id)

    def delete(self, record_id):
        return self.set_deleted(record_id, True)

    def restore(self, record_id):
        return self.set_deleted(record_id, False)

    def set_deleted(self, record_id, deleted):
        blocked = self.mutation_guard()
        if blocked:
            return blocked
        body = request.get_json(silent=True) or {}
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row, error = self.current(db, record_id, body, deleted=not deleted)
            if error:
                return error
            timestamp = datetime.now().isoformat(timespec='microseconds')
            db.execute('UPDATE wastage_records SET deleted_at=?,updated_at=?,revision=revision+1 WHERE id=?',
                       (timestamp if deleted else None, timestamp, record_id))
        return jsonify(id=record_id, deleted=deleted)

    def photo(self, record_id):
        with self.connection() as db:
            row = db.execute('SELECT photo,photo_mime FROM wastage_records WHERE id=? AND deleted_at IS NULL', (record_id,)).fetchone()
        if not row or row['photo'] is None:
            return jsonify(error='Photo was not found.'), 404
        return Response(row['photo'], mimetype=row['photo_mime'], headers={
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
            'Content-Disposition': 'inline; filename="wastage.jpg"'})
