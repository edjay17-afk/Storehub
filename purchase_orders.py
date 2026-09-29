"""Read-only StoreHub purchase orders and exports using the supplied CSV layout."""
import csv
import people
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from flask import jsonify, request, Response
from storehub import array, decimal, identity, normalized, timestamp


class PurchaseOrders:
    def __init__(self, integration):
        self.sh = integration
        self.root = Path(integration.app.root_path)
        routes = [('', 'GET', self.list), ('/refresh', 'POST', self.refresh),
                  ('/<order_id>', 'GET', self.detail), ('/<order_id>/refresh', 'POST', self.refresh_detail),
                  ('/<order_id>.csv', 'GET', self.export)]
        for path, method, handler in routes:
            integration.app.add_url_rule('/api/storehub/purchase-orders' + path, 'purchase_orders' + (path or '/list'), handler, methods=[method])

    def identifier(self, value):
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(value)):
            raise ValueError('Enter a valid StoreHub purchase order reference.')
        return str(value)

    def validate(self, order):
        if not isinstance(order, dict):
            raise ValueError('StoreHub returned an invalid purchase order.')
        self.identifier(order.get('purchaseOrderId'))
        identity(order.get('targetStoreId'))
        if not order.get('createdTime'):
            raise ValueError('StoreHub returned a purchase order without its creation date.')
        for field in ['createdTime', 'modifiedTime', 'expectedArrivalDate']:
            if order.get(field):
                try:
                    when = datetime.fromisoformat(str(order[field]).replace('Z', '+00:00'))
                    if not when.tzinfo:
                        raise ValueError
                except ValueError:
                    raise ValueError('StoreHub returned an invalid purchase-order date.') from None
        for field in ['subTotal', 'total', 'tax', 'discount']:
            if order.get(field) is not None:
                decimal(order[field])
        for item in array(order.get('orderedItems', [])):
            identity(item.get('productId'))
            for field in ['orderedQuantity', 'receivedQuantity', 'supplierPrice', 'subTotal']:
                if item.get(field) is not None:
                    decimal(item[field])
            if item.get('orderedQuantity') is None:
                raise ValueError('StoreHub returned a purchase-order line without its ordered quantity.')
            for component in array(item.get('componentsUsages', [])):
                identity(component.get('productId'))
                if component.get('quantity', component.get('Quantity')) is not None:
                    decimal(component.get('quantity', component.get('Quantity')))
        return order

    def range(self, values):
        today = datetime.now(timezone.utc).date()
        try:
            start = date.fromisoformat(str(values.get('from') or (today - timedelta(days=30)).isoformat()))
            end = date.fromisoformat(str(values.get('to') or today.isoformat()))
        except ValueError:
            raise ValueError('Choose valid purchase-order dates.') from None
        if start > end or (end - start).days > 365 or end > today + timedelta(days=1):
            raise ValueError('Choose a date range of up to one year, with From before To.')
        return start, end

    def day(self, value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).date() if value else None

    def save_orders(self, db, orders, start, end, synced):
        # Replace only this successful query window; older fetched history stays cached.
        for row in db.execute('SELECT remote_id,payload FROM sh_orders').fetchall():
            created = self.day(json.loads(row['payload']).get('createdTime'))
            if created is not None and start <= created <= end:
                db.execute('DELETE FROM sh_orders WHERE remote_id=?', (row['remote_id'],))
        db.executemany('INSERT OR REPLACE INTO sh_orders VALUES (?,?)', [(str(o['purchaseOrderId']), json.dumps(o)) for o in orders])
        db.execute('INSERT OR REPLACE INTO sh_order_windows VALUES (?,?,?)', (start.isoformat(), end.isoformat(), synced))
        self.sh.save(db, 'orders_sync', synced)
        self.sh.save(db, 'orders_error', '')

    def source_labels(self, db):
        labels = {}
        path = self.root / 'data' / 'Purchase_Orders_09-28-2026.csv'
        try:
            with path.open(encoding='utf-8-sig', newline='') as file:
                for row in csv.DictReader(file):
                    value = row.get('P.O ID', '')
                    match = re.fullmatch(r'PO0*(\d+)', value, re.I)
                    if match:
                        labels[str(int(match[1]))] = row
        except OSError:
            pass
        stores = dict(db.execute('SELECT remote_id,name FROM sh_stores'))
        suppliers, matched = {}, {}
        for row in db.execute('SELECT remote_id,payload FROM sh_orders'):
            order = json.loads(row['payload'])
            source = labels.get(str(order['purchaseOrderId']))
            if not source or not order.get('createdTime'):
                continue
            when = datetime.fromisoformat(order['createdTime'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%m/%d/%Y %H:%M')
            if when != source.get('Created Date') or normalized(stores.get(order['targetStoreId'], '')) != normalized(source.get('Target Store', '')):
                continue
            matched[str(order['purchaseOrderId'])] = source
            if source.get('Supplier') and order.get('supplierId'):
                suppliers.setdefault(order['supplierId'], set()).add(source['Supplier'])
        return matched, {key: next(iter(names)) for key, names in suppliers.items() if len(names) == 1}

    def context(self, db):
        stores = {r['remote_id']: r['name'] for r in db.execute('SELECT * FROM sh_stores')
                  if self.sh.allowed_store(r['name'])}
        active = set(dict(db.execute('SELECT location,remote_id FROM sh_locations')).values()) & set(stores)
        products = {}
        for row in db.execute('SELECT l.remote_id,p.fields FROM sh_links l JOIN products p ON p.id=l.local_id'):
            products[row['remote_id']] = json.loads(row['fields'])
        for row in db.execute('SELECT remote_id,payload FROM sh_products'):
            remote = json.loads(row['payload'])
            p = products.setdefault(row['remote_id'], {})
            for field, key in [('Product Name','name'),('SKU','sku'),('Category','category')]:
                p.setdefault(field, remote.get(key, ''))
        sources, suppliers = self.source_labels(db)
        employees = {r['id']: r['name'] for r in people.cached(db, 'employees')['records']}
        return stores, active, products, sources, suppliers, employees

    def enrich(self, order, context):
        stores, _, products, sources, suppliers, employees = context
        source = sources.get(str(order['purchaseOrderId']), {})
        items = []
        for line in order.get('orderedItems', []):
            p = products.get(line['productId'], {})
            ordered = decimal(line['orderedQuantity'])
            received = decimal(line.get('receivedQuantity') or 0)
            components = [dict(c, name=products.get(c['productId'], {}).get('Product Name') or 'Unavailable product') for c in line.get('componentsUsages', [])]
            items.append(dict(line, name=p.get('Product Name') or 'Unavailable product', sku=p.get('SKU', ''), category=p.get('Category', ''), unit=p.get('Base Unit') or p.get('Unit') or '', outstanding=str(max(Decimal(0), ordered-received)), overReceived=str(max(Decimal(0),received-ordered)), componentsUsages=components))
        result = dict(order, id=str(order['purchaseOrderId']), storeName=stores.get(order['targetStoreId'], 'Unknown store'), supplierName=source.get('Supplier') or suppliers.get(order.get('supplierId'), ''), requestedBy=source.get('Requested By') or employees.get(order.get('requestedBy'), ''), completedByName=source.get('Completed By') or employees.get(order.get('completedBy'), ''), cancelledBy=source.get('Cancelled By') or employees.get(order.get('cancelledBy'), ''), cancelledDate=source.get('Cancelled Date', ''), completionDate=source.get('Completion Date', ''), orderedItems=items)
        result['ordered'] = str(sum((decimal(i['orderedQuantity']) for i in items), Decimal(0)))
        result['received'] = str(sum((decimal(i.get('receivedQuantity') or 0) for i in items), Decimal(0)))
        result['outstanding'] = str(sum((decimal(i['outstanding']) for i in items), Decimal(0)))
        return result

    def get(self, db, order_id):
        self.identifier(order_id)
        row = db.execute('SELECT payload FROM sh_orders WHERE remote_id=?', (order_id,)).fetchone()
        context = self.context(db)
        if not row or json.loads(row['payload']).get('targetStoreId') not in context[1]:
            raise ValueError('Purchase order not found in your active locations. Refresh the selected dates first.')
        return self.enrich(json.loads(row['payload']), context)

    def list(self):
        start, end = self.range(request.args)
        try:
            page = int(request.args.get('page', '1'))
            size = int(request.args.get('size', '20'))
        except ValueError:
            raise ValueError('Choose a valid purchase-order page.') from None
        if page < 1 or size not in [10,20,50]:
            raise ValueError('Choose a valid purchase-order page size.')
        q = request.args.get('q','').strip().casefold()
        status = request.args.get('status','').casefold()
        target = request.args.get('store','')
        with self.sh.db() as db:
            context = self.context(db)
            locations = [dict(id=sid,name=context[0].get(sid,loc)) for loc,sid in db.execute('SELECT location,remote_id FROM sh_locations')]
            orders = []
            statuses = set()
            for row in db.execute('SELECT payload FROM sh_orders'):
                order = json.loads(row[0])
                created = self.day(order.get('createdTime'))
                if order.get('targetStoreId') not in context[1] or created is None or not start <= created <= end:
                    continue
                statuses.add(str(order.get('status') or 'Unknown'))
                item = self.enrich(order, context)
                reference = 'PO'+item['id'].zfill(4) if item['id'].isdigit() else item['id']
                search = [item['id'], reference, item['storeName'], item['supplierName'], item.get('supplierId',''), item.get('notes','')] + [v for l in item['orderedItems'] for v in [l['name'], l['sku']]]
                if status and status != str(order.get('status','')).casefold(): continue
                if target and target != order.get('targetStoreId'): continue
                if q and not any(q in str(v).casefold() for v in search): continue
                orders.append(item)
            orders.sort(key=lambda o:(o.get('createdTime',''),o['id']),reverse=True)
            total = len(orders)
            pages = max(1, (total+size-1)//size)
            page = min(page,pages)
            runs = [r['synced'] for r in db.execute('SELECT * FROM sh_order_windows') if r['start']<=start.isoformat() and r['end']>=end.isoformat()]
            refreshed = max(runs,default='')
            error = self.sh.setting(db,'orders_error')
        return jsonify(orders=orders[(page-1)*size:page*size], total=total, page=page, pages=pages, locations=locations, statuses=sorted(statuses), refreshed=refreshed, error=error, fromDate=start.isoformat(), toDate=end.isoformat(), needsRefresh=not bool(refreshed))

    def detail(self, order_id):
        with self.sh.db() as db:
            order = self.get(db,order_id)
        return jsonify(order)

    def operation(self, fn):
        if not self.sh.lock.acquire(blocking=False):
            raise ValueError('A StoreHub operation is running. Wait for it to finish.')
        self.sh.busy = True
        try:
            return fn()
        except ValueError as error:
            with self.sh.db() as db:
                self.sh.save(db,'orders_error',str(error))
            raise
        finally:
            self.sh.busy = False
            self.sh.lock.release()

    def refresh(self):
        body = self.sh.body()
        start, end = self.range(body)
        def fetch():
            client = self.sh.client()
            with self.sh.db() as db:
                active = set(dict(db.execute('SELECT location,remote_id FROM sh_locations')).values())
            if not active:
                raise ValueError('Map your StoreHub locations in the connection page first.')
            orders = [self.validate(o) for o in self.sh.dates(client,'/purchaseOrders','purchaseOrderId',start,end+timedelta(days=1)) if o.get('targetStoreId') in active]
            with self.sh.db() as db:
                self.save_orders(db,orders,start,end,timestamp())
            return jsonify(ok=True, count=len(orders), refreshed=timestamp())
        return self.operation(fetch)

    def refresh_detail(self, order_id):
        self.sh.body()
        self.identifier(order_id)
        def fetch():
            # Recheck access before and after the remote read; no receipt is recorded.
            with self.sh.db() as db:
                self.get(db,order_id)
            order = self.validate(self.sh.client().call('/purchaseOrders/'+quote(order_id,safe='')))
            if str(order['purchaseOrderId']) != order_id:
                raise ValueError('StoreHub returned a different purchase order. Cached details were preserved.')
            with self.sh.db() as db:
                active = set(dict(db.execute('SELECT location,remote_id FROM sh_locations')).values())
                if order['targetStoreId'] not in active:
                    raise ValueError('The purchase order is no longer in an active mapped location.')
                db.execute('INSERT OR REPLACE INTO sh_orders VALUES (?,?)',(order_id,json.dumps(order)))
                self.sh.save(db,'orders_error','')
            return self.detail(order_id)
        return self.operation(fetch)

    def export(self, order_id):
        with self.sh.db() as db:
            order = self.get(db,order_id)
        with (self.root/'data'/'Purchase_Orders_09-28-2026.csv').open(encoding='utf-8-sig',newline='') as file:
            headers = next(csv.reader(file))
        def date_text(value):
            return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%m/%d/%Y %H:%M') if value else ''
        status = {'open':'Open','completed':'Completed','cancelled':'Cancelled','partiallyreceived':'PartiallyReceived'}.get(str(order.get('status','')).casefold(),order.get('status',''))
        reference = 'PO'+order_id.zfill(4) if order_id.isdigit() else order_id
        common = {'P.O ID':reference,'Created Date':date_text(order.get('createdTime')),'Estimated Date of Arrival':date_text(order.get('expectedArrivalDate')),'Completion Date':order.get('completionDate',''),'Supplier':order['supplierName'] or order.get('supplierId',''),'Target Store':order['storeName'],'Status':status,'Notes':order.get('notes',''),'Requested By':order['requestedBy'],'Completed By':order['completedByName'] or order.get('completedBy',''),'Cancelled By':order['cancelledBy'],'Cancelled Date':order['cancelledDate']}
        rows = [dict(common,**{'Total (RM)':order.get('total','')})]
        for n,item in enumerate(order['orderedItems'],1):
            rows.append(dict(common,**{'No.':n,'Product Name':item['name'],'SKU':item['sku'],'Category':item['category'],'Ordered Quantity':item['orderedQuantity'],'Received Quantity':item.get('receivedQuantity',''),'Unit':item['unit'],'Cost (RM)':item.get('supplierPrice',''),'SubTotal (RM)':item.get('subTotal','')}))
        stream=io.StringIO(newline='')
        writer=csv.DictWriter(stream,fieldnames=headers,extrasaction='ignore',quoting=csv.QUOTE_ALL,lineterminator='\r\n')
        writer.writeheader();writer.writerows(rows)
        return Response('\ufeff'+stream.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="StoreHub-{reference}.csv"'})
