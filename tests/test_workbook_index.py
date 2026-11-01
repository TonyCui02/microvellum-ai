"""Focused checks for lossless workbook metadata extraction."""
import io
from pathlib import Path
import sys
import unittest
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'investigation/sqlce'))
from index_workbooks import workbook_index


def fixture():
    files = {
        'xl/workbook.xml': '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Prompts" r:id="r1"/></sheets><definedNames><definedName name="Width">Prompts!$B$1</definedName></definedNames></workbook>''',
        'xl/_rels/workbook.xml.rels': '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml" Type="worksheet"/></Relationships>''',
        'xl/sharedStrings.xml': '''<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Width</t></si><si><t>Hidden label</t></si></sst>''',
        'xl/worksheets/sheet1.xml': '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="s"><v>0</v></c><c><f t="shared" si="7" ref="B1:B3">A1</f><v>900</v></c></row><row r="3"><c t="s"><v>1</v></c><c><f t="shared" si="7"/><v>4</v></c></row></sheetData></worksheet>''',
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return zlib.compress(out.getvalue())


def material_fixture():
    files = {
        'xl/workbook.xml': '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="SheetStockLibrary" r:id="r1"/><sheet name="CutParts" r:id="r2"/><sheet name="Prompts" r:id="r3"/></sheets></workbook>''',
        'xl/_rels/workbook.xml.rels': '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml" Type="worksheet"/><Relationship Id="r2" Target="worksheets/sheet2.xml" Type="worksheet"/><Relationship Id="r3" Target="worksheets/sheet3.xml" Type="worksheet"/></Relationships>''',
        'xl/sharedStrings.xml': '''<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>White Satin Board</t></si><si><t>material-link-1</t></si><si><t>Carcase_Panel</t></si><si><t>White Satin Board</t></si><si><t>material-link-1</t></si><si><t>Width</t></si></sst>''',
        'xl/worksheets/sheet1.xml': '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>16</v></c><c r="E1" t="s"><v>1</v></c></row></sheetData></worksheet>''',
        'xl/worksheets/sheet2.xml': '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>2</v></c><c r="B1" t="s"><v>3</v></c><c r="D1" t="s"><v>4</v></c></row></sheetData></worksheet>''',
        'xl/worksheets/sheet3.xml': '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>5</v></c></row></sheetData></worksheet>''',
    }
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,value in files.items(): archive.writestr(name,value)
    return zlib.compress(out.getvalue())


class WorkbookIndexTests(unittest.TestCase):
    def test_omitted_coordinates_names_hidden_rows_and_formula_cache(self):
        result = workbook_index(fixture())
        first, hidden = result['prompt_rows']
        self.assertEqual(first['row'], 1)
        self.assertEqual(first['cells']['A']['value'], 'Width')
        self.assertEqual(first['cells']['B']['value'], '900')
        self.assertEqual(first['defined_names_for_value_cell'][0]['name'], 'Width')
        self.assertEqual(hidden['row'], 3)
        self.assertEqual(hidden['cells']['A']['value'], 'Hidden label')
        self.assertEqual(hidden['cells']['B']['value'], '4')
        self.assertEqual(hidden['cells']['B']['formula_status'], 'shared_master_identified_translation_required')
        self.assertEqual(hidden['cells']['B']['shared_formula_master']['formula'], 'A1')

    def test_incomplete_stream_is_flagged(self):
        result = workbook_index(fixture()[:-4])
        self.assertEqual(result['compression'], 'incomplete_stream')
        self.assertEqual(result['zip_crc'], 'ok')

    def test_library_workbook_rows_include_membership_and_pointer_values(self):
        result=workbook_index(material_fixture())
        by_sheet={}
        for row in result['library_rows']: by_sheet.setdefault(row['sheet'],[]).append(row)
        self.assertEqual(by_sheet['SheetStockLibrary'][0]['cells']['E'],'material-link-1')
        self.assertEqual(by_sheet['CutParts'][0]['cells']['D'],'material-link-1')
        self.assertNotIn('Prompts',by_sheet)


if __name__ == '__main__':
    unittest.main()
