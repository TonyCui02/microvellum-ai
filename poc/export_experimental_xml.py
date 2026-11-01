"""Convert an active POC review JSON to an explicitly experimental XML draft."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
import zlib

from model_validation import validate_model
from material_catalog import material_scope


def sha256(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def fail(message):
    raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='new output directory; must not exist')
    args = parser.parse_args()
    model = json.loads(args.model.read_text(encoding='utf-8-sig'))
    if args.out.exists():
        fail('output directory already exists')
    project = model['project']
    room = model['room']
    job_number = project.get('job_number')
    if not isinstance(job_number, str):
        fail('project.job_number must be provided as text (use an empty string only when intentionally blank)')
    location_name = room.get('name')
    if not isinstance(location_name, str) or not location_name.strip():
        fail('room.name must be provided as the intended location name')
    source = model.get('source', {})
    source_path = source.get('path') if isinstance(source, dict) else None
    if not isinstance(source_path, str) or not source_path.strip():
        fail('source.path must identify the reviewed drawing')
    drawing = Path(source_path)
    if not drawing.is_absolute():
        drawing = args.model.resolve().parent / drawing
    if not drawing.is_file() or drawing.name != source.get('file'):
        fail('source.path and source.file must identify the reviewed drawing')
    visual_review = source.get('visual_review')
    if not isinstance(visual_review, dict):
        fail('source.visual_review must record the drawing hash and inspected pages')
    expected_source_hash = visual_review.get('source_sha256')
    if not isinstance(expected_source_hash, str) or sha256(drawing) != expected_source_hash:
        fail('reviewed drawing hash is missing or does not match the current source')
    if drawing.suffix.lower() == '.png':
        page_count = 1
    elif drawing.suffix.lower() == '.pdf':
        try:
            info = subprocess.run(['pdfinfo', str(drawing)], check=True, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace')
        except (OSError, subprocess.CalledProcessError) as exc:
            fail(f'cannot inspect PDF page count: {exc}')
        match = re.search(r'^Pages:\s*(\d+)\s*$', info.stdout, re.M)
        if not match or int(match.group(1)) < 1:
            fail('cannot determine PDF page count')
        page_count = int(match.group(1))
    else:
        fail('reviewed drawing must be a PDF or PNG')
    inspected_pages = visual_review.get('inspected_pages')
    if (not isinstance(inspected_pages, list) or
            not all(isinstance(page, int) and not isinstance(page, bool) for page in inspected_pages) or
            sorted(inspected_pages) != list(range(1, page_count + 1))):
        fail('visual review must cover every source page')
    products = room['products']
    walls = room['walls']
    if model.get('review', {}).get('approved_by'):
        review_state = 'reviewed'
    else:
        review_state = project.get('review_status', 'draft')
    if room.get('features'):
        fail('unsupported room features remain; resolve or remove them before export')
    review_errors = validate_model(model)
    if review_errors:
        fail('drawing model failed completeness/evidence checks: ' + '; '.join(review_errors))
    review = model.get('review', {})
    if (not review.get('approved_by') or not review.get('approved_at') or
            review.get('approved_revision') != project.get('revision') or review.get('issues')):
        fail('drawing coverage or review issues remain; complete review before XML generation')
    if not products:
        fail('no cabinet products to export')

    catalog_hash = sha256(args.catalog)
    if project.get('catalog_sha256') != catalog_hash:
        fail('project.catalog_sha256 must match the reviewed catalog file')
    uri = args.catalog.resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        fail('catalog integrity check failed')
    source = db.execute("SELECT value FROM metadata WHERE key='source_sqlite_sha256'").fetchone()
    if not source:
        fail('catalog source fingerprint missing')

    group_specs = project.get('specification_groups')
    if not isinstance(group_specs, list) or not group_specs:
        fail('project.specification_groups must explicitly identify the selected catalog groups')
    group_by_name = {}
    library_scope = None
    for spec in group_specs:
        if not isinstance(spec, dict) or not isinstance(spec.get('name'), str) or not spec['name']:
            fail('each specification group needs an exact name')
        if spec['name'] in group_by_name:
            fail(f"duplicate specification group {spec['name']!r}")
        group_rows = db.execute("SELECT record_id,link_id,library_link_id,fields_json FROM entities WHERE table_name='SpecificationGroups' AND name=? COLLATE BINARY", (spec['name'],)).fetchall()
        if len(group_rows) != 1:
            fail(f"specification group {spec['name']!r} is missing or ambiguous in catalog")
        group = group_rows[0]
        if library_scope is None:
            library_scope = group['library_link_id']
        if not group['library_link_id'] or group['library_link_id'] != library_scope:
            fail('all selected specification groups must share one library scope')
        group_fields = json.loads(group['fields_json'])
        global_rows = db.execute("SELECT record_id,name FROM entities WHERE table_name='GlobalFiles' AND link_id=?", (group_fields.get('LinkIDGlobalFileName'),)).fetchall() if group_fields.get('LinkIDGlobalFileName') else []
        if len(global_rows) > 1:
            fail(f"group {spec['name']!r}: global workbook is ambiguous")
        global_name = global_rows[0]['name'] if global_rows else None
        global_names = set()
        if global_rows:
            global_wb = db.execute("SELECT compression,payload FROM workbooks WHERE table_name='GlobalFiles' AND record_id=?", (global_rows[0]['record_id'],)).fetchall()
            if len(global_wb) == 1:
                indexed_global = json.loads(zlib.decompress(global_wb[0]['payload']))
                global_names = {name['name'] for name in indexed_global.get('defined_names', [])}
        emitted_global = []
        proposed_global = []
        for prompt in spec.get('global_prompt_overrides', []):
            if not isinstance(prompt, dict) or not isinstance(prompt.get('name'), str):
                fail(f"group {spec['name']!r}: malformed global prompt override")
            if prompt.get('status') != 'confirmed':
                proposed_global.append(prompt['name']); continue
            if prompt['name'] not in global_names:
                fail(f"group {spec['name']!r}: global prompt {prompt['name']!r} is not defined in the catalog workbook")
            value = prompt.get('value')
            if isinstance(value, bool) or not isinstance(value, (int, float, str)) or (isinstance(value, float) and not math.isfinite(value)):
                fail(f"group {spec['name']!r}: invalid global prompt value")
            emitted_global.append({'name':prompt['name'],'value':str(value)})
        group_by_name[spec['name']] = {'row':group, 'global_name':global_name,
                                       'global_prompts':emitted_global,'proposed_global_prompts':proposed_global}
    if not group_by_name:
        fail('at least one specification group is required')
    default_group = next(iter(group_by_name)) if len(group_by_name) == 1 else None

    material_report = {'xml_supported': False, 'product_annotations': []}

    root = ET.Element('Root', {'Application': 'Microvellum', 'ApplicationVersion': '7.0'})
    proj = ET.SubElement(root, 'Project', {'Name': project['name']})
    ET.SubElement(proj, 'JobNumber').text = job_number
    locations = ET.SubElement(proj, 'Locations')
    ET.SubElement(locations, 'Location', {'Name': location_name})
    groups = ET.SubElement(proj, 'SpecificationGroups')
    for group_name, spec in group_by_name.items():
        group_node = ET.SubElement(groups, 'SpecificationGroup', {'Name':group_name})
        if spec['global_prompts'] and spec['global_name']:
            global_node = ET.SubElement(group_node, 'Global', {'Name':spec['global_name']})
            prompt_container = ET.SubElement(global_node, 'Prompts')
            for override in spec['global_prompts']:
                prompt_node = ET.SubElement(prompt_container, 'Prompt', {'Name':override['name']})
                ET.SubElement(prompt_node, 'Value').text = override['value']
    # Wall XML and LinkIDWall are intentionally disabled. The source project's
    # canonical records have null wall links and XML wall behavior is unverified
    # in the target build. Internal wall axes may still provide drawing-relative
    # coordinates, but are never serialized as Microvellum walls.
    for wall in walls:
        start, end = wall['start'], wall['end']
        dx, dy = end[0] - start[0], end[1] - start[1]
        width = math.hypot(dx, dy)
        if not math.isfinite(width) or width <= 0:
            fail(f"wall {wall['id']}: invalid length")

    xml_products = ET.SubElement(proj, 'Products')
    product_manifest = []
    seen_items = set()
    for p in products:
        lib = p.get('library_product') or {}
        record_id = lib.get('record_id')
        if not record_id:
            fail(f"product {p.get('id')}: selected catalog record ID is required")
        exact_name = lib.get('name')
        if isinstance(exact_name, str) and exact_name.strip().casefold() == 'wall':
            fail(f"product {p['id']}: Wall products are prohibited by this profile")
        rows = db.execute("SELECT record_id,library_link_id,category_link_id,fields_json FROM entities WHERE table_name='Products' AND record_id=?", (record_id,)).fetchall()
        if len(rows) != 1:
            fail(f"product {p['id']}: selected record not found in catalog")
        row = rows[0]
        selected_group = p.get('specification_group')
        product_group = selected_group.get('name') if isinstance(selected_group, dict) else selected_group
        product_group = product_group or default_group
        if product_group not in group_by_name:
            fail(f"product {p['id']}: specification group {product_group!r} is not declared")
        if isinstance(selected_group, dict) and selected_group.get('link_id') != group_by_name[product_group]['row']['link_id']:
            fail(f"product {p['id']}: specification group identity does not match the catalog")
        if row['library_link_id'] != group_by_name[product_group]['row']['library_link_id']:
            fail(f"product {p['id']}: product and specification group library scopes differ")
        raw = json.loads(row['fields_json'])
        if raw.get('Name') != exact_name or raw.get('Type') != 1:
            fail(f"product {p['id']}: exact name or library product type mismatch")
        wb = db.execute("SELECT compression,payload FROM workbooks WHERE table_name='Products' AND record_id=?", (record_id,)).fetchall()
        if len(wb) != 1 or wb[0]['compression'] not in ('complete', 'direct_zip', 'incomplete_stream'):
            fail(f"product {p['id']}: product workbook missing")
        if wb[0]['compression'] == 'incomplete_stream' and p.get('prompt_overrides'):
            fail(f"product {p['id']}: prompt overrides are blocked for an incomplete workbook stream")
        workbook = json.loads(zlib.decompress(wb[0]['payload']))
        if workbook.get('status') != 'ok':
            fail(f"product {p['id']}: product workbook indexing failed")
        prompt_names = {}
        for prompt_row in workbook.get('prompt_rows', []):
            for defined in prompt_row.get('defined_names_for_value_cell', []):
                prompt_names.setdefault(defined['name'], 0)
                prompt_names[defined['name']] += 1

        dims = p['dimensions']
        for key in ('width', 'height', 'depth'):
            val = dims.get(key)
            if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val) or val <= 0:
                fail(f"product {p['id']}: {key} must be a positive finite dimension")
        item_number = str(p.get('id'))
        if item_number in seen_items:
            fail('duplicate item number')
        seen_items.add(item_number)
        comment = p.get('comment', '')
        if not isinstance(comment, str):
            fail(f"product {p['id']}: comment must be text")
        wall = next((w for w in walls if w['id'] == p.get('wall_id')), None)
        if wall is None:
            fail(f"product {p['id']}: wall reference is unresolved")
        dx, dy = wall['end'][0] - wall['start'][0], wall['end'][1] - wall['start'][1]
        length = math.hypot(dx, dy)
        x = wall['start'][0] + dx / length * p['offset']
        y = wall['start'][1] + dy / length * p['offset']
        node = ET.SubElement(xml_products, 'Product', {'Name': exact_name})
        product_vals = [('Quantity', p.get('quantity', 1)), ('Width', dims['width']), ('Height', dims['height']),
                        ('Depth', dims['depth']), ('ItemNumber', item_number), ('Comment', comment),
                        ('XOrigin', x), ('YOrigin', y), ('ZOrigin', p.get('elevation', 0)), ('Angle', p.get('rotation', 0)),
                        ('LinkIDLocation', location_name), ('LinkIDSpecificationGroup', product_group)]
        for tag, value in product_vals:
            ET.SubElement(node, tag).text = str(value)
        prompts = ET.SubElement(node, 'Prompts')
        emitted = []
        for prompt in p.get('prompt_overrides', []):
            if prompt.get('status') != 'confirmed':
                continue
            name = prompt['name']
            if prompt_names.get(name) != 1:
                fail(f"product {p['id']}: prompt {name!r} is absent or ambiguous in indexed workbook")
            value = prompt.get('value')
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                fail(f"product {p['id']}: only finite numeric confirmed prompt values are supported")
            pr = ET.SubElement(prompts, 'Prompt', {'Name': name})
            ET.SubElement(pr, 'Value').text = str(value)
            emitted.append({'name': name, 'value': str(value)})
        proposed = [x['name'] for x in p.get('prompt_overrides', []) if x.get('status') != 'confirmed']
        product_manifest.append({'id': p['id'], 'name': exact_name, 'record_id': record_id,
                                 'quantity': p.get('quantity', 1),
                                 'specification_group':product_group,
                                 'category_link_id': row['category_link_id'], 'item_number': item_number,
                                 'comment':comment,
                                 'dimensions': {key:str(dims[key]) for key in ('width','height','depth')},
                                 'pose': {'x':str(x),'y':str(y),'z':str(p.get('elevation',0)),'angle':str(p.get('rotation',0))},
                                 'confirmed_prompts_exported': emitted, 'proposed_prompts_omitted': proposed})
        legacy_material = p.get('material')
        if legacy_material:
            candidate_checks = []
            for role, candidate in (p.get('material_catalog') or {}).items():
                if not isinstance(candidate, dict) or not candidate.get('record_id'):
                    continue
                if candidate.get('catalog_sha256') and candidate['catalog_sha256'] != catalog_hash:
                    fail(f"product {p['id']} material {role}: candidate is from a different catalog fingerprint")
                rows = db.execute("SELECT record_id,link_id,name FROM entities WHERE table_name='Materials' AND record_id=?", (candidate['record_id'],)).fetchall()
                if len(rows) != 1 or rows[0]['link_id'] != candidate.get('link_id') or rows[0]['name'] != candidate.get('name'):
                    fail(f"product {p['id']} material {role}: selected catalog identity no longer matches")
                if candidate.get('group') and candidate['group'] != product_group:
                    fail(f"product {p['id']} material {role}: candidate group does not match product specification group")
                if candidate.get('role') and candidate['role'] != role:
                    fail(f"product {p['id']} material {role}: candidate role does not match")
                try:
                    members, assigned = material_scope(db, product_group, role)
                except ValueError as exc:
                    fail(f"product {p['id']} material {role}: cannot verify group scope: {exc}")
                if candidate['link_id'] not in members:
                    fail(f"product {p['id']} material {role}: record is not in specification group {product_group!r}")
                candidate_checks.append({'role':role,'record_id':candidate['record_id'],'link_id':candidate['link_id'],
                                         'name':rows[0]['name'],'group_assigned':candidate['link_id'] in assigned})
            material_report['product_annotations'].append({'product_id': p['id'], 'requirement': legacy_material,
                'catalog_candidates': candidate_checks, 'xml_action': 'omitted; no material assignment field is supported by the current XML profile'})

    ET.indent(root, space='  ')
    data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    # Independent XML parse and check key counts/references before writing.
    parsed = ET.fromstring(data)
    if parsed.tag != 'Root' or len(parsed.findall('./Project/Products/Product')) != len(products):
        fail('XML read-back product count mismatch')
    if parsed.find('./Project/Walls') is not None or parsed.findall('.//LinkIDWall') or any((n.get('Name') or '').strip().casefold()=='wall' for n in parsed.findall('./Project/Products/Product')):
        fail('wall XML is prohibited by this experimental profile')
    readback_groups = parsed.findall('./Project/SpecificationGroups/SpecificationGroup')
    if [node.get('Name') for node in readback_groups] != list(group_by_name):
        fail('XML read-back specification group coverage mismatch')
    for group_node in readback_groups:
        group_name = group_node.get('Name')
        expected_global = {x['name']:x['value'] for x in group_by_name[group_name]['global_prompts']}
        global_node = group_node.find('Global')
        actual_global = {x.get('Name'):x.findtext('Value') for x in (global_node.findall('./Prompts/Prompt') if global_node is not None else [])}
        if actual_global != expected_global:
            fail(f"XML read-back global prompt mismatch in group {group_name!r}")
    readback_by_item = {node.findtext('ItemNumber'):node for node in parsed.findall('./Project/Products/Product')}
    for expected in product_manifest:
        node = readback_by_item.get(expected['item_number'])
        if node is None or node.get('Name') != expected['name']:
            fail(f"XML read-back identity mismatch for item {expected['item_number']!r}")
        for tag, value in (('Quantity',str(expected['quantity'])),('Width',expected['dimensions']['width']),('Height',expected['dimensions']['height']),('Depth',expected['dimensions']['depth']),('XOrigin',expected['pose']['x']),('YOrigin',expected['pose']['y']),('ZOrigin',expected['pose']['z']),('Angle',expected['pose']['angle']),('LinkIDLocation',location_name),('LinkIDSpecificationGroup',expected['specification_group'])):
            if node.findtext(tag) != value:
                fail(f"XML read-back {tag} mismatch for item {expected['item_number']!r}")
        if (node.findtext('Comment') or '') != expected['comment']:
            fail(f"XML read-back Comment mismatch for item {expected['item_number']!r}")
        actual_prompts = {x.get('Name'):x.findtext('Value') for x in node.findall('./Prompts/Prompt')}
        expected_prompts = {x['name']:x['value'] for x in expected['confirmed_prompts_exported']}
        if actual_prompts != expected_prompts:
            fail(f"XML read-back product prompt mismatch for item {expected['item_number']!r}")

    omitted = sorted({name for p in product_manifest for name in p['proposed_prompts_omitted']})
    warnings = [
        'Experimental draft only; active-library provenance and target import behavior have not been verified.',
        'Units, coordinate axes, insertion anchor and pose interpretation remain uncalibrated.',
        'Each product entry has one position even when its quantity is greater than one; importer behavior is unverified.',
        'XML was synthesized from the POC review model; no POC-to-Microvellum XML contract is vendor-verified.',
    ]
    if omitted:
        warnings.append('Proposed but unconfirmed prompts were omitted: ' + ', '.join(omitted) + '.')
    if material_report['product_annotations']:
        warnings.append('Item material requirements and selected catalog records were checked and recorded in validation.json, but are omitted from project.xml because this XML profile has no verified material assignment field.')
    report = {
        'status': 'insufficient_evidence', 'review_status': review_state,
        'static_checks': 'passed', 'target_import_tested': False,
        'experimental_only': True, 'product_count': len(products), 'wall_count': 0,
        'coordinate_reference_wall_count': len(walls),
        'specification_groups': [{'name':name,'global_prompts_exported':spec['global_prompts'],
                                  'proposed_global_prompts_omitted':spec['proposed_global_prompts']}
                                 for name,spec in group_by_name.items()],
        'drawing_inventory': {'entity_count': len(room['drawing_inventory']),
                              'mapped_count': sum(x['resolution'] in ('model_product','model_feature') for x in room['drawing_inventory']),
                              'explicitly_excluded_count': sum(x['resolution'] == 'explicit_exclusion' for x in room['drawing_inventory'])},
        'model_sha256': sha256(args.model), 'catalog_sha256': catalog_hash,
        'source_sqlite_sha256': source[0], 'xml_sha256': hashlib.sha256(data).hexdigest(),
        'target': {'build': '15.6.1308.640', 'library_label': 'modified Kitchen Library v7.1; snapshot match unverified', 'units': project['units']},
        'products': product_manifest, 'item_materials': material_report, 'warnings': warnings,
        'limitations': ['This draft does not prove XML import, drawing, workbook calculation or manufacturing behavior.',
                        'The POC depicts schematic boxes; this XML uses their dimensions and placements as supplied.']
    }
    args.out.mkdir(parents=False, exist_ok=False)
    (args.out / 'project.xml').write_bytes(data)
    (args.out / 'validation.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (args.out / 'README.txt').write_text('EXPERIMENTAL XML DRAFT. Insufficient evidence for production use. Import only into a disposable calibration project after reviewing validation.json. Do not append to production or generate CNC output.\n', encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    db.close()


if __name__ == '__main__':
    main()
