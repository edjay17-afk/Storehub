import json
import app as module
from test_workflow import client, add_product, order, act


def alerts(client):
    response = client.get('/api/notifications')
    assert response.status_code == 200
    return {a['id']: a for a in response.json['alerts']}


def test_pending_documents_resolve_after_receipt(client):
    pid = add_product(client, '0')
    did = order(client, 'purchase', [dict(product_id=pid, quantity='2', cost='1')])
    assert did in alerts(client)['local-purchases']['detail']
    assert act(client, did, 'receive', quantities={pid: '2'}).status_code == 200
    assert 'local-purchases' not in alerts(client)


def test_inventory_grouping_stable_and_excludes_unrelated_stores(client):
    with module.connection() as db:
        for sid, name in [('a', module.WAREHOUSE), ('b', '688'), ('c', 'Digital Store')]:
            db.execute('INSERT INTO sh_stores VALUES (?,?)', (sid, name))
            db.execute('INSERT INTO sh_locations VALUES (?,?)', (name, sid))
            db.execute('INSERT INTO sh_inventory VALUES (?,?,?,?,?)', ('p', sid, '-1', '2', '10'))
        db.execute('INSERT INTO sh_products VALUES (?,?)', ('p', json.dumps({'name': 'Rice'})))
        db.execute('INSERT INTO sh_inventory VALUES (?,?,?,?,?)', ('q', 'a', '2', '3', '10'))
    result = alerts(client)
    assert len(result) == 2
    assert result['negative:' + module.WAREHOUSE]['level'] == 'error'
    low = result['low:' + module.WAREHOUSE]
    with module.connection() as db:
        db.execute("UPDATE sh_inventory SET quantity='1' WHERE remote_id='q'")
    assert alerts(client)['low:' + module.WAREHOUSE]['fingerprint'] == low['fingerprint']
    with module.connection() as db:
        db.execute("UPDATE sh_inventory SET quantity='4' WHERE remote_id='q'")
    assert 'low:' + module.WAREHOUSE not in alerts(client)


def test_sync_and_unknown_api_results_are_read_only(client):
    with module.connection() as db:
        db.execute('INSERT INTO sh_settings VALUES (?,?)', ('error', 'Service unavailable'))
        db.execute('INSERT INTO sh_settings VALUES (?,?)', ('warnings', json.dumps(['Products unavailable'])))
        before = '\n'.join(db.iterdump())
    result = alerts(client)
    assert result['error']['detail'] == 'Service unavailable'
    assert 'sync-warnings' in result
    with module.connection() as db:
        assert '\n'.join(db.iterdump()) == before


def test_remote_purchase_alert_requires_allowed_store_and_outstanding_items(client):
    with module.connection() as db:
        db.execute('INSERT INTO sh_stores VALUES (?,?)', ('a', module.WAREHOUSE))
        db.execute('INSERT INTO sh_locations VALUES (?,?)', (module.WAREHOUSE, 'a'))
        for oid, target, status, received in [('pending', 'a', 'Open', 0), ('done', 'a', 'Completed', 0), ('other', 'b', 'Open', 0), ('full', 'a', 'Open', 2)]:
            payload = dict(targetStoreId=target, status=status, orderedItems=[dict(orderedQuantity=2, receivedQuantity=received)])
            db.execute('INSERT INTO sh_orders VALUES (?,?)', (oid, json.dumps(payload)))
    assert alerts(client)['remote-purchases']['detail'].startswith('1 purchase orders')
