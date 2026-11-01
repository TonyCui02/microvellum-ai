"""Create a research JSONL index of workbook metadata from a SQLite snapshot.

The index contains source library data. Keep it outside tracked source paths.
It does not calculate formulas or decide which prompts can be overridden.
"""
import argparse
import hashlib
import io
import json
import posixpath
from pathlib import Path
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib

from decode_workbook import LIMIT, TABLES

M = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
P = '{http://schemas.openxmlformats.org/package/2006/relationships}'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode(blob):
    if blob.startswith(b'PK\x03\x04'):
        return blob, 'direct_zip'
    obj = zlib.decompressobj()
    data = obj.decompress(blob, LIMIT + 1)
    if len(data) > LIMIT or obj.unconsumed_tail or obj.unused_data:
        raise ValueError('oversize or unexpected trailing compressed data')
    return data, 'complete' if obj.eof else 'incomplete_stream'


def text_content(element):
    return ''.join(node.text or '' for node in element.iter(M + 't'))


def column_number(label):
    value = 0
    for char in label.upper():
        value = value * 26 + ord(char) - ord('A') + 1
    return value


def column_label(number):
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def workbook_index(blob):
    data, compression = decode(blob)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > LIMIT:
            raise ValueError('unpacked workbook exceeds limit')
        bad = z.testzip()
        if bad:
            raise ValueError('ZIP CRC failed: ' + bad)
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        relationships = {r.get('Id'): r for r in rels.findall(P + 'Relationship')}
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            shared = [text_content(si) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(M + 'si')]
        sheets = []
        for sheet in wb.findall('.//' + M + 'sheet'):
            rel = relationships.get(sheet.get(R + 'id'))
            if rel is None or rel.get('TargetMode') == 'External':
                raise ValueError('missing or external worksheet relationship')
            target = rel.get('Target', '')
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            if not path.startswith('xl/'):
                raise ValueError('worksheet path outside xl')
            sheets.append({'name': sheet.get('name'), 'state': sheet.get('state', 'visible'), 'path': path})
        names = [{'name': n.get('name'), 'scope_sheet_index': n.get('localSheetId'),
                  'expression': n.text, 'hidden': n.get('hidden')} for n in wb.findall('.//' + M + 'definedName')]
        external = []
        for relpath in (n for n in z.namelist() if n.endswith('.rels')):
            for rel in ET.fromstring(z.read(relpath)).findall(P + 'Relationship'):
                if rel.get('TargetMode') == 'External' or rel.get('Type', '').endswith('/externalLink'):
                    external.append({'relationship_file': relpath, 'target': rel.get('Target'), 'type': rel.get('Type')})
        prompt_names = {}
        for name in names:
            match = re.fullmatch(r"(?:'Prompts'|Prompts)!\$?B\$?(\d+)", name['expression'] or '', re.I)
            if match:
                prompt_names.setdefault(int(match.group(1)), []).append(name)
        prompts = []
        library_rows = []
        for sheet in sheets:
            relevant_library_sheets = {
                'CutPartsFiles': {'cutparts', 'sheetstocklibrary', 'solidstocklibrary', 'buyoutlibrary'},
                'EdgebandFiles': {'edgebandparts', 'edgebandlibrary'},
                'HardwareFiles': {'hardwareparts', 'hardwarelibrary'},
            }
            table = next((name for name, tables in relevant_library_sheets.items()
                          if sheet['name'].casefold() in tables), None)
            if sheet['name'].casefold() != 'prompts' and not table:
                continue
            root = ET.fromstring(z.read(sheet['path']))
            previous_row = 0
            for row in root.findall('.//' + M + 'sheetData/' + M + 'row'):
                row_number = int(row.get('r')) if row.get('r') else None
                if row_number is None:
                    first_address = next((c.get('r') for c in row.findall(M + 'c') if c.get('r')), None)
                    match = re.fullmatch(r'[A-Za-z]+(\d+)', first_address or '')
                    row_number = int(match.group(1)) if match else previous_row + 1
                if row_number <= previous_row:
                    raise ValueError('non-increasing worksheet rows')
                previous_row = row_number
                cells = {}
                previous_column = 0
                for cell in row.findall(M + 'c'):
                    address = cell.get('r')
                    if address:
                        match = re.fullmatch(r'([A-Za-z]+)(\d+)', address)
                        if not match or int(match.group(2)) != row_number:
                            raise ValueError('invalid worksheet cell coordinate')
                        col = column_number(match.group(1))
                    else:
                        col = previous_column + 1
                        address = column_label(col) + str(row_number)
                    if col <= previous_column:
                        raise ValueError('non-increasing worksheet columns')
                    previous_column = col
                    kind = cell.get('t', 'n')
                    v = cell.find(M + 'v')
                    formula = cell.find(M + 'f')
                    raw = v.text if v is not None else None
                    value = raw
                    if kind == 's' and raw is not None:
                        idx = int(raw)
                        if idx >= len(shared) or idx < 0:
                            raise ValueError('invalid shared string index')
                        value = shared[idx]
                    elif kind == 'inlineStr':
                        value = text_content(cell)
                    if value is None and formula is None:
                        continue
                    entry = {'address': address, 'type': kind, 'value': value}
                    if formula is not None:
                        entry['formula'] = formula.text
                        entry['formula_attributes'] = dict(formula.attrib)
                        if formula.get('t') == 'shared' and not formula.text:
                            entry['formula_status'] = 'shared_formula_unresolved'
                    cells[column_label(col)] = entry
                if cells:
                    entry = {'row': row_number, 'defined_names_for_value_cell': prompt_names.get(row_number, []), 'cells': cells}
                    if sheet['name'].casefold() == 'prompts':
                        prompts.append(entry)
                    else:
                        library_rows.append({'table': table, 'sheet': sheet['name'], 'row': row_number,
                                             'cells': {column: cell.get('value') for column, cell in cells.items()}})
        shared_masters = {}
        for row in prompts:
            for cell in row['cells'].values():
                attrs = cell.get('formula_attributes', {})
                if attrs.get('t') == 'shared' and cell.get('formula'):
                    key = attrs.get('si')
                    if key in shared_masters:
                        raise ValueError('duplicate shared formula master')
                    shared_masters[key] = cell
        for row in prompts:
            for cell in row['cells'].values():
                if cell.get('formula_status') == 'shared_formula_unresolved':
                    master = shared_masters.get(cell['formula_attributes'].get('si'))
                    if master:
                        cell['shared_formula_master'] = {'address': master['address'],
                                                         'formula': master['formula'],
                                                         'range': master['formula_attributes'].get('ref')}
                        cell['formula_status'] = 'shared_master_identified_translation_required'
        return {'compression': compression, 'blob_sha256': sha(blob), 'workbook_sha256': sha(data),
                'zip_crc': 'ok', 'sheets': sheets, 'defined_names': names,
                'external_relationships': external, 'prompt_rows': prompts,
                'library_rows': library_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('output', type=Path, help='new JSONL file outside tracked source')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    source = args.database.resolve()
    source_hash = hashlib.file_digest(source.open('rb'), 'sha256').hexdigest()
    counts = {'ok': 0, 'error': 0}
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as db, args.output.open('x', encoding='utf-8') as out:
        for table in TABLES:
            sql = f'SELECT ID, LinkID, Name, WorkBook FROM "{table}" WHERE WorkBook IS NOT NULL'
            for record_id, link_id, name, blob in db.execute(sql):
                result = {'source_database_sha256': source_hash, 'table': table, 'record_id': record_id,
                          'link_id': link_id, 'name': name}
                try:
                    result.update(workbook_index(blob))
                    result['status'] = 'ok'
                    counts['ok'] += 1
                except (ValueError, KeyError, IndexError, TypeError, ET.ParseError, zlib.error, zipfile.BadZipFile) as exc:
                    result.update({'status': 'error', 'error': str(exc), 'blob_sha256': sha(blob)})
                    counts['error'] += 1
                out.write(json.dumps(result, ensure_ascii=False) + '\n')
    print(json.dumps({'source_sha256': source_hash, 'output': str(args.output.resolve()), **counts}))
    return 1 if counts['error'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, sqlite3.Error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
