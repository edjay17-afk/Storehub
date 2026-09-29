"""Validate Netlify's signed proxy requests before serving warehouse data."""

import base64
import binascii
import hashlib
import hmac
import json
import time


def _decode(segment):
    return base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))


def valid_netlify_signature(value, secret, site_id, now=None):
    if not value or not secret or not site_id:
        return False
    try:
        header_part, payload_part, signature_part = value.split('.')
        signed = f'{header_part}.{payload_part}'.encode('ascii')
        header = json.loads(_decode(header_part))
        claims = json.loads(_decode(payload_part))
        signature = _decode(signature_part)
        expected = hmac.new(secret.encode('utf-8'), signed, hashlib.sha256).digest()
        current = time.time() if now is None else now
        return (header.get('alg') == 'HS256'
                and hmac.compare_digest(signature, expected)
                and claims.get('iss') == 'netlify'
                and claims.get('netlify_id') == site_id
                and isinstance(claims.get('exp'), (int, float))
                and current < claims['exp'] <= current + 300)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, binascii.Error):
        return False
