import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/mv_kitchen'))
import rules


class RulesTests(unittest.TestCase):
    def test_evaluation_and_stale_snapshot(self):
        book = {'schema_version': 1, 'rules': [{'id': 'depth', 'version': 1, 'enabled': True,
                'title': 'Depth', 'instruction': 'Check depth', 'scope': 'Cabinets', 'source': 'Shop'}]}
        record = rules.snapshot(book)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            rules.check(record, book)
        record['evaluations'][0].update(status='applied', reason='Page 1; 20 mm deduction', affected_items=['P1'])
        self.assertEqual(rules.check(record, book), rules.fingerprint(book))
        changed = copy.deepcopy(book); changed['rules'][0]['version'] = 2
        with self.assertRaisesRegex(ValueError, 'changed'):
            rules.check(record, changed)
        self.assertEqual(rules.check(record, changed, True), rules.fingerprint(book))
        record['evaluations'].clear()
        with self.assertRaisesRegex(ValueError, 'Every enabled'):
            rules.check(record, book)

    def test_commands_persist_and_snapshot_clears_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            book = Path(temp) / 'rules.json'; model = Path(temp) / 'model.json'
            command = [sys.executable, str(ROOT / 'scripts/mv.py'), 'rules', '--rulebook', str(book)]
            result = subprocess.run(command + ['add', 'door-depth', '--title', 'Depth', '--instruction', 'Check depth',
                                    '--scope', 'Cabinets', '--source', 'User'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('door-depth', [r['id'] for r in rules.load(book)['rules']])
            model.write_text(json.dumps({'project': {'revision': 2}, 'review': {'approved_by': 'User', 'approved_revision': 2}}))
            result = subprocess.run(command + ['snapshot', '--model', str(model)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(model.read_text()); self.assertEqual(data['project']['revision'], 3)
            self.assertNotIn('approved_by', data['review'])
            self.assertEqual(data['review']['rules']['evaluations'][0]['status'], 'pending')
            result = subprocess.run(command + ['disable', 'door-depth'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(next(r for r in rules.load(book)['rules'] if r['id'] == 'door-depth')['enabled'])
            self.assertEqual(rules.load(book)['history'][0]['version'], 1)

    def test_defaults_merge_without_overwriting_or_reenabling(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rules.json'
            defaults = rules.load(path)
            self.assertEqual(len(defaults['rules']), 2)
            modified = copy.deepcopy(defaults['rules'][0]); modified.update(enabled=False, title='Custom title', version=2)
            rules.write(path, {'schema_version': 1, 'rules': [modified]})
            merged = rules.load(path)
            self.assertEqual(len(merged['rules']), 2)
            self.assertEqual(next(r for r in merged['rules'] if r['id'] == modified['id']), modified)


if __name__ == '__main__':
    unittest.main()
