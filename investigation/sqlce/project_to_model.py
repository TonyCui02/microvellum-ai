"""Create a normalized XML-workflow JSON model from a read-only SQLite analysis snapshot.

This is for canonical comparison only. The SQLite input must be a disposable
analysis copy produced under investigation/sqlce/README.md; it is not an SDF
writer and the generated model does not prove XML importer compatibility.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import re
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import index_workbooks


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def quantity_note(row):
    value = row['Quantity']
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value) or not 1 <= value <= 999999999:
        return {'item_number': str(row['ItemNumber']), 'status': 'invalid_quantity',
                'quantity': row['Quantity']}
    return None


def named_global_values(blob):
    index = index_workbooks.workbook_index(blob)
    cells = {cell.get('address'): cell for row in index['prompt_rows'] for cell in row.get('cells', {}).values()}
    values = {}
    for definition in index['defined_names']:
        match = re.fullmatch(r"(?:'Prompts'|Prompts)!\$?([A-Z]+)\$?(\d+)", definition.get('expression') or '', re.I)
        if not match:
            continue
        cell = cells.get(match.group(1).upper() + match.group(2), {})
        values[definition['name']] = {'value':cell.get('value'), 'formula':cell.get('formula')}
    return values


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-sqlite', type=Path, required=True)
    ap.add_argument('--catalog', type=Path, required=True)
    ap.add_argument('--project', required=True, help='exact project name')
    ap.add_argument('--out', type=Path, required=True, help='new JSON file')
    ap.add_argument('--include-literal-text', action='store_true', help='Include nonempty literal text prompt candidates for experimental calibration; importer behavior is unverified')
    args = ap.parse_args()
    if args.out.exists():
        ap.error('output already exists')
    source = args.project_sqlite.resolve(strict=True)
    catalog = args.catalog.resolve(strict=True)
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    cat = sqlite3.connect(catalog.as_uri() + '?mode=ro', uri=True)
    cat.row_factory = sqlite3.Row
    try:
        project = src.execute('SELECT * FROM Projects WHERE Name=? COLLATE BINARY', (args.project,)).fetchone()
        if project is None:
            raise ValueError('project not found')
        locations = src.execute('SELECT * FROM Locations WHERE LinkIDProject=?', (project['LinkID'],)).fetchall()
        if len(locations) != 1:
            raise ValueError(f'expected exactly one location, found {len(locations)}')
        location = locations[0]
        source_groups = src.execute('SELECT * FROM SpecificationGroups WHERE LinkIDProject=? ORDER BY Name', (project['LinkID'],)).fetchall()
        group_links = {r[0] for r in src.execute('SELECT DISTINCT LinkIDSpecificationGroup FROM Products WHERE LinkIDProject=? AND LinkIDSpecificationGroup IS NOT NULL', (project['LinkID'],))}
        groups = []
        omitted_groups = []
        global_prompt_notes = []
        for source_group in source_groups:
            candidates = cat.execute("SELECT record_id,library_link_id FROM entities WHERE table_name='SpecificationGroups' AND name=? COLLATE BINARY AND library_link_id=?", (source_group['Name'], source_group['LinkIDLibrary'])).fetchall()
            if len(candidates) == 1:
                source_global = src.execute('SELECT Name,WorkBook FROM GlobalFiles WHERE LinkID=?', (source_group['LinkIDGlobalFileName'],)).fetchone() if source_group['LinkIDGlobalFileName'] else None
                global_prompts = []
                if source_global:
                    cat_global = cat.execute("SELECT record_id FROM entities WHERE table_name='GlobalFiles' AND name=? COLLATE BINARY AND library_link_id=?", (source_global['Name'], source_group['LinkIDLibrary'])).fetchall()
                    if len(cat_global) == 1:
                        cat_global_wb = cat.execute("SELECT payload FROM workbooks WHERE table_name='GlobalFiles' AND record_id=?", (cat_global[0]['record_id'],)).fetchone()
                        try:
                            project_values = named_global_values(source_global['WorkBook'])
                            library_index = json.loads(zlib.decompress(cat_global_wb['payload'])) if cat_global_wb else {}
                            # Rebuild a name -> cell-value map for the research catalog's global workbook.
                            library_values = {}
                            cells = {cell.get('address'):cell for row in library_index.get('prompt_rows',[]) for cell in row.get('cells',{}).values()}
                            for definition in library_index.get('defined_names',[]):
                                match = re.fullmatch(r"(?:'Prompts'|Prompts)!\$?([A-Z]+)\$?(\d+)", definition.get('expression') or '', re.I)
                                if match:
                                    cell = cells.get(match.group(1).upper()+match.group(2), {})
                                    library_values[definition['name']] = {'value':cell.get('value'),'formula':cell.get('formula')}
                            for name, value in project_values.items():
                                if value.get('formula') is None and value.get('value') is not None and value != library_values.get(name, {}):
                                    global_prompts.append({'name':name,'value':str(value['value'])})
                                    if name not in library_values:
                                        global_prompt_notes.append({'group':source_group['Name'],'name':name,'status':'no_matching_catalog_global_prompt'})
                                elif value.get('formula') is not None and value != library_values.get(name, {}):
                                    global_prompt_notes.append({'group':source_group['Name'],'name':name,'status':'formula_difference_not_serialized','formula':value.get('formula')})
                        except (ValueError, TypeError, KeyError, AttributeError, zlib.error) as exc:
                            global_prompt_notes.append({'group':source_group['Name'],'status':'global_workbook_unreadable','detail':str(exc)})
                    else:
                        global_prompt_notes.append({'group':source_group['Name'],'status':'catalog_global_workbook_missing_or_ambiguous'})
                    groups.append({'name': source_group['Name'], 'record_id': candidates[0]['record_id'], 'global_name': source_global['Name'], 'global_prompts': global_prompts})
                else:
                    groups.append({'name': source_group['Name'], 'record_id': candidates[0]['record_id'], 'global_name': None, 'global_prompts': []})
            else:
                omitted_groups.append({'name': source_group['Name'], 'link_id': source_group['LinkID'], 'library_scope': source_group['LinkIDLibrary'], 'referenced_by_product': source_group['LinkID'] in group_links, 'reason': 'No unique matching specification group in the selected research catalog.'})
        used_group = next((g for g in source_groups if g['LinkID'] in group_links), None)
        if used_group is None:
            raise ValueError('products do not reference a specification group')
        rows = src.execute('SELECT * FROM Products WHERE LinkIDLocation=? ORDER BY ItemNumber, ID', (location['LinkID'],)).fetchall()
        products = []
        prompt_notes = []
        prompt_provenance = []
        for row in rows:
            unsupported_quantity = quantity_note(row)
            if unsupported_quantity:
                prompt_notes.append(unsupported_quantity)
            candidates = cat.execute("SELECT record_id FROM entities WHERE table_name='Products' AND name=? COLLATE BINARY AND category_link_id=?", (row['Name'], row['LinkIDCategory'])).fetchall()
            if len(candidates) != 1:
                raise ValueError(f"{row['ItemNumber']}: catalog identity ambiguous for {row['Name']!r} and category")
            catalog_id = candidates[0]['record_id']
            catalog_wb = cat.execute("SELECT compression,payload FROM workbooks WHERE table_name='Products' AND record_id=?", (catalog_id,)).fetchone()
            project_values = {}
            try:
                project_index = index_workbooks.workbook_index(row['WorkBook'])
                for prompt_row in project_index['prompt_rows']:
                    names = prompt_row['defined_names_for_value_cell']
                    cell = prompt_row['cells'].get('B', {})
                    for defined in names:
                        project_values[defined['name']] = {'value': cell.get('value'), 'formula': cell.get('formula'), 'input_type': prompt_row.get('cells', {}).get('C', {}).get('value')}
            except (ValueError, TypeError, KeyError, AttributeError, zlib.error) as exc:
                prompt_notes.append({'item_number': str(row['ItemNumber']), 'status': 'instance_workbook_unreadable', 'detail': str(exc)})
            library_values = {}
            if catalog_wb:
                try:
                    catalog_index = json.loads(zlib.decompress(catalog_wb['payload']))
                    for prompt_row in catalog_index.get('prompt_rows', []):
                        cell = prompt_row.get('cells', {}).get('B', {})
                        for defined in prompt_row.get('defined_names_for_value_cell', []):
                            library_values[defined['name']] = {'value': cell.get('value'), 'formula': cell.get('formula')}
                except (ValueError, TypeError, KeyError, zlib.error):
                    prompt_notes.append({'item_number': str(row['ItemNumber']), 'status': 'library_workbook_unreadable'})
            overrides = []
            for prompt_name, observed in project_values.items():
                if prompt_name in ('Width', 'Height', 'Depth'):
                    continue
                default = library_values.get(prompt_name, {})
                value = str(observed['value']) if observed.get('value') is not None else ''
                numeric = bool(re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value))
                literal_candidate = observed.get('formula') is None
                cached_prompt_candidate = observed.get('formula') is not None and observed.get('input_type') in (None, '') and numeric and value != str(default.get('value'))
                if not literal_candidate and not cached_prompt_candidate:
                    continue
                if observed.get('value') is None or (value == str(default.get('value')) and default.get('formula') is None):
                    continue
                if not numeric:
                    if not args.include_literal_text or not value.strip() or not literal_candidate:
                        prompt_notes.append({'item_number': str(row['ItemNumber']), 'prompt': prompt_name, 'status': 'non_numeric_override_not_exported'})
                        continue
                    prompt_provenance.append({'item_number': str(row['ItemNumber']), 'prompt': prompt_name, 'formula': None, 'serialized': 'literal_text_candidate_unverified'})
                overrides.append({'name': prompt_name, 'value': value})
                if cached_prompt_candidate:
                    prompt_provenance.append({'item_number': str(row['ItemNumber']), 'prompt': prompt_name, 'formula': observed['formula'], 'serialized': 'cached_numeric_value_candidate'})
            products.append({
                'id': str(row['ItemNumber']), 'record_id': catalog_id,
                'quantity': str(int(row['Quantity'])) if not unsupported_quantity else '1',
                'name': row['Name'], 'location': location['Name'], 'group': row['ProductSpecGroupName'],
                'item_number': str(row['ItemNumber']),
                'comment': '|'.join(str(row[key]) for key in ('Comments','Comments1','Comments2','Comments3') if row[key] is not None),
                'width': str(row['Width']), 'height': str(row['Height']), 'depth': str(row['Depth']),
                'x': str(row['X']), 'y': str(row['Y']), 'z': str(row['Z']), 'angle': str(row['Angle']),
                'prompts': overrides,
            })
        model = {
            'schema_version': '1',
            'target': {'build': '15.6.1308.640', 'configuration': 'research snapshot; active match unverified', 'output_units': 'mm', 'coordinates': 'native_unverified'},
            'catalog_sha256': sha(catalog),
            'review': {'status': 'reviewed', 'reviewer': 'canonical snapshot extraction', 'note': 'Reference reconstruction from project SDF analysis copy; not a drawing-only interpretation.'},
            # Projects.ProjectNumber and Projects.JobNumber are distinct fields.
            # Preserve the actual job number for XML; ProjectNumber has no
            # verified XML mapping and remains analysis metadata.
            'project': {'name': project['Name'], 'job_number': project['JobNumber'] or '', 'project_number': project['ProjectNumber'], 'properties': {}},
            'locations': [location['Name']],
            'groups': groups,
            'products': products,
            'source': {'kind': 'canonical_project_analysis_copy', 'sqlite_sha256': sha(source), 'project_id': project['LinkID'], 'prompt_notes': prompt_notes, 'prompt_provenance': prompt_provenance, 'unmapped_project_groups': omitted_groups, 'prompt_basis': 'static numeric inputs and numeric formula caches' + (' and nonempty literal text' if args.include_literal_text else '') + ' differing from the selected research-catalog workbook; override necessity and text import behavior are unverified', 'library_match_verified': False, 'global_prompt_notes': global_prompt_notes},
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(model, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps({'model': str(args.out.resolve()), 'source_sqlite_sha256': sha(source), 'catalog_sha256': sha(catalog), 'project': project['Name'], 'location': location['Name'], 'products': len(products), 'wall_linked_products': sum(row['LinkIDWall'] is not None for row in rows), 'static_prompt_candidate_count': sum(len(p['prompts']) for p in products), 'prompt_notes': prompt_notes}, indent=2))
    finally:
        src.close()
        cat.close()


if __name__ == '__main__':
    main()
