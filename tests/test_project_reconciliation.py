"""Ensure project facts cannot silently disappear in analysis-model export."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'investigation/sqlce'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'investigation/sqlce' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


project = load('project_to_model')
comparison = load('compare_project_xml')


class ProjectReconciliationTests(unittest.TestCase):
    def test_invalid_quantity_is_a_blocking_source_note(self):
        self.assertIsNone(project.quantity_note({'Quantity': 1, 'ItemNumber': 'P1'}))
        self.assertIsNone(project.quantity_note({'Quantity': 4, 'ItemNumber': 'P1'}))
        self.assertEqual(project.quantity_note({'Quantity': 0, 'ItemNumber': 'P1'}),
                         {'item_number': 'P1', 'status': 'invalid_quantity', 'quantity': 0})

    def test_comparison_includes_changed_literal_text(self):
        instance = {'Drawer_Type': {'value': 'Choice & Co', 'formula': None, 'input_type': '5'}}
        library = {'Drawer_Type': {'value': 'Other', 'formula': None}}
        with patch.object(comparison, 'workbook_values', return_value=instance), \
             patch.object(comparison, 'indexed_values', return_value=library):
            self.assertEqual(comparison.prompt_candidates(None, None), {'Drawer_Type': 'Choice & Co'})


if __name__ == '__main__':
    unittest.main()
