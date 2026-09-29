"""Read-only attention summaries from local and synced StoreHub records."""
import hashlib
import json
from decimal import Decimal
from manual_inventory import expiry_status
from flask import jsonify


def register(app, db_factory, integration):
    @app.get('/api/notifications')
    def notifications():
        alerts = []

        def add(key, title, detail, members, target, level='warning', location=''):
            if not members:
                return
            fingerprint = hashlib.sha256(json.dumps(sorted(str(m) for m in members)).encode()).hexdigest()[:20]
            alerts.append(dict(id=key, fingerprint=fingerprint, title=title, detail=detail,
                               target=target, level=level, location=location))

        with db_factory() as db:
            settings = dict(db.execute('SELECT key,value FROM sh_settings'))
            for key, title, target in [('error', 'StoreHub sync needs attention', 'storehub'),
                                       ('orders_error', 'Purchase-order sync needs attention', 'purchase-orders')]:
                if settings.get(key):
                    add(key, title, settings[key], [settings[key]], target, 'error')
            warnings = json.loads(settings.get('warnings', '[]'))
            if warnings:
                add('sync-warnings', 'Some StoreHub data could not refresh', 'Open StoreHub connection to review the sync warnings.', warnings, 'storehub')
            stocks = {}
            for row in db.execute('SELECT i.*,s.name,m.location,p.payload FROM sh_inventory i JOIN sh_stores s ON s.remote_id=i.store_id JOIN sh_locations m ON m.remote_id=i.store_id LEFT JOIN sh_products p ON p.remote_id=i.remote_id'):
                if not integration.allowed_store(row['name']):
                    continue
                qty = Decimal(row['quantity'])
                warning = Decimal(row['warning']) if row['warning'] is not None else None
                kind = 'negative' if qty < 0 else 'low' if warning is not None and warning > 0 and qty <= warning else ''
                if kind:
                    product = json.loads(row['payload']) if row['payload'] else {}
                    stocks.setdefault((kind, row['location']), []).append((row['remote_id'], product.get('name') or 'Unavailable product'))
            for (kind, location), items in stocks.items():
                label = 'negative stock' if kind == 'negative' else 'low stock'
                examples = ', '.join(name for _, name in sorted(items, key=lambda item: item[1])[:3])
                add(kind + ':' + location, f'{len(items)} product{"s" if len(items) != 1 else ""} {"have" if len(items) != 1 else "has"} {label}', location + ': ' + examples + (f' and {len(items)-3} more.' if len(items) > 3 else '.'),
                    [pid for pid, _ in items], 'catalog', 'error' if kind == 'negative' else 'warning', location)
            documents = [dict(r) for r in db.execute('SELECT id,kind,status FROM documents')]
            for key, title, kinds, states in [
                ('local-purchases', 'Warehouse purchases awaiting receipt', ['purchase'], ['Open', 'PartiallyReceived']),
                ('transfers-to-ship', 'Transfers awaiting warehouse shipment', ['transfer'], ['Created']),
                ('transfers-to-receive', 'Transfers awaiting branch receipt', ['transfer'], ['Shipped'])]:
                ids = [r['id'] for r in documents if r['kind'] in kinds and r['status'] in states]
                add(key, title, f'{len(ids)} open records: ' + ', '.join(ids[:3]), ids, 'documents')
            active = {r['remote_id'] for r in db.execute('SELECT s.* FROM sh_stores s JOIN sh_locations m ON m.remote_id=s.remote_id') if integration.allowed_store(r['name'])}
            pending = []
            for row in db.execute('SELECT remote_id,payload FROM sh_orders'):
                order = json.loads(row['payload'])
                status = ''.join(c for c in str(order.get('status', '')).lower() if c.isalnum())
                if order.get('targetStoreId') in active and status not in ('completed', 'closed', 'cancelled', 'canceled', 'received') and any(Decimal(str(i.get('orderedQuantity', 0))) > Decimal(str(i.get('receivedQuantity') or 0)) for i in order.get('orderedItems', [])):
                    pending.append(row['remote_id'])
            add('remote-purchases', 'StoreHub purchase orders have outstanding items', f'{len(pending)} purchase orders still have products to receive.', pending, 'purchase-orders')
            pending_products = [r['id'] for r in db.execute('SELECT id,fields FROM products') if json.loads(r['fields']).get('_storehub_baseline_pending')]
            add('opening-balances', 'Products need opening balances', f'{len(pending_products)} imported products need their local balances initialized.', pending_products, 'storehub')
            uncertain = [r[0] for r in db.execute("SELECT token FROM sh_partner_drafts WHERE status IN ('sending','uncertain')")]
            add('uncertain-api', 'A StoreHub change needs verification', f'{len(uncertain)} API changes have an unconfirmed outcome. Review Recent API changes before trying again.', uncertain, 'partner-tools', 'error')
            publishing = [r[0] for r in db.execute("SELECT local_id FROM sh_publish WHERE status IN ('sending','uncertain','failed')")]
            add('publish-attention', 'Product publishing needs attention', f'{len(publishing)} product publishing attempts need review.', publishing, 'storehub', 'error')
            batches = {}
            for row in db.execute('SELECT * FROM manual_inventory WHERE deleted_at IS NULL'):
                status = expiry_status(row['expiry_date'], row['quantity'])
                if status in ('Expired', 'Expiring soon'):
                    batches.setdefault((row['branch'], status), []).append(row)
            for (branch, status), records in batches.items():
                examples = ', '.join(r['product_name'] for r in records[:3])
                add('inventory-expiry:' + branch + ':' + status,
                    f'{len(records)} inventory batch{"es" if len(records) != 1 else ""}: {status.lower()}',
                    branch + ': ' + examples + (f' and {len(records)-3} more.' if len(records) > 3 else '.'),
                    [r['id'] + ':' + str(r['revision']) + ':' + r['expiry_date'] for r in records],
                    'manual-inventory', 'error' if status == 'Expired' else 'warning', branch)
        alerts.sort(key=lambda a: (a['level'] != 'error', a['title'], a['id']))
        return jsonify(alerts=alerts, lastSync=settings.get('last_sync', ''))
