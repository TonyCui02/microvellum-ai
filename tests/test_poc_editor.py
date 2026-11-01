import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'poc'))
import server as review_server


class POCEditorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_manifest, self.old_catalog, self.old_catalog_hash = review_server.MANIFEST, review_server.CATALOG, review_server.CATALOG_SHA256
        self.dbpath = self.root / 'catalog.sqlite'
        db = sqlite3.connect(self.dbpath)
        db.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE entities(table_name TEXT,record_id TEXT,link_id TEXT,name TEXT,category_link_id TEXT,
            library_link_id TEXT,project_link_id TEXT,fields_json TEXT);
          INSERT INTO entities VALUES('Libraries','lib-rec','lib-link','Test Library',NULL,NULL,NULL,'{}');
          INSERT INTO entities VALUES('Projects','proj-rec','template-link','Template',NULL,'lib-link',NULL,'{}');
          INSERT INTO entities VALUES('SpecificationGroups','group-rec','group-link','Kitchen Library',NULL,'lib-link',NULL,'{}');
          INSERT INTO entities VALUES('Products','product-rec','product-link','2 Door Upper',NULL,'lib-link','template-link','{"LinkIDSpecificationGroup":"group-link","Note":"searchable detail"}');''')
        db.commit(); db.close()
        review_server.CATALOG = self.dbpath
        review_server.CATALOG_SHA256 = hashlib.sha256(self.dbpath.read_bytes()).hexdigest()
        self.drawing = self.root / 'source.png'; self.drawing.write_bytes(b'test drawing')
        drawing_hash = hashlib.sha256(self.drawing.read_bytes()).hexdigest()
        self.model_path = self.root / 'model.json'
        self.model = {
            'schema_version':'0.1.0','project':{'name':'Test','units':'mm','revision':1},
            'source':{'file':'source.png','visual_review':{'source_sha256':drawing_hash,'inspected_pages':[1]}},
            'room':{'walls':[{'id':'W1','start':[0,0],'end':[2000,0],'height':2400,'thickness':100}], 'products':[{
                'id':'P1','label':'Upper','kind':'upper','library_product':{'name':'Old'},'wall_id':'W1','offset':0,
                'dimensions':{'width':500,'height':600,'depth':300},'visual_dimensions':{},'elevation':1000,'rotation':0,
                'dimension_evidence':{k:{'status':'confirmed','description':'test'} for k in ('width','height','depth')},
                'evidence':[],'prompt_overrides':[]}], 'features':[],
                'drawing_inventory':[{'id':'I1','label':'Upper','kind':'cabinet','evidence':['test'],'resolution':'model_product','model_id':'P1'}]},
            'review':{'issues':[]}}
        self.model_path.write_text(json.dumps(self.model), encoding='utf-8')
        self.manifest = self.root / 'active.json'
        self.manifest.write_text(json.dumps({'model':str(self.model_path),'pdf':str(self.drawing),'pdf_sha256':drawing_hash,'page_count':1}),encoding='utf-8')
        review_server.MANIFEST = self.manifest
        self.http = ThreadingHTTPServer(('127.0.0.1',0), review_server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(timeout=2)
        review_server.MANIFEST, review_server.CATALOG, review_server.CATALOG_SHA256 = self.old_manifest, self.old_catalog, self.old_catalog_hash
        self.temp.cleanup()

    def test_project_linked_upper_is_searchable_and_has_its_group(self):
        with urlopen(self.base+'/api/catalog/products?q=2%20Door%20Upper') as response:
            data=json.load(response)
        self.assertEqual(data['total'],1)
        self.assertEqual(data['results'][0]['name'],'2 Door Upper')
        self.assertEqual(data['results'][0]['project_link_id'],'template-link')
        self.assertEqual(data['results'][0]['specification_group']['link_id'],'group-link')
        with urlopen(self.base+'/api/catalog/products?q=searchable%20detail') as response:
            self.assertEqual(json.load(response)['total'],1)

    def test_save_endpoint_persists_only_submitted_draft_with_revision_guard(self):
        with urlopen(self.base+'/api/review') as response: revision=json.load(response)['revision']
        updated=json.loads(json.dumps(self.model)); updated['room']['products'][0]['dimensions']['width']=550
        request=Request(self.base+'/api/model',data=json.dumps(updated).encode(),headers={'Content-Type':'application/json','If-Match':revision},method='POST')
        with urlopen(request) as response: result=json.load(response)
        saved=json.loads(self.model_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['room']['products'][0]['dimensions']['width'],550)
        self.assertEqual(saved['project']['revision'],2)
        self.assertEqual(result['project_revision'],2)
        stale=Request(self.base+'/api/model',data=json.dumps(updated).encode(),headers={'Content-Type':'application/json','If-Match':revision},method='POST')
        with self.assertRaises(HTTPError) as raised: urlopen(stale)
        self.assertEqual(raised.exception.code,409)
        raised.exception.close()


if __name__ == '__main__':
    unittest.main()
