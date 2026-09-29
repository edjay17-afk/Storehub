import io
import uuid
from datetime import date, timedelta

import pytest
from PIL import Image
import app as module
from test_workflow import client


def details(client, **changes):
    options = client.get('/api/wastage').json
    catalog = client.get('/api/catalog').json
    result = dict(submission_id=options['submission_id'], waste_date=options['today'],
                  branch=options['branches'][0], product_id=catalog['products'][0]['id'],
                  quantity='2.125', notes='Damaged packaging')
    result.update(changes)
    return result


def photo():
    buffer = io.BytesIO()
    Image.new('RGB', (30, 40), 'green').save(buffer, 'PNG')
    buffer.seek(0)
    return buffer


def test_photo_record_persists_without_changing_inventory(client):
    fields = details(client)
    with module.connection() as db:
        before = db.execute('SELECT fields FROM products WHERE id=?', (fields['product_id'],)).fetchone()[0]
        movements = db.execute('SELECT count(*) FROM movements').fetchone()[0]
    result = client.post('/api/wastage', data={**fields, 'photo': (photo(), 'evidence.png')})
    assert result.status_code == 201
    record_id = result.json['id']
    saved = client.get('/api/wastage').json['records'][0]
    assert saved['quantity'] == '2.125' and saved['has_photo'] == 1
    assert saved['notes'] == 'Damaged packaging'
    evidence = client.get(f'/api/wastage/{record_id}/photo')
    assert evidence.status_code == 200 and evidence.mimetype == 'image/jpeg'
    assert Image.open(io.BytesIO(evidence.data)).size == (30, 40)
    with module.connection() as db:
        assert db.execute('SELECT fields FROM products WHERE id=?', (fields['product_id'],)).fetchone()[0] == before
        assert db.execute('SELECT count(*) FROM movements').fetchone()[0] == movements
        assert db.execute('SELECT length(photo) FROM wastage_records').fetchone()[0] > 0
    # A fresh connection and request still return the saved image and record.
    assert client.get(f'/api/wastage/{record_id}/photo').data == evidence.data


def test_duplicate_submission_creates_one_record(client):
    fields = details(client)
    first = client.post('/api/wastage', data=fields)
    replay = client.post('/api/wastage', data=fields)
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json['id'] == first.json['id'] and replay.json['repeated']
    assert client.get('/api/wastage').json['total'] == 1
    assert client.get('/api/wastage/'+first.json['id']+'/photo').status_code == 404


@pytest.mark.parametrize('changes', [dict(branch='688'), dict(branch=module.WAREHOUSE),
    dict(branch='unknown'), dict(product_id='missing'), dict(quantity='0'), dict(quantity='-1'),
    dict(quantity='NaN'), dict(quantity='Infinity'), dict(quantity='0.0000001'),
    dict(quantity='1000000001'), dict(waste_date='not-a-date'),
    dict(waste_date=(date.today()+timedelta(days=1)).isoformat()), dict(submission_id='bad')])
def test_invalid_records_do_not_persist(client, changes):
    assert client.post('/api/wastage', data=details(client, **changes)).status_code == 400
    assert client.get('/api/wastage').json['total'] == 0


def test_bad_and_oversize_photos_rejected(client):
    fields = details(client)
    for buffer in (io.BytesIO(b'<svg>fake image</svg>'), io.BytesIO(b'x'*(10*1024*1024+1))):
        result = client.post('/api/wastage', data={**fields, 'photo': (buffer, 'image.jpg')})
        assert result.status_code in (400, 413)
    assert client.get('/api/wastage').json['total'] == 0


def test_history_filters_pagination_and_removed_branch(client):
    fields = details(client)
    for i in range(22):
        assert client.post('/api/wastage', data={**fields, 'submission_id': str(uuid.uuid4())}).status_code == 201
    history = client.get('/api/wastage').json
    assert len(history['records']) == 20 and history['pages'] == 2
    assert len(client.get('/api/wastage?page=2').json['records']) == 2
    assert client.get('/api/wastage?branch=688').json['total'] == 0
    assert client.get('/api/wastage?search=nonexistent-product').json['total'] == 0
    assert client.get('/api/wastage?waste_date='+fields['waste_date']).json['total'] == 22
    assert '688' not in history['branches'] and module.WAREHOUSE not in history['branches']


def test_cross_origin_save_rejected(client):
    assert client.post('/api/wastage', data=details(client), headers={'Origin':'https://example.com'}).status_code == 403
    assert client.get('/api/wastage').json['total'] == 0


def saved_record(client, with_photo=True):
    fields = details(client)
    response = client.post('/api/wastage', data={**fields, **({'photo': (photo(), 'photo.png')} if with_photo else {})})
    return response.json['id'], fields


def test_edit_details_preserves_photo_and_stock(client):
    record_id, fields = saved_record(client)
    before = client.get('/api/catalog').json
    original_photo = client.get(f'/api/wastage/{record_id}/photo').data
    fields.update(revision='0', quantity='3.75', notes='Corrected count')
    result = client.post(f'/api/wastage/{record_id}/edit', data=fields)
    assert result.status_code == 200
    saved = client.get('/api/wastage').json['records'][0]
    assert saved['quantity'] == '3.75' and saved['notes'] == 'Corrected count'
    assert saved['revision'] == 1 and saved['updated_at']
    assert client.get(f'/api/wastage/{record_id}/photo').data == original_photo
    after = client.get('/api/catalog').json
    assert before['products'] == after['products'] and before['movements'] == after['movements']
    assert client.post(f'/api/wastage/{record_id}/edit', data=fields).status_code == 409


def test_edit_replace_remove_photo_and_change_product_branch(client):
    record_id, fields = saved_record(client)
    catalog = client.get('/api/catalog').json
    fields.update(revision='0', photo_action='replace', product_id=catalog['products'][1]['id'],
                  branch=client.get('/api/wastage').json['branches'][1], waste_date=(date.today()-timedelta(days=1)).isoformat())
    assert client.post(f'/api/wastage/{record_id}/edit', data={**fields, 'photo':(photo(),'replacement.png')}).status_code == 200
    saved = client.get('/api/wastage').json['records'][0]
    assert saved['product_id'] == fields['product_id'] and saved['branch'] == fields['branch']
    assert saved['waste_date'] == fields['waste_date'] and saved['has_photo']
    fields.update(revision='1', photo_action='remove')
    assert client.post(f'/api/wastage/{record_id}/edit', data=fields).status_code == 200
    assert client.get(f'/api/wastage/{record_id}/photo').status_code == 404


@pytest.mark.parametrize('changes', [dict(quantity='-1'),dict(branch='688'),dict(product_id='missing'),
    dict(photo_action='replace'),dict(photo_action='bad'),dict(waste_date='bad')])
def test_invalid_edit_leaves_record_unchanged(client, changes):
    record_id, fields = saved_record(client)
    fields.update(revision='0', **changes)
    assert client.post(f'/api/wastage/{record_id}/edit', data=fields).status_code == 400
    saved = client.get('/api/wastage').json['records'][0]
    assert saved['revision'] == 0 and saved['quantity'] == '2.125' and saved['has_photo']


def test_delete_restore_photo_and_conflict(client):
    record_id, fields = saved_record(client)
    original_photo = client.get(f'/api/wastage/{record_id}/photo').data
    assert client.post(f'/api/wastage/{record_id}/delete',json={'revision':5}).status_code == 409
    assert client.post(f'/api/wastage/{record_id}/delete',json={'revision':0}).status_code == 200
    assert client.get('/api/wastage').json['total'] == 0
    deleted = client.get('/api/wastage?deleted=1').json['records'][0]
    assert client.post('/api/wastage', data=fields).status_code == 409
    assert deleted['deleted_at'] and deleted['revision'] == 1
    assert client.get(f'/api/wastage/{record_id}/photo').status_code == 404
    assert client.post(f'/api/wastage/{record_id}/edit',data={**fields,'revision':1}).status_code == 404
    assert client.post(f'/api/wastage/{record_id}/restore',json={'revision':0}).status_code == 409
    assert client.post(f'/api/wastage/{record_id}/restore',json={'revision':1}).status_code == 200
    assert client.get('/api/wastage').json['total'] == 1
    assert client.get('/api/wastage?deleted=1').json['total'] == 0
    assert client.get(f'/api/wastage/{record_id}/photo').data == original_photo


@pytest.mark.parametrize('action', ['edit','delete','restore'])
def test_edit_delete_restore_reject_other_origins(client, action):
    record_id, fields = saved_record(client)
    assert client.post(f'/api/wastage/{record_id}/{action}',headers={'Origin':'https://example.com'}).status_code == 403


def test_existing_database_migration_preserves_records(client):
    from wastage import initialize
    import sqlite3
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE wastage_records(id TEXT PRIMARY KEY,submission_id TEXT,waste_date TEXT,branch TEXT,product_id TEXT,product_name TEXT,sku TEXT,quantity TEXT,notes TEXT,created TEXT,photo BLOB,photo_mime TEXT)')
    db.execute("INSERT INTO wastage_records(id,waste_date,quantity) VALUES ('old','2026-01-01','2')")
    initialize(db)
    initialize(db)
    assert db.execute("SELECT quantity,revision,deleted_at FROM wastage_records WHERE id='old'").fetchone() == ('2',0,None)
    db.close()
