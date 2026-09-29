"""Public-facing scanner serves only static camera code; never the warehouse API."""
from pathlib import Path

from flask import Flask, send_file

ROOT = Path(__file__).resolve().parent
scanner_app = Flask(__name__, static_folder=None)


@scanner_app.after_request
def security_headers(response):
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=()'
    response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; media-src 'self' blob:; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


@scanner_app.get('/')
def scanner():
    return send_file(ROOT / 'templates' / 'secure-scanner.html')


@scanner_app.get('/decoder.js')
def decoder():
    return send_file(ROOT / 'static' / 'vendor' / 'zxing-browser.min.js')


@scanner_app.get('/scanner.js')
def scanner_script():
    return send_file(ROOT / 'static' / 'secure-scanner.js')


if __name__ == '__main__':
    scanner_app.run(host='127.0.0.1', port=5078, debug=False)
