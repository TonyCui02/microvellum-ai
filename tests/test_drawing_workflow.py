import struct
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
render=load('render_drawing','src/mv_kitchen/render_drawing.py')
validate=load('model_validation','poc/model_validation.py')
prompts=load('inspect_prompts','investigation/sqlce/inspect_product_prompts.py')
def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
PNG=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x00\x00\x00'))+chunk(b'IEND',b'')

def sample():
    visual_check={view:{'status':'matches_source','source_pages':[1],'evidence':'Compared modeled geometry with the source drawing.'} for view in ('front','top','3d')}
    depth_search={'views_checked':['plan','front elevation','section','notes'],'source_pages':[1],'result':'Inspected the applicable views and notes; no depth value is shown.'}
    return {'project':{'name':'Example','units':'mm'},'source':{'visual_review':{'inspected_pages':[1]}},'review':{'issues':[],'visual_self_check':visual_check},'room':{'walls':[{'id':'W','start':[0,0],'end':[1000,0],'height':2400,'thickness':100}], 'drawing_inventory':[{'id':'A-view','label':'Cabinet','kind':'cabinet','resolution':'model_product','model_id':'A','evidence':['Page 1 plan']}], 'products':[{'id':'A','label':'Cabinet','wall_id':'W','offset':0,'elevation':0,'dimensions':{'width':400,'height':700,'depth':None},'visual_dimensions':{'depth':300},'dimension_evidence':{'width':{'status':'observed','page':1,'description':'Plan'},'height':{'status':'observed','page':1,'description':'Elevation'},'depth':{'status':'unknown','page':1,'description':'Not shown','search':depth_search}},'evidence':[],'prompt_overrides':[]} ]}}

class DrawingWorkflowTests(unittest.TestCase):
    def test_png_copy_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.png';source.write_bytes(PNG);out=Path(d)/'out';data=render.render(source,out)
            self.assertEqual((data['page_count'],data['pages'][0]['width_px']),(1,1));self.assertEqual((out/'page-1.png').read_bytes(),PNG)
            with self.assertRaises(ValueError):render.render(source,out)
            self.assertEqual(source.read_bytes(),PNG)
    def test_evidence_and_unsupported_geometry_are_blocked(self):
        good=sample();self.assertEqual(validate.validate_model(good),[])
        self.assertEqual(validate.validate_model(good,require_visual_self_check=True),[])
        missing_check=copy.deepcopy(good);missing_check['review'].pop('visual_self_check')
        self.assertTrue(any('self-check' in e for e in validate.validate_model(missing_check,require_visual_self_check=True)))
        bad_check=copy.deepcopy(good);bad_check['review']['visual_self_check']['top']['source_pages']=[2]
        self.assertTrue(any('visually inspected' in e for e in validate.validate_model(bad_check,require_visual_self_check=True)))
        bad=copy.deepcopy(good);bad['room']['products'][0]['dimension_evidence']['depth']['status']='observed'
        self.assertTrue(any('status disagree' in e for e in validate.validate_model(bad)))
        bad=copy.deepcopy(good);bad['room']['products'][0]['dimension_evidence']['depth'].pop('search')
        self.assertTrue(any('views checked' in e for e in validate.validate_model(bad)))
        bad=copy.deepcopy(good);bad['room']['drawing_inventory'][0]['resolution']='unresolved'
        self.assertTrue(any('is unresolved' in e for e in validate.validate_model(bad)))
        two=copy.deepcopy(good);two['room']['walls'].append({'id':'W2','start':[1000,0],'end':[1000,1000],'height':2400,'thickness':100});other=copy.deepcopy(two['room']['products'][0]);other['id']='B';other['wall_id']='W2';two['room']['products'].append(other);two['room']['drawing_inventory'].append({'id':'B-view','label':'Cabinet B','kind':'cabinet','resolution':'model_product','model_id':'B','evidence':['Page 1 plan']});self.assertEqual(validate.validate_model(two),[])
        bad=copy.deepcopy(good);bad['room']['products'][0]['rotation']=45
        self.assertTrue(any('rotated items' in e for e in validate.validate_model(bad)))
        bad=copy.deepcopy(good);bad['room']['products'][0]['quantity']=0
        self.assertTrue(any('quantity' in e for e in validate.validate_model(bad)))
        bad=copy.deepcopy(good);bad['room']['products'][0]['wall_id']='missing'
        self.assertTrue(any('invalid wall' in e for e in validate.validate_model(bad)))
        bad=copy.deepcopy(good);bad['room']['products'][0]['dimension_evidence']['width']['page']=2
        self.assertTrue(any('inspected source page' in e for e in validate.validate_model(bad)))
    def test_prompt_names_are_not_treated_as_validated_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'catalog.sqlite';db=sqlite3.connect(file)
            db.executescript('CREATE TABLE entities(table_name,record_id,name,link_id,category_link_id,library_link_id); CREATE TABLE workbooks(table_name,record_id,payload);')
            wb={'status':'ok','compression':'incomplete_stream','prompt_rows':[{'row':4,'defined_names_for_value_cell':[{'name':'Adj_Shelf_Qty'}],'cells':{'A':{'value':'Shelf'},'B':{'value':'1','formula':'[1]!Qty'}}}]}
            db.execute('insert into entities values(?,?,?,?,?,?)',('Products','id','Upper','L','C','Lib'));db.execute('insert into workbooks values(?,?,?)',('Products','id',zlib.compress(json.dumps(wb).encode())));db.commit();db.close()
            result=prompts.inspect(file,'id',['Adj_Shelf_Qty','NoSuchPrompt'])
            self.assertEqual([x['status'] for x in result['requested_checks']],['unique_name_match','missing']);self.assertEqual(result['prompts'][0]['formula'],'[1]!Qty');self.assertEqual(result['workbook_compression'],'incomplete_stream')

if __name__=='__main__': unittest.main()
