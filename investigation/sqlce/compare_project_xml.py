"""Compare XML-expressible project instance facts with a SQLite SDF analysis copy.

This is a semantic comparison, not an XML importer emulator. It deliberately
flags the XML walls section because this project's canonical products are all
unlinked to walls and the target build's wall behavior is unverified.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import index_workbooks


def workbook_values(blob):
    indexed = index_workbooks.workbook_index(blob)
    values = {}
    for row in indexed['prompt_rows']:
        cell = row.get('cells', {}).get('B', {})
        for name in row.get('defined_names_for_value_cell', []):
            values[name['name']] = {'value': cell.get('value'), 'formula': cell.get('formula'), 'input_type': row.get('cells', {}).get('C', {}).get('value')}
    return values


def indexed_values(payload):
    indexed = json.loads(zlib.decompress(payload))
    values = {}
    for row in indexed.get('prompt_rows', []):
        cell = row.get('cells', {}).get('B', {})
        for name in row.get('defined_names_for_value_cell', []):
            values[name['name']] = {'value': cell.get('value'), 'formula': cell.get('formula')}
    return values


def named_global_values_from_index(indexed):
    cells = {cell.get('address'):cell for row in indexed.get('prompt_rows',[]) for cell in row.get('cells',{}).values()}
    values = {}
    for definition in indexed.get('defined_names',[]):
        match = re.fullmatch(r"(?:'Prompts'|Prompts)!\$?([A-Z]+)\$?(\d+)", definition.get('expression') or '', re.I)
        if match:
            values[definition['name']] = cells.get(match.group(1).upper()+match.group(2), {})
    return values


def global_candidates(source_blob, catalog_payload):
    source = named_global_values_from_index(index_workbooks.workbook_index(source_blob))
    catalog_index = json.loads(zlib.decompress(catalog_payload))
    library = named_global_values_from_index(catalog_index)
    result = {}
    for name, value in source.items():
        if value.get('formula') is not None or value.get('value') is None:
            continue
        default = library.get(name, {})
        if name not in library or value.get('value') != default.get('value') or default.get('formula') is not None:
            result[name] = str(value['value'])
    return result


def prompt_candidates(instance_blob, library_payload):
    instance = workbook_values(instance_blob)
    library = indexed_values(library_payload)
    expected = {}
    for name, value in instance.items():
        if name in ('Width', 'Height', 'Depth'):
            continue
        default = library.get(name, {})
        text = str(value.get('value')) if value.get('value') is not None else ''
        numeric = bool(re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', text))
        literal_candidate = value.get('formula') is None
        cached_candidate = value.get('formula') is not None and value.get('input_type') in (None, '') and numeric and text != str(default.get('value'))
        if not literal_candidate and not cached_candidate:
            continue
        if value.get('value') is None or (text == str(default.get('value')) and default.get('formula') is None):
            continue
        if text.strip() and (numeric or literal_candidate):
            expected[name] = text
    return expected


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sqlite', type=Path, required=True, help='canonical SDF SQLite analysis copy')
    ap.add_argument('--catalog', type=Path, required=True, help='library catalog used to resolve exact templates')
    ap.add_argument('--project', required=True, help='exact project name')
    ap.add_argument('--xml', type=Path, required=True)
    ap.add_argument('--out', type=Path, help='optional JSON report path')
    args = ap.parse_args()
    report = {'status': 'fail', 'errors': [], 'warnings': [], 'checks': {}}
    try:
        data = args.xml.read_bytes()
        if len(data) > 10_000_000 or b'<!' in data:
            raise ValueError('XML declaration/DTD prohibited or file exceeds size limit')
        root = ET.fromstring(data)
        project = root.find('./Project')
        if project is None:
            raise ValueError('missing Project')
        if project.find('Walls') is not None or project.findall('.//LinkIDWall') or project.findall('.//Wall'):
            report['errors'].append('Wall XML or LinkIDWall is present; canonical project has no linked walls and this profile prohibits walls.')
        locations = project.findall('./Locations/Location')
        groups = project.findall('./SpecificationGroups/SpecificationGroup')
        products = project.findall('./Products/Product')
        with sqlite3.connect(args.sqlite.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(args.catalog.resolve().as_uri() + '?mode=ro', uri=True) as cat:
            src.row_factory = cat.row_factory = sqlite3.Row
            expected_project = src.execute('SELECT * FROM Projects WHERE Name=? COLLATE BINARY', (args.project,)).fetchone()
            if expected_project is None:
                raise ValueError('canonical project not found')
            canonical_locations = src.execute('SELECT * FROM Locations WHERE LinkIDProject=?', (expected_project['LinkID'],)).fetchall()
            if len(canonical_locations) != 1:
                raise ValueError(f'expected one canonical location, found {len(canonical_locations)}')
            location = canonical_locations[0]
            expected = src.execute('SELECT * FROM Products WHERE LinkIDLocation=? ORDER BY ItemNumber,ID', (location['LinkID'],)).fetchall()
            report['checks']['project_name'] = {'expected': expected_project['Name'], 'actual': project.attrib.get('Name'), 'match': project.attrib.get('Name') == expected_project['Name']}
            if project.attrib.get('Name') != expected_project['Name']:
                report['errors'].append('Project name mismatch')
            expected_job = expected_project['JobNumber'] or ''
            actual_job = project.findtext('JobNumber') or ''
            report['checks']['job_number'] = {'expected': expected_job, 'actual': actual_job, 'match': actual_job == expected_job}
            if actual_job != expected_job:
                report['errors'].append('Project JobNumber mismatch')
            report['project_number'] = {'canonical_value': expected_project['ProjectNumber'], 'xml_mapping': 'not emitted; no verified XML element mapping'}
            report['checks']['location'] = {'expected': location['Name'], 'actual': [x.attrib.get('Name') for x in locations]}
            if [x.attrib.get('Name') for x in locations] != [location['Name']]:
                report['errors'].append('Location list mismatch')
            expected_group_names = sorted({r['ProductSpecGroupName'] for r in expected if r['ProductSpecGroupName']})
            canonical_groups = src.execute('SELECT * FROM SpecificationGroups WHERE LinkIDProject=?', (expected_project['LinkID'],)).fetchall()
            catalog_group_names = []
            unmapped_groups = []
            for canonical_group in canonical_groups:
                match = cat.execute("SELECT record_id FROM entities WHERE table_name='SpecificationGroups' AND name=? COLLATE BINARY AND library_link_id=?", (canonical_group['Name'], canonical_group['LinkIDLibrary'])).fetchall()
                if len(match) == 1:
                    catalog_group_names.append(canonical_group['Name'])
                else:
                    unmapped_groups.append({'name': canonical_group['Name'], 'library_scope': canonical_group['LinkIDLibrary'], 'catalog_matches': len(match)})
            actual_group_names = sorted(x.attrib.get('Name') for x in groups)
            report['checks']['groups'] = {'expected': expected_group_names, 'actual': actual_group_names}
            report['checks']['catalog_resolvable_project_groups'] = {'expected': sorted(catalog_group_names), 'actual': actual_group_names, 'match': sorted(catalog_group_names) == actual_group_names}
            report['unmapped_project_groups'] = unmapped_groups
            if unmapped_groups:
                report['warnings'].append('One or more canonical project groups have no unique counterpart in the selected catalog and are omitted from XML.')
            if sorted(catalog_group_names) != actual_group_names:
                report['errors'].append('Catalog-resolvable project specification group list mismatch')
            global_checks = []
            xml_groups = {node.attrib.get('Name'):node for node in groups}
            for canonical_group in canonical_groups:
                if canonical_group['Name'] not in xml_groups:
                    continue
                global_row = src.execute('SELECT Name,WorkBook FROM GlobalFiles WHERE LinkID=?', (canonical_group['LinkIDGlobalFileName'],)).fetchone() if canonical_group['LinkIDGlobalFileName'] else None
                if not global_row:
                    global_checks.append({'group':canonical_group['Name'],'status':'no canonical global workbook'})
                    continue
                catalog_global = cat.execute("SELECT record_id FROM entities WHERE table_name='GlobalFiles' AND name=? COLLATE BINARY AND library_link_id=?", (global_row['Name'],canonical_group['LinkIDLibrary'])).fetchall()
                if len(catalog_global) != 1:
                    report['warnings'].append(f"{canonical_group['Name']}: no unique catalog global workbook for comparison")
                    continue
                payload = cat.execute("SELECT payload FROM workbooks WHERE table_name='GlobalFiles' AND record_id=?", (catalog_global[0]['record_id'],)).fetchone()
                expected_global = global_candidates(global_row['WorkBook'], payload['payload']) if payload else {}
                group_node = xml_groups[canonical_group['Name']]
                global_node = group_node.find('Global')
                actual_global = {}
                if global_node is not None:
                    if global_node.attrib.get('Name') != global_row['Name']:
                        report['errors'].append(f"{canonical_group['Name']}: Global name mismatch")
                    for prompt in global_node.findall('./Prompts/Prompt'):
                        name = prompt.attrib.get('Name')
                        if name in actual_global:
                            report['errors'].append(f"{canonical_group['Name']}: duplicate global prompt {name!r}")
                        actual_global[name] = prompt.findtext('Value')
                if actual_global != expected_global:
                    report['errors'].append(f"{canonical_group['Name']}: global prompt candidate mismatch expected={expected_global} actual={actual_global}")
                global_checks.append({'group':canonical_group['Name'],'global_name':global_row['Name'],'expected_candidates':expected_global,'xml_values':actual_global,'match':actual_global==expected_global})
            report['checks']['global_prompts'] = global_checks
            by_item = {}
            for product in products:
                item = product.findtext('ItemNumber')
                if item in by_item:
                    report['errors'].append(f'duplicate XML item number {item!r}')
                by_item[item] = product
            expected_items = {str(r['ItemNumber']): r for r in expected}
            report['checks']['product_count'] = {'expected': len(expected), 'actual': len(products), 'match': len(expected) == len(products)}
            if set(by_item) != set(expected_items):
                report['errors'].append(f'item coverage mismatch; missing={sorted(set(expected_items)-set(by_item))}, extra={sorted(set(by_item)-set(expected_items))}')
            product_checks = []
            for item, row in expected_items.items():
                node = by_item.get(item)
                if node is None:
                    continue
                facts = {'Name': node.attrib.get('Name'), 'Width': node.findtext('Width'), 'Height': node.findtext('Height'), 'Depth': node.findtext('Depth'), 'Quantity': node.findtext('Quantity'), 'XOrigin': node.findtext('XOrigin'), 'YOrigin': node.findtext('YOrigin'), 'ZOrigin': node.findtext('ZOrigin'), 'Angle': node.findtext('Angle'), 'LinkIDLocation': node.findtext('LinkIDLocation'), 'LinkIDSpecificationGroup': node.findtext('LinkIDSpecificationGroup')}
                fields = {'Name': 'Name', 'Width': 'Width', 'Height': 'Height', 'Depth': 'Depth', 'Quantity': 'Quantity', 'XOrigin': 'X', 'YOrigin': 'Y', 'ZOrigin': 'Z', 'Angle': 'Angle'}
                mismatches = []
                for xml_field, sdf_field in fields.items():
                    try:
                        equal = abs(float(facts[xml_field]) - float(row[sdf_field])) <= 1e-7 if xml_field != 'Name' else facts[xml_field] == row[sdf_field]
                    except (TypeError, ValueError):
                        equal = False
                    if not equal:
                        mismatches.append({'field': xml_field, 'expected': row[sdf_field], 'actual': facts[xml_field]})
                if facts['LinkIDLocation'] != location['Name']:
                    mismatches.append({'field': 'LinkIDLocation', 'expected': location['Name'], 'actual': facts['LinkIDLocation']})
                if facts['LinkIDSpecificationGroup'] != row['ProductSpecGroupName']:
                    mismatches.append({'field': 'LinkIDSpecificationGroup', 'expected': row['ProductSpecGroupName'], 'actual': facts['LinkIDSpecificationGroup']})
                expected_comment = '|'.join(str(row[key]) for key in ('Comments','Comments1','Comments2','Comments3') if row[key] is not None)
                actual_comment = node.findtext('Comment') or ''
                if actual_comment != expected_comment:
                    mismatches.append({'field':'Comment','expected':expected_comment,'actual':actual_comment})
                candidates = cat.execute("SELECT record_id FROM entities WHERE table_name='Products' AND name=? COLLATE BINARY AND category_link_id=?", (row['Name'], row['LinkIDCategory'])).fetchall()
                if len(candidates) != 1:
                    mismatches.append({'field': 'library_template_resolution', 'expected': 'unique exact name/category candidate', 'actual': len(candidates)})
                    expected_prompts = {}
                else:
                    template = cat.execute("SELECT payload FROM workbooks WHERE table_name='Products' AND record_id=?", (candidates[0]['record_id'],)).fetchone()
                    try:
                        expected_prompts = prompt_candidates(row['WorkBook'], template['payload']) if template else {}
                    except (ValueError, TypeError, KeyError, AttributeError, zlib.error) as exc:
                        expected_prompts = {}
                        report['warnings'].append(f'{item}: cannot independently resolve prompt overrides: {exc}')
                actual_prompts = {}
                for p in node.findall('./Prompts/Prompt'):
                    name = p.attrib.get('Name')
                    if name in actual_prompts:
                        mismatches.append({'field': 'prompts', 'expected': 'unique names', 'actual': f'duplicate {name}'})
                    actual_prompts[name] = p.findtext('Value')
                if actual_prompts != expected_prompts:
                    mismatches.append({'field': 'prompts', 'expected': expected_prompts, 'actual': actual_prompts})
                if row['LinkIDWall'] is not None:
                    mismatches.append({'field': 'LinkIDWall', 'expected': row['LinkIDWall'], 'actual': node.findtext('LinkIDWall')})
                if mismatches:
                    report['errors'].append(f'item {item}: ' + json.dumps(mismatches, ensure_ascii=False))
                product_checks.append({'item_number': item, 'name': row['Name'], 'matched': not mismatches, 'comment':actual_comment, 'static_prompt_candidates_expected': expected_prompts, 'prompt_values_in_xml': actual_prompts})
            report['checks']['products'] = product_checks
            report['checks']['wall_links'] = {'expected': sum(r['LinkIDWall'] is not None for r in expected), 'xml_wall_elements': len(root.findall('.//Wall')), 'xml_wall_links': len(root.findall('.//LinkIDWall'))}
            report['scope'] = 'pass means all fields enumerated in checks match; it is not full SDF parity.'
            report['prompt_scope'] = 'Values are compared to project workbook cells and candidate differences against the selected research catalog; target library match and importer behavior are unverified.'
            report['comment_scope'] = 'Product Comments/Comments1/Comments2/Comments3 are combined into XML Comment with pipe delimiters based on an example XML file; target importer mapping is unverified.'
            report['not_compared'] = ['unmapped specification groups reported above', 'product GUIDs and row IDs', 'binary workbook payloads and calculated formulas', 'materials and subassemblies', 'product category/library/release/work order/product-group links, anchors and flags', 'ProjectNumber is preserved in the analysis model but no verified XML mapping is known', 'project metadata unrelated to XML fields', 'rendered geometry or Microvellum importer behavior']
            report['canonical_sqlite_sha256'] = __import__('hashlib').file_digest(args.sqlite.open('rb'), 'sha256').hexdigest()
            report['xml_sha256'] = __import__('hashlib').file_digest(args.xml.open('rb'), 'sha256').hexdigest()
        report['status'] = 'pass' if not report['errors'] else 'fail'
    except (OSError, sqlite3.Error, ET.ParseError, ValueError, KeyError, TypeError) as exc:
        report['errors'].append(str(exc))
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    return 0 if report['status'] == 'pass' else 2


if __name__ == '__main__':
    sys.exit(main())
