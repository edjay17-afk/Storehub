from secure_scanner_server import scanner_app


def test_scanner_serves_only_generic_scanner_files():
    client = scanner_app.test_client()
    for path in ['/', '/decoder.js', '/scanner.js']:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['Permissions-Policy'] == 'camera=(self), microphone=()'
        assert "connect-src 'none'" in response.headers['Content-Security-Policy']
    for path in ['/api/catalog', '/exports/products.csv', '/data/warehouse.db', '/static/app.js']:
        assert client.get(path).status_code == 404
    assert client.post('/').status_code == 405

def test_scanner_monitor_state_requires_fresh_trusted_https(tmp_path, monkeypatch):
    import app as module
    import json
    from datetime import datetime,timedelta,timezone
    monkeypatch.setattr(module,'ROOT',tmp_path)
    (tmp_path/'data').mkdir()
    path=tmp_path/'data/scanner-status.json'
    client=module.app.test_client()
    assert client.get('/api/scanner-status').json['ready'] is False
    state=dict(ready=True,url='https://new-scanner.trycloudflare.com',checked=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(state))
    assert client.get('/api/scanner-status').json==dict(ready=True,url=state['url'])
    state['checked']=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
    path.write_text(json.dumps(state))
    assert client.get('/api/scanner-status').json['ready'] is False
    state.update(checked=datetime.now(timezone.utc).isoformat(),url='https://untrusted.example.com')
    path.write_text(json.dumps(state))
    assert client.get('/api/scanner-status').json['ready'] is False
    path.write_text('invalid json')
    assert client.get('/api/scanner-status').json['ready'] is False
