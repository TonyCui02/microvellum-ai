"""Exercise the drawing-to-XML plumbing with generated, non-customer data."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

from test_drawing_workflow import sample
from test_xml_workflow import workflow

ROOT = Path(__file__).resolve().parents[1]


def make_pdf():
    """Build a minimal one-page PDF without an external PDF library."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>',
               b'<< /Length 0 >>\nstream\n\nendstream']
    result = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(result)
    result.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        result.extend(f'{offset:010d} 00000 n \n'.encode())
    result.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(result)


@unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftoppm'), 'Poppler tools required')
class DrawingExportIntegrationTests(unittest.TestCase):
    def test_pdf_review_model_and_catalog_to_xml(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            drawing = root / 'drawing.pdf'
            drawing.write_bytes(make_pdf())
            source_hash = hashlib.sha256(drawing.read_bytes()).hexdigest()
            render = subprocess.run([sys.executable, str(ROOT / 'src/mv_kitchen/render_drawing.py'),
                                     str(drawing), '--out', str(root / 'pages'), '--dpi', '72'],
                                    capture_output=True, text=True)
            self.assertEqual(render.returncode, 0, render.stderr)
            manifest = json.loads((root / 'pages/manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['source_sha256'], source_hash)
            self.assertEqual(manifest['page_count'], 1)
            self.assertEqual(hashlib.sha256(drawing.read_bytes()).hexdigest(), source_hash)

            catalog = root / 'catalog.sqlite'
            with closing(sqlite3.connect(catalog)) as db:
                db.executescript('''CREATE TABLE metadata(key TEXT, value TEXT);
                    CREATE TABLE entities(table_name TEXT,record_id TEXT,name TEXT,link_id TEXT,
                        library_link_id TEXT,category_link_id TEXT,fields_json TEXT);
                    CREATE TABLE workbooks(table_name TEXT,record_id TEXT,compression TEXT,payload BLOB);''')
                db.execute('INSERT INTO metadata VALUES (?,?)', ('source_sqlite_sha256', 'a' * 64))
                db.executemany('INSERT INTO entities VALUES (?,?,?,?,?,?,?)', [
                    ('SpecificationGroups', 'g1', 'Test Group', 'g-link', 'library', None, '{}'),
                    ('Products', 'p1', 'Synthetic Cabinet', 'p-link', 'library', 'category',
                     json.dumps({'Name': 'Synthetic Cabinet', 'Type': 1}))])
                db.execute('INSERT INTO workbooks VALUES (?,?,?,?)',
                           ('Products', 'p1', 'complete', zlib.compress(b'{"status":"ok"}')))
                db.commit()

            model = sample()
            model['project'].update({'job_number': 'JOB-123', 'revision': 1,
                                     'catalog_sha256': hashlib.sha256(catalog.read_bytes()).hexdigest(),
                                     'specification_groups': [{'name': 'Test Group'}]})
            model['room']['name'] = 'Test Room'
            item = model['room']['products'][0]
            item['library_product'] = {'record_id': 'p1', 'name': 'Synthetic Cabinet'}
            item['specification_group'] = {'name': 'Test Group', 'link_id': 'g-link'}
            item['quantity'] = 4
            item['dimensions']['depth'] = 300
            item['dimension_evidence']['depth'] = {'status': 'confirmed', 'description': 'synthetic input'}
            model['review'].update({'approved_by': 'Synthetic reviewer', 'approved_at': '2026-01-01',
                                    'approved_revision': 1})
            model['source']['file'] = drawing.name
            model['source']['path'] = drawing.name
            model['source']['visual_review']['source_sha256'] = source_hash
            model_path = root / 'model.json'
            model_path.write_text(json.dumps(model), encoding='utf-8')
            script = ROOT / 'poc/export_experimental_xml.py'
            output = root / 'output'
            command = [sys.executable, str(script), '--model', str(model_path), '--catalog', str(catalog),
                       '--out', str(output)]
            exported = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            data = (output / 'project.xml').read_bytes()
            xml = ET.fromstring(data)
            self.assertEqual(xml.findtext('./Project/JobNumber'), 'JOB-123')
            self.assertEqual(xml.find('./Project/Locations/Location').get('Name'), 'Test Room')
            self.assertEqual(xml.findtext('./Project/Products/Product/Width'), '400')
            self.assertEqual(xml.findtext('./Project/Products/Product/Quantity'), '4')
            self.assertEqual(workflow.parse_xml(data)['products'][0]['name'], 'Synthetic Cabinet')
            self.assertEqual(json.loads((output / 'validation.json').read_text(encoding='utf-8'))['xml_sha256'],
                             hashlib.sha256(data).hexdigest())
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

            model['source']['visual_review']['inspected_pages'] = [1, 2]
            model_path.write_text(json.dumps(model), encoding='utf-8')
            invalid_pages = subprocess.run(command[:-1] + [str(root / 'invalid-pages')], capture_output=True, text=True)
            self.assertNotEqual(invalid_pages.returncode, 0)
            self.assertIn('every source page', invalid_pages.stderr)
            model['source']['visual_review']['inspected_pages'] = [1]
            del model['project']['job_number']
            model_path.write_text(json.dumps(model), encoding='utf-8')
            blocked = subprocess.run(command[:-1] + [str(root / 'missing-job')], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn('project.job_number', blocked.stderr)
            self.assertFalse((root / 'missing-job').exists())
            model['project']['job_number'] = 'JOB-123'
            model_path.write_text(json.dumps(model), encoding='utf-8')
            drawing.write_bytes(drawing.read_bytes() + b'changed')
            stale = subprocess.run(command[:-1] + [str(root / 'stale-source')], capture_output=True, text=True)
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn('drawing hash', stale.stderr)
            self.assertFalse((root / 'stale-source').exists())


class BundledCatalogIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftoppm'), 'Poppler tools required')
    def test_default_catalog_search_to_reviewed_model_and_xml(self):
        catalog = ROOT / 'data/catalog/kitchen-library-v7.1.sqlite'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            drawing = root / 'drawing.pdf'
            drawing.write_bytes(make_pdf())
            digest = hashlib.sha256(drawing.read_bytes()).hexdigest()
            rendered = subprocess.run([sys.executable, str(ROOT / 'scripts/mv.py'), 'render', str(drawing),
                                       '--out', str(root / 'pages'), '--dpi', '72'], capture_output=True, text=True)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            with closing(sqlite3.connect(catalog.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                product = db.execute("SELECT record_id,name FROM entities WHERE table_name='Products' AND name='2 Door Base' ORDER BY record_id LIMIT 1").fetchone()
                group = db.execute("SELECT name FROM entities WHERE table_name='SpecificationGroups' AND name='Kitchen Library'").fetchone()
            self.assertIsNotNone(product)
            self.assertIsNotNone(group)
            model = sample()
            model['project'].update({'job_number':'TEST', 'revision':1,
                                     'catalog_sha256':hashlib.sha256(catalog.read_bytes()).hexdigest(),
                                     'specification_groups':[{'name':group[0]}]})
            model['room']['name'] = 'Synthetic Room'
            model['source'].update({'path':drawing.name, 'file':drawing.name})
            model['source']['visual_review']['source_sha256'] = digest
            item = model['room']['products'][0]
            item['library_product'] = {'record_id':product[0], 'name':product[1]}
            item['dimensions']['depth'] = 300
            item['dimension_evidence']['depth'] = {'status':'confirmed', 'description':'synthetic input'}
            model['review'].update({'approved_by':'Test reviewer', 'approved_at':'2026-01-01', 'approved_revision':1})
            model_path = root / 'review.json'
            model_path.write_text(json.dumps(model), encoding='utf-8')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/mv.py'), 'export', '--model', str(model_path),
                                     '--out', str(root / 'out')], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            xml = workflow.parse_xml((root / 'out/project.xml').read_bytes())
            self.assertEqual(xml['products'][0]['name'], product[1])
            report = json.loads((root / 'out/validation.json').read_text(encoding='utf-8'))
            self.assertEqual(report['catalog_sha256'], model['project']['catalog_sha256'])
            self.assertFalse(report['target_import_tested'])


if __name__ == '__main__':
    unittest.main()
