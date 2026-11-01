import subprocess
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / 'scripts/mv.py'


class EntrypointTests(unittest.TestCase):
    def test_help_and_command_argument_forwarding(self):
        for args, expected in ((['--help'], 'render'), (['render', '--help'], '--out'),
                               (['export', '--help'], '--catalog')):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(ENTRYPOINT), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_unknown_command_fails(self):
        result = subprocess.run([sys.executable, str(ENTRYPOINT), 'unknown'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_bundled_catalog_is_used_for_search(self):
        result = subprocess.run([sys.executable, str(ENTRYPOINT), 'catalog', 'search', 'sink', '--limit', '1'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['products'])


if __name__ == '__main__':
    unittest.main()
