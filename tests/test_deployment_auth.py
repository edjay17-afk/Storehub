import base64
import hashlib
import hmac
import json
import time
from unittest.mock import patch

import app as warehouse
from deployment_auth import valid_netlify_signature


def token(secret='test-secret', site_id='test-site', expiry=None, algorithm='HS256'):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')

    header = encode({'alg': algorithm, 'typ': 'JWT'})
    payload = encode({'iss': 'netlify', 'netlify_id': site_id,
                      'exp': time.time() + 60 if expiry is None else expiry})
    message = f'{header}.{payload}'
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode().rstrip('=')
    return f'{message}.{signature}'


def test_proxy_signature_rejects_invalid_or_expired_requests():
    valid = token()
    assert valid_netlify_signature(valid, 'test-secret', 'test-site')
    assert not valid_netlify_signature(valid, 'wrong-secret', 'test-site')
    assert not valid_netlify_signature(valid, 'test-secret', 'wrong-site')
    assert not valid_netlify_signature(token(expiry=time.time() - 1), 'test-secret', 'test-site')
    assert not valid_netlify_signature(token(expiry=time.time() + 600), 'test-secret', 'test-site')
    assert not valid_netlify_signature(token(algorithm='none'), 'test-secret', 'test-site')
    assert not valid_netlify_signature('malformed', 'test-secret', 'test-site')


def test_deployed_backend_rejects_direct_requests():
    with patch.object(warehouse, 'REQUIRE_NETLIFY_PROXY', True), \
         patch.object(warehouse, 'NETLIFY_PROXY_SECRET', 'test-secret'), \
         patch.object(warehouse, 'NETLIFY_SITE_ID', 'test-site'):
        client = warehouse.app.test_client()
        assert client.get('/healthz').status_code == 200
        assert client.get('/').status_code == 403
        assert client.get('/', headers={'x-nf-sign': token()}).status_code == 200


def test_deployment_waits_for_private_database(tmp_path):
    with patch.object(warehouse, 'DEPLOY_MODE', True), \
         patch.object(warehouse, 'REQUIRE_NETLIFY_PROXY', True), \
         patch.object(warehouse, 'NETLIFY_PROXY_SECRET', 'test-secret'), \
         patch.object(warehouse, 'NETLIFY_SITE_ID', 'test-site'), \
         patch.dict(warehouse.app.config, {'DATABASE': str(tmp_path / 'warehouse.db')}):
        client = warehouse.app.test_client()
        assert client.get('/healthz').json == {'running': True, 'data_ready': False}
        assert client.get('/', headers={'x-nf-sign': token()}).status_code == 503
