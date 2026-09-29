import json
import uuid
from datetime import date, timedelta

import pytest
import app as module
from test_workflow import client, catalog


def entry(client, **overrides):
    c = catalog(client)
    return dict(submission_id=str(uuid.uuid4()), branch=c['locations'][1],
                product_id=c['products'][0]['id'], count_date=date.today().isoformat(),
                counted_by='Maria Santos', quantity='12', batch='LOT-1',
                expiry_date=(date.today()+timedelta(days=10)).isoformat(), notes='Shelf count', **{}) | overrides


def create(client, **overrides):
    payload = entry(client, **overrides)
    response = client.post('/api/manual-inventory', json=payload)
    assert response.status_code == 201, response.json
    return response.json['id'], payload


def test_create_batches_record_only_and_replay(client):
    before = catalog(client)['products']
    record_id, payload = create(client)
    replay = client.post('/api/manual-inventory', json=payload)
    assert replay.json == dict(id=record_id, repeated=True)
    create(client, expiry_date=(date.today()+timedelta(days=45)).isoformat())
    records = client.get('/api/manual-inventory').json
    assert records['total'] == 2
    assert records['summary'] == dict(total=2, expired=0, soon=1)
    assert records['records'][0]['counted_by'] == 'Maria Santos'
    assert records['records'][0]['expiry_status'] == 'Expiring soon'
    assert catalog(client)['products'] == before
    with module.connection() as db:
        assert db.execute('SELECT count(*) FROM movements').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM manual_inventory_history').fetchone()[0] == 2


@pytest.mark.parametrize('override', [
    dict(quantity='-1'), dict(quantity='NaN'), dict(quantity='Infinity'),
    dict(quantity='0.0000001'), dict(quantity='1000000001'), dict(quantity='bad'),
    dict(branch='688'), dict(branch='Other branch'), dict(product_id='missing'),
    dict(count_date='2026-99-99'), dict(count_date=(date.today()+timedelta(days=1)).isoformat()),
    dict(expiry_date='not-a-date'), dict(counted_by='   '), dict(counted_by='x'*121),
    dict(batch='x'*121), dict(notes='x'*2001), dict(submission_id='invalid')])
def test_invalid_count_rejected(client, override):
    assert client.post('/api/manual-inventory', json=entry(client, **override)).status_code == 400
    assert client.get('/api/manual-inventory').json['total'] == 0


def test_duplicates_edit_delete_restore_and_audit(client):
    record_id, payload = create(client)
    assert client.post('/api/manual-inventory', json=entry(client)).status_code == 409
    base = f'/api/manual-inventory/{record_id}'
    assert client.post(base+'/edit', json=payload | dict(revision=0, quantity='5')).status_code == 200
    assert client.post(base+'/edit', json=payload | dict(revision=0)).status_code == 409
    assert client.post(base+'/delete', json=dict(revision=1)).status_code == 200
    assert client.get('/api/manual-inventory').json['total'] == 0
    assert client.get('/api/manual-inventory?deleted=1').json['records'][0]['quantity'] == '5'
    assert client.post('/api/manual-inventory', json=payload).status_code == 409
    other_id, _ = create(client)
    assert client.post(base+'/restore', json=dict(revision=2)).status_code == 409
    assert client.post(f'/api/manual-inventory/{other_id}/delete', json=dict(revision=0)).status_code == 200
    assert client.post(base+'/restore', json=dict(revision=2)).status_code == 200
    assert client.post(base+'/delete', json=dict(revision=True)).status_code == 409
    assert client.get('/api/manual-inventory').json['records'][0]['revision'] == 3
    with module.connection() as db:
        changes = db.execute('SELECT action,payload FROM manual_inventory_history WHERE record_id=? ORDER BY id', (record_id,)).fetchall()
        assert [r['action'] for r in changes] == ['create', 'edit', 'delete', 'restore']
        assert json.loads(changes[0]['payload'])['quantity'] == '12'


def test_expiry_filters_and_notifications(client):
    record_id, _ = create(client, expiry_date=(date.today()-timedelta(days=1)).isoformat())
    soon_id, _ = create(client, batch='soon', expiry_date=date.today().isoformat())
    create(client, batch='empty', quantity='0', expiry_date=(date.today()-timedelta(days=1)).isoformat())
    create(client, batch='none', expiry_date='')
    create(client, batch='later', expiry_date=(date.today()+timedelta(days=31)).isoformat())
    assert client.get('/api/manual-inventory?status=expired').json['total'] == 1
    assert client.get('/api/manual-inventory?status=soon').json['total'] == 1
    assert client.get('/api/manual-inventory?status=none').json['total'] == 1
    assert client.get('/api/manual-inventory?status=invalid').status_code == 400
    alerts = [a for a in client.get('/api/notifications').json['alerts'] if a['target'] == 'manual-inventory']
    assert len(alerts) == 2
    assert any(a['level'] == 'error' for a in alerts)
    fingerprint = alerts[0]['fingerprint']
    assert [a for a in client.get('/api/notifications').json['alerts'] if a['target'] == 'manual-inventory'][0]['fingerprint'] == fingerprint
    client.post(f'/api/manual-inventory/{record_id}/delete', json=dict(revision=0))
    assert len([a for a in client.get('/api/notifications').json['alerts'] if a['target'] == 'manual-inventory']) == 1
    payload = entry(client, batch='soon', expiry_date=date.today().isoformat(), quantity='0', revision=0)
    assert client.post(f'/api/manual-inventory/{soon_id}/edit', json=payload).status_code == 200
    assert not [a for a in client.get('/api/notifications').json['alerts'] if a['target'] == 'manual-inventory']


def test_pagination_search_branch_and_origin(client):
    for n in range(22):
        create(client, batch=f'lot-{n}')
    c = catalog(client)
    create(client, branch=c['warehouse'], batch='Warehouse lot')
    assert len(client.get('/api/manual-inventory').json['records']) == 20
    assert client.get('/api/manual-inventory?page=2').json['total'] == 23
    assert len(client.get('/api/manual-inventory?page=2').json['records']) == 3
    assert client.get('/api/manual-inventory?page=100').json['page'] == 2
    assert client.get('/api/manual-inventory?page=bad').status_code == 400
    assert client.get('/api/manual-inventory?search=Warehouse%20lot').json['total'] == 1
    assert client.get('/api/manual-inventory', query_string={'branch':c['warehouse']}).json['total'] == 1
    assert client.post('/api/manual-inventory', json=entry(client), headers={'Origin':'https://other.example'}).status_code == 403
    record = client.get('/api/manual-inventory').json['records'][0]
    assert client.post(f"/api/manual-inventory/{record['id']}/delete", json=dict(revision=0), headers={'Origin':'https://other.example'}).status_code == 403
    assert client.post('/api/manual-inventory/missing/delete', json=dict(revision=0)).status_code == 404


def test_edit_conflict_and_deleted_record_guard(client):
    rid, payload = create(client)
    second, other = create(client, batch='second')
    assert client.post(f'/api/manual-inventory/{second}/edit', json=payload | dict(revision=0)).status_code == 409
    assert client.post(f'/api/manual-inventory/{rid}/delete', json=dict(revision=0)).status_code == 200
    assert client.post(f'/api/manual-inventory/{rid}/edit', json=payload | dict(revision=1)).status_code == 409
    assert client.post(f'/api/manual-inventory/{rid}/restore', json=dict(revision=0)).status_code == 409
    assert client.post(f'/api/manual-inventory/{rid}/restore', json=dict(revision=1)).status_code == 200
