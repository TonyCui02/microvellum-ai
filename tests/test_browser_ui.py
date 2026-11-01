"""Optional real-browser smoke test; Playwright is a development-only dependency."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import test_poc_editor

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('MV_PLAYWRIGHT_PATH') and os.environ.get('MV_BROWSER_EXECUTABLE')
                     and shutil.which('node'), 'Set MV_PLAYWRIGHT_PATH and MV_BROWSER_EXECUTABLE for browser test')
class BrowserUITests(unittest.TestCase):
    def test_edit_and_reload(self):
        fixture = test_poc_editor.POCEditorIntegrationTests('test_save_endpoint_persists_only_submitted_draft_with_revision_guard')
        fixture.setUp()
        try:
            env = dict(os.environ, MV_REVIEW_URL=fixture.base)
            result = subprocess.run(['node', str(ROOT / 'tests/ui/review-smoke.cjs')], env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
