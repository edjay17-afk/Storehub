"""Minimal cached StoreHub name directory shared by read-only views."""
import json
from datetime import datetime, timedelta, timezone

from storehub import array, RemoteError


def cached(db, kind):
    row = db.execute('SELECT value FROM sh_settings WHERE key=?', ('people_' + kind,)).fetchone()
    return json.loads(row[0]) if row else dict(records=[], refreshed='')


def refresh(integration):
    result, warnings = {}, []
    for kind, key in [('employees', 'id'), ('customers', 'refId')]:
        with integration.db() as db:
            saved = cached(db, kind)
        fresh = bool(saved['refreshed']) and datetime.now(timezone.utc) - datetime.fromisoformat(saved['refreshed']) < timedelta(minutes=15)
        if not fresh:
            try:
                rows = array(integration.client().call('/' + kind))
                records = []
                for row in rows:
                    name = ' '.join(str(row.get(k) or '').strip() for k in ('firstName', 'lastName')).strip() or str(row.get('name') or '').strip()
                    if row.get(key) and name:
                        records.append(dict(id=str(row[key]), name=name))
                saved = dict(records=records, refreshed=datetime.now(timezone.utc).isoformat())
                with integration.db() as db:
                    integration.save(db, 'people_' + kind, json.dumps(saved))
            except (RemoteError, ValueError):
                warnings.append('Could not refresh ' + kind + ' names. Previously loaded names remain available.')
        result[kind] = saved['records']
    return dict(**result, warnings=warnings)


def name(db, kind, ref):
    if not ref:
        return ''
    return next((r['name'] for r in cached(db, kind)['records'] if r['id'] == str(ref)), '')
