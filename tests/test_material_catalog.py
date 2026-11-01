import json
import sqlite3
import unittest
import zlib

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'poc'))
from material_catalog import search_materials


class MaterialCatalogTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
          CREATE TABLE entities(table_name TEXT,record_id TEXT,link_id TEXT,name TEXT,category_link_id TEXT,library_link_id TEXT,project_link_id TEXT,fields_json TEXT);
          CREATE TABLE workbooks(table_name TEXT,record_id TEXT,link_id TEXT,blob_sha256 TEXT,compression TEXT,payload BLOB);
        ''')
        fields={'LinkIDCutPartsFileName':'cut-wb','LinkIDEdgeBandFileName':'edge-wb','LinkIDHardwareFileName':'hw-wb'}
        self.db.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?,?)',('SpecificationGroups','g','group-link','Kitchen Library',None,'lib',None,json.dumps(fields)))
        for rid,link,name,typ,thickness in [('m1','mat-1','White Satin Board',2,16),('m2','mat-2','White Melamine',2,18),('e1','edge-1','1mm White',5,1),('m3','mat-3','Unrelated white sheet',2,16)]:
            raw={'Type':typ,'Thickness':thickness}
            self.db.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?,?)',('Materials',rid,link,name,'cat','lib',None,json.dumps(raw)))
        self._workbook('CutPartsFiles','cut-wb',[
            {'sheet':'SheetStockLibrary','cells':{'A':'White Satin Board','E':'mat-1'}},
            {'sheet':'SheetStockLibrary','cells':{'A':'White Melamine','E':'mat-2'}},
            {'sheet':'CutParts','cells':{'A':'Cabinet_Body','B':'White Satin Board','D':'mat-1'}},
        ])
        self._workbook('EdgebandFiles','edge-wb',[
            {'sheet':'EdgeBandLibrary','cells':{'A':'1mm White','E':'edge-1'}},
            {'sheet':'EdgebandParts','cells':{'A':'Cabinet_Body_Edges','B':'1mm White','D':'edge-1'}},
        ])
        self._workbook('HardwareFiles','hw-wb',[{'sheet':'HardwareLibrary','cells':{'A':'Hinge','E':'hardware-1'}}])

    def _workbook(self,table,link,library_rows):
        payload=zlib.compress(json.dumps({'status':'ok','library_rows':library_rows}).encode())
        self.db.execute('INSERT INTO workbooks VALUES (?,?,?,?,?,?)',(table,table+'-id',link,'hash','complete',payload))

    def test_search_is_scoped_by_group_component_and_role(self):
        panels,total=search_materials(self.db,'Kitchen Library','panel_finish','white')
        self.assertEqual(total,2)
        self.assertEqual({x['link_id'] for x in panels},{'mat-1','mat-2'})
        self.assertEqual(next(x for x in panels if x['link_id']=='mat-1')['group_assigned'],True)
        edges,total=search_materials(self.db,'Kitchen Library','edging','white')
        self.assertEqual(total,1)
        self.assertEqual(edges[0]['link_id'],'edge-1')
        self.assertTrue(edges[0]['group_assigned'])

    def test_exact_group_link_disambiguates_duplicate_group_names(self):
        self.db.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?,?)',
                        ('SpecificationGroups','g2','other-group-link','Kitchen Library',None,'lib',None,json.dumps({})))
        panels,total=search_materials(self.db,'Kitchen Library','panel_finish','white',group_link_id='group-link')
        self.assertEqual(total,2)
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):
            search_materials(self.db,'Kitchen Library','panel_finish','white')

    def test_broad_unrelated_records_do_not_appear(self):
        results,total=search_materials(self.db,'Kitchen Library','panel_finish','unrelated')
        self.assertEqual(results,[])
        self.assertEqual(total,0)

    def test_unindexed_component_fails_closed(self):
        self.db.execute("DELETE FROM workbooks WHERE table_name='CutPartsFiles'")
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):
            search_materials(self.db,'Kitchen Library','panel_finish')

    def test_handle_profile_is_not_a_material_catalog_role(self):
        with self.assertRaisesRegex(ValueError,'not a material-catalog role'):
            search_materials(self.db,'Kitchen Library','handle_profile')


if __name__=='__main__':
    unittest.main()
