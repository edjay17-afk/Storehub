import csv
import io
import json

import app as module
from test_workflow import client


def records(response):
    return list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))


def api_product(changed=False):
    with module.connection() as db:
        fields=dict.fromkeys(module.config(db)[0], '')
        fields.update({'Product Id':'api-test', 'Product Name':'Missing, "CSV" fields',
                       'Cost':'1.2345', '_storehub_api_imported':True, '_storehub_baseline_pending':True})
        db.execute('INSERT INTO products VALUES (?,?,?)',('api-test',json.dumps(fields),int(changed)))


def test_full_catalog_download_includes_all_and_marks_incomplete(client):
    api_product()
    response=client.get('/exports/products.csv?scope=all')
    assert response.status_code==200
    rows=records(response)
    assert len(rows)==1362
    missing=next(r for r in rows if r['Product Id']=='api-test')
    assert missing['Product Name']=='Missing, "CSV" fields'
    assert missing['Cost']=='1.2345' and missing['Tax-Inclusive Price']==''
    assert missing['Stock Balance Status']=='Not initialized locally'
    assert missing['CSV Export Status'].startswith('REVIEW ONLY')
    assert not any(k.startswith('688_') or k.startswith('_storehub') for k in missing)
    status=client.get('/api/product-export-status').json
    assert status['total']==1362 and status['ready']==1361
    assert status['missing'][0]['baseline_pending']


def test_ready_download_preserves_template_and_excludes_only_missing(client):
    original=client.get('/exports/products.csv?scope=all').data
    api_product()
    export=client.get('/exports/products.csv?scope=ready')
    assert export.data==original
    assert 'CSV Export Status' not in export.data.decode('utf-8-sig')


def test_changed_export_handles_api_only_without_mutation(client):
    api_product(changed=True)
    before=client.get('/api/catalog').json['products']
    status=client.get('/api/product-export-status?scope=changed').json
    assert status['total']==1 and status['ready']==0 and len(status['missing'])==1
    response=client.get('/exports/products.csv')
    assert response.status_code==200 and 'Changed_Products_Review.csv' in response.headers['Content-Disposition']
    assert len(records(response))==1
    assert len(records(client.get('/exports/products.csv?scope=ready&changed=1')))==1  # instruction row only
    assert client.get('/api/catalog').json['products']==before


def test_complete_catalog_keeps_original_export(client):
    status=client.get('/api/product-export-status').json
    assert status['total']==1361 and status['ready']==1361 and status['missing']==[]
    response=client.get('/exports/products.csv?scope=all')
    assert 'filename="Products.csv"' in response.headers['Content-Disposition']
    assert len(records(response))==1362  # instruction row and products
