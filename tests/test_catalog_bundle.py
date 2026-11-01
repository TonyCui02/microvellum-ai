import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_catalog_bundle', ROOT / 'scripts/prepare_catalog_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class CatalogBundleTests(unittest.TestCase):
    def test_copy_redacts_local_path_and_preserves_other_data(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.sqlite'
            output = Path(temp) / 'bundle.sqlite'
            db = sqlite3.connect(source)
            db.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE entities(table_name TEXT,record_id TEXT,fields_json TEXT);
                CREATE TABLE workbooks(table_name TEXT,record_id TEXT,payload BLOB);''')
            workbook = {'external_relationships':[{'target':r'file:///C:\Users\someone\Desktop\file.cutx'}],
                        'prompt_rows':[{'name':'Width'}]}
            db.execute('INSERT INTO entities VALUES (?,?,?)', ('Products', 'p', '{"Name":"Cabinet"}'))
            db.execute('INSERT INTO workbooks VALUES (?,?,?)', ('Products', 'p', zlib.compress(json.dumps(workbook).encode())))
            db.commit()
            db.close()
            before = source.read_bytes()
            self.assertEqual(bundle.prepare(source, output), 1)
            self.assertEqual(source.read_bytes(), before)
            db = sqlite3.connect(output)
            try:
                result = json.loads(zlib.decompress(db.execute('SELECT payload FROM workbooks').fetchone()[0]))
                self.assertEqual(result['external_relationships'][0]['target'], '[machine-local path redacted]')
                self.assertEqual(result['prompt_rows'], [{'name':'Width'}])
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally:
                db.close()
            with self.assertRaises(FileExistsError):
                bundle.prepare(source, output)


if __name__ == '__main__':
    unittest.main()
