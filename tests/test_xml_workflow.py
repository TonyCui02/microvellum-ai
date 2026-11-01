import copy
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'src/mv_kitchen/xml_workflow.py'
spec = importlib.util.spec_from_file_location('xml_workflow', SCRIPT)
workflow = importlib.util.module_from_spec(spec); spec.loader.exec_module(workflow)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.dir = Path(self.tmp.name); self.catalog = self.dir / 'catalog.sqlite'
        with closing(sqlite3.connect(self.catalog)) as db:
            db.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE entities(table_name TEXT,record_id TEXT,name TEXT,library_link_id TEXT,fields_json TEXT,link_id TEXT); CREATE TABLE workbooks(table_name TEXT,record_id TEXT,compression TEXT,payload BLOB);')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [('schema_version','1'),('source_sqlite_sha256','a'*64)])
            db.executemany('INSERT INTO entities VALUES (?,?,?,?,?,?)', [('Products','p1','Cabinet & "Door"','lib',json.dumps({'Type':1}),None),('SpecificationGroups','g1','Group','lib','{}','group-link')])
            db.execute("INSERT INTO workbooks VALUES ('Products','p1','complete',?)", (zlib.compress(b'{"status":"ok"}'),))
            db.commit()
        self.model = {'schema_version':'1','target':{'build':'test','configuration':'test','output_units':'mm','coordinates':'native_unverified'},'catalog_sha256':workflow.digest(self.catalog),'review':{'status':'reviewed','reviewer':'test','note':'synthetic'},'project':{'name':'Test & Co','job_number':'101'},'locations':['Room'], 'groups':[{'name':'Group','record_id':'g1'}], 'products':[{'id':'one','record_id':'p1','name':'Cabinet & "Door"','location':'Room','group':'Group','item_number':'1.01','width':'600','height':'720','depth':'500','x':'0','y':'0','z':'0','angle':'0','prompts':[]}]}
        self.path = self.dir/'model.json'
    def tearDown(self): self.tmp.cleanup()
    def invoke(self, command='validate', *extra):
        self.path.write_text(json.dumps(self.model), encoding='utf8')
        return subprocess.run([sys.executable,str(SCRIPT),command,'--model',str(self.path),'--catalog',str(self.catalog),*map(str,extra)],capture_output=True,text=True)
    def test_export_roundtrip_and_manifest(self):
        out=self.dir/'out'; result=self.invoke('export','--experimental','--out',out)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads((out/'validation.json').read_text(encoding='utf8'))
        self.assertEqual(report['status'],'insufficient_evidence'); self.assertFalse(report['target_import_tested'])
        data=(out/'project.xml').read_bytes(); self.assertEqual(report['xml_sha256'],hashlib.sha256(data).hexdigest()); self.assertIn(b'&amp;',data)
        self.assertEqual(workflow.parse_xml(data)['products'][0]['name'],'Cabinet & "Door"')
        self.assertNotEqual(self.invoke('export','--experimental','--out',out).returncode,0)

    def test_comment_and_empty_source_job_number_round_trip(self):
        self.model['project']['job_number']=''
        self.model['products'][0]['comment']='2340|2340'
        # Empty job numbers are only valid for an explicitly identified SDF analysis model.
        self.model['source']={'kind':'canonical_project_analysis_copy','sqlite_sha256':'a'*64,'project_id':'p1','prompt_notes':[],
            'prompt_provenance':[],'unmapped_project_groups':[],'prompt_basis':'reference','library_match_verified':False,'global_prompt_notes':[]}
        result=self.invoke(); self.assertEqual(result.returncode,0,result.stderr)
        with closing(sqlite3.connect(self.catalog)) as db:
            actual=workflow.parse_xml(workflow.serialize(workflow.validate(self.model,db)))
        self.assertEqual(actual['project']['job_number'],'')
        self.assertEqual(actual['products'][0]['comment'],'2340|2340')
    def test_broken_links_values_and_unsupported_fields_block(self):
        for key, value in [('location','other'),('width','NaN'),('width','1,5'),('angle','1e2'),('name','cabinet & "Door"'),('bogus',[])]:
            with self.subTest(key=key,value=value):
                original=copy.deepcopy(self.model); self.model['products'][0][key]=value
                self.assertEqual(self.invoke().returncode,2); self.model=original
    def test_duplicates_and_fingerprint_block(self):
        self.model['products'].append(copy.deepcopy(self.model['products'][0])); self.assertEqual(self.invoke().returncode,2)
        self.model['products'].pop(); self.model['catalog_sha256']='bad'; self.assertEqual(self.invoke().returncode,2)

    def test_unresolved_source_quantity_blocks_export(self):
        self.model['source'] = {'kind':'canonical_project_analysis_copy','sqlite_sha256':'a'*64,
            'project_id':'p1','prompt_notes':[{'item_number':'P1','status':'unsupported_quantity','quantity':4}],
            'prompt_provenance':[],'unmapped_project_groups':[],'prompt_basis':'reference',
            'library_match_verified':False,'global_prompt_notes':[]}
        out = self.dir / 'blocked'
        result = self.invoke('export', '--experimental', '--out', out)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())

    def test_quantity_preserves_one_product_and_coordinates(self):
        self.model['products'][0]['quantity'] = '4'
        result = self.invoke('export', '--experimental', '--out', self.dir / 'quantity')
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = workflow.parse_xml((self.dir / 'quantity/project.xml').read_bytes())
        self.assertEqual(len(parsed['products']), 1)
        self.assertEqual(parsed['products'][0]['quantity'], '4')
        self.assertEqual([parsed['products'][0][axis] for axis in ('x','y','z')], ['0','0','0'])

    def test_invalid_quantities_block(self):
        for value in ('0', '-1', '1.5', '01', 4, True):
            with self.subTest(value=value):
                self.model['products'][0]['quantity'] = value
                self.assertEqual(self.invoke().returncode, 2)
    def test_exact_record_id_resolves_duplicate_name_and_incomplete_workbook(self):
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("INSERT INTO entities (table_name,record_id,name,library_link_id,fields_json) VALUES ('Products','p2','Cabinet & \"Door\"','lib','{}')")
            db.commit()
        self.model['catalog_sha256']=workflow.digest(self.catalog); self.assertEqual(self.invoke().returncode,0)
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("DELETE FROM entities WHERE record_id='p2'"); db.execute("UPDATE workbooks SET compression='incomplete_stream'")
            db.commit()
        self.model['catalog_sha256']=workflow.digest(self.catalog); self.assertEqual(self.invoke().returncode,0)
        self.model['products'][0]['prompts']=[{'name':'Adj_Shelf_Qty','value':'2'}]
        self.assertEqual(self.invoke().returncode,2)

    def test_prompt_override_is_checked_and_round_trips(self):
        workbook={'status':'ok','prompt_rows':[{'defined_names_for_value_cell':[{'name':'Adj_Shelf_Qty'}]}]}
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("UPDATE workbooks SET payload=?", (zlib.compress(json.dumps(workbook).encode()),))
            db.commit()
        self.model['catalog_sha256']=workflow.digest(self.catalog)
        self.model['products'][0]['prompts']=[{'name':'Adj_Shelf_Qty','value':'2'}]
        result=self.invoke(); self.assertEqual(result.returncode,0,result.stderr)
        with closing(sqlite3.connect(self.catalog)) as db:
            actual=workflow.parse_xml(workflow.serialize(workflow.validate(self.model,db)))
        self.assertEqual(actual['products'][0]['prompts'],[{'name':'Adj_Shelf_Qty','value':'2'}])

    def test_specification_group_global_prompt_round_trips(self):
        global_index={'status':'ok','defined_names':[{'name':'Shadow_Line','expression':'Prompts!$M$1'}],
                      'prompt_rows':[{'row':1,'cells':{'M':{'address':'M1','value':'65'}}}]}
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("UPDATE entities SET fields_json=? WHERE table_name='SpecificationGroups' AND record_id='g1'", (json.dumps({'Name':'Group','LinkIDGlobalFileName':'global-link'}),))
            db.execute("INSERT INTO entities (table_name,record_id,name,library_link_id,fields_json,link_id) VALUES ('GlobalFiles','gf1','Globals','lib','{}','global-link')")
            db.execute("INSERT INTO workbooks VALUES ('GlobalFiles','gf1','complete',?)", (zlib.compress(json.dumps(global_index).encode()),))
            db.commit()
        self.model['catalog_sha256']=workflow.digest(self.catalog)
        self.model['groups'][0].update({'global_name':'Globals','global_prompts':[{'name':'Shadow_Line','value':'65'}]})
        result=self.invoke(); self.assertEqual(result.returncode,0,result.stderr)
        with closing(sqlite3.connect(self.catalog)) as db:
            actual=workflow.parse_xml(workflow.serialize(workflow.validate(self.model,db)))
        self.assertEqual(actual['groups'][0]['global_prompts'],[{'name':'Shadow_Line','value':'65'}])
    def test_parser_rejects_mutation_and_external_entities(self):
        with closing(sqlite3.connect(self.catalog)) as db: data=workflow.serialize(workflow.validate(self.model, db))
        with self.assertRaises(workflow.Invalid): workflow.parse_xml(data.replace(b'<Width>',b'<width>'))
        with self.assertRaises(workflow.Invalid): workflow.parse_xml(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///x">]><a>&x;</a>')
    def test_explicit_experimental_flag_required(self):
        self.assertEqual(self.invoke('export','--out',self.dir/'out').returncode,2)

    def test_current_catalog_schema_is_supported_and_unknown_versions_block(self):
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
            db.commit()
        self.model['catalog_sha256'] = workflow.digest(self.catalog)
        self.assertEqual(self.invoke().returncode, 0)
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("UPDATE metadata SET value='3' WHERE key='schema_version'")
            db.commit()
        self.model['catalog_sha256'] = workflow.digest(self.catalog)
        self.assertEqual(self.invoke().returncode, 2)

    def test_record_id_disambiguates_duplicate_library_names(self):
        with closing(sqlite3.connect(self.catalog)) as db:
            db.execute("INSERT INTO entities (table_name,record_id,name,library_link_id,fields_json) VALUES ('Products','p2','Cabinet & \"Door\"','lib','{}')")
            db.execute("INSERT INTO workbooks VALUES ('Products','p2','complete',?)", (zlib.compress(b'{\"status\":\"ok\"}'),))
            db.commit()
        self.model['catalog_sha256']=workflow.digest(self.catalog)
        self.assertEqual(self.invoke().returncode,0)

if __name__ == '__main__': unittest.main()
