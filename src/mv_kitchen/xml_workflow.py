"""Experimental Microvellum XML preparation; not an importer emulator."""
import argparse
from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zlib


class Invalid(ValueError):
    pass


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def obj(value, keys, where):
    if not isinstance(value, dict):
        raise Invalid(f'{where}: expected object')
    extra = set(value) - set(keys)
    missing = set(keys) - set(value)
    if extra or missing:
        raise Invalid(f'{where}: unknown fields {sorted(extra)}; missing fields {sorted(missing)}')
    return value


def string(value, where):
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f'{where}: expected nonempty string')
    if any(not (c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
        raise Invalid(f'{where}: invalid XML character')
    return value


def number(value, where, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value):
        raise Invalid(f'{where}: use a decimal string without exponent or locale separator')
    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise Invalid(f'{where}: invalid number') from exc
    if not d.is_finite() or (positive and d <= 0):
        raise Invalid(f'{where}: expected finite' + (' positive' if positive else '') + ' number')
    if len(value) > 64:
        raise Invalid(f'{where}: number too long')
    return format(d, 'f')


def quantity(value, where):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,8}', value):
        raise Invalid(f'{where}: expected a positive integer string')
    return value


def array(value, where):
    if not isinstance(value, list) or not value:
        raise Invalid(f'{where}: expected nonempty array')
    return value


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Invalid(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    with path.open(encoding='utf-8-sig') as f:
        return json.load(f, object_pairs_hook=unique, parse_constant=lambda x: (_ for _ in ()).throw(Invalid('invalid JSON constant: ' + x)))


def validate(model, db):
    allowed = ['schema_version', 'target', 'catalog_sha256', 'review', 'project', 'locations', 'groups', 'products']
    if 'source' in model:
        allowed.append('source')
        source_meta = obj(model['source'], ['kind', 'sqlite_sha256', 'project_id', 'prompt_notes', 'prompt_provenance', 'unmapped_project_groups', 'prompt_basis', 'library_match_verified', 'global_prompt_notes'], 'source')
        if source_meta['kind'] != 'canonical_project_analysis_copy':
            raise Invalid('source.kind is unsupported')
        string(source_meta['sqlite_sha256'], 'source.sqlite_sha256')
        string(source_meta['project_id'], 'source.project_id')
        string(source_meta['prompt_basis'], 'source.prompt_basis')
        if source_meta['library_match_verified'] is not False:
            raise Invalid('source.library_match_verified must remain false for this unproven catalog match')
        if source_meta['prompt_notes']:
            raise Invalid('source project has unresolved data or unsupported quantities; export is blocked')
    obj(model, allowed, 'model')
    if model['schema_version'] != '1':
        raise Invalid('unsupported schema_version')
    target = obj(model['target'], ['build', 'configuration', 'output_units', 'coordinates'], 'target')
    for k in target:
        string(target[k], 'target.' + k)
    if target['output_units'] not in ('mm', 'in'):
        raise Invalid('target.output_units: expected mm or in')
    if target['coordinates'] != 'native_unverified':
        raise Invalid('target.coordinates: only native_unverified is supported')
    review = obj(model['review'], ['status', 'reviewer', 'note'], 'review')
    if review['status'] != 'reviewed':
        raise Invalid('review.status must be reviewed')
    string(review['reviewer'], 'review.reviewer'); string(review['note'], 'review.note')
    project = model['project']
    if not isinstance(project, dict) or set(project) - {'name','job_number','project_number','properties'} or not {'name','job_number'} <= set(project):
        raise Invalid('project: expected name and job_number with supported optional metadata')
    string(project['name'], 'project.name')
    if not isinstance(project['job_number'], str): raise Invalid('project.job_number: expected text')
    if project['job_number']:
        string(project['job_number'], 'project.job_number')
    elif 'source' not in model:
        raise Invalid('project.job_number is required for a drawing model')
    project_number = project.get('project_number')
    if project_number is not None and not isinstance(project_number, (str,int)):
        raise Invalid('project.project_number: expected text or integer')
    project['properties'] = project.get('properties', {})
    allowed_properties = {'JobAddress','JobPhone','JobFax','JobEmail','Architect','Contractor','Draftsman','Estimator','GeneralContact','ProjectManager'}
    if not isinstance(project['properties'],dict) or set(project['properties'])-allowed_properties:
        raise Invalid('project.properties: unsupported property name')
    for key,value in project['properties'].items(): string(value, 'project.properties.'+key)
    locations = [string(x, 'location') for x in array(model['locations'], 'locations')]
    if len(set(locations)) != len(locations): raise Invalid('duplicate location name')
    groups = []
    for g in array(model['groups'], 'groups'):
        g.setdefault('global_name', None)
        g.setdefault('global_prompts', [])
        obj(g, ['name', 'record_id', 'global_name', 'global_prompts'], 'group')
        name = string(g['name'], 'group.name')
        rid = string(g['record_id'], 'group.record_id')
        global_name = string(g['global_name'], 'group.global_name') if g['global_name'] is not None else None
        if not isinstance(g['global_prompts'], list): raise Invalid(f'group {name}: global_prompts must be a list')
        groups.append({'name':name, 'record_id':rid, 'global_name':global_name, 'global_prompts':g['global_prompts']})
    if len({x['name'] for x in groups}) != len(groups): raise Invalid('duplicate group name')
    group_names = set()
    group_libraries = {}
    unmatched_global_prompts = []
    normalized_groups = []
    for group in groups:
        name, rid = group['name'], group['record_id']
        rows = db.execute("SELECT record_id, library_link_id FROM entities WHERE table_name='SpecificationGroups' AND name=? COLLATE BINARY", (name,)).fetchall()
        if len(rows) != 1 or rows[0][0] != rid: raise Invalid(f'group {name!r}: missing, ambiguous or record ID mismatch')
        group_names.add(name)
        group_libraries[name] = rows[0][1]
        group_data = db.execute("SELECT fields_json FROM entities WHERE table_name='SpecificationGroups' AND record_id=?", (rid,)).fetchone()
        fields = json.loads(group_data[0]) if group_data else {}
        global_link = fields.get('LinkIDGlobalFileName')
        global_rows = db.execute("SELECT record_id,name FROM entities WHERE table_name='GlobalFiles' AND link_id=?", (global_link,)).fetchall() if global_link else []
        allowed_global_prompts = set()
        if len(global_rows) == 1:
            global_workbooks = db.execute("SELECT compression,payload FROM workbooks WHERE table_name='GlobalFiles' AND record_id=?", (global_rows[0][0],)).fetchall()
            if len(global_workbooks) == 1:
                try:
                    global_index = json.loads(zlib.decompress(global_workbooks[0][1]))
                    cells = {cell.get('address'):cell for row in global_index.get('prompt_rows',[]) for cell in row.get('cells',{}).values()}
                    for definition in global_index.get('defined_names',[]):
                        match = re.fullmatch(r"(?:'Prompts'|Prompts)!\$?M\$?(\d+)", definition.get('expression') or '', re.I)
                        if match and match.group(1): allowed_global_prompts.add(definition.get('name'))
                except (ValueError, TypeError, KeyError, zlib.error):
                    raise Invalid(f'group {name}: global workbook index is unreadable')
            if group['global_name'] and group['global_name'] != global_rows[0][1]:
                raise Invalid(f'group {name}: global workbook name mismatch')
        elif group['global_prompts']:
            raise Invalid(f'group {name}: cannot resolve the catalog global workbook')
        seen_global = set()
        norm_global = []
        for prompt in group['global_prompts']:
            obj(prompt, ['name','value'], f'group {name} global prompt')
            prompt_name = string(prompt['name'], 'global prompt name')
            prompt_value = string(prompt['value'], 'global prompt value')
            if prompt_name in seen_global: raise Invalid(f'group {name}: duplicate global prompt {prompt_name!r}')
            seen_global.add(prompt_name)
            if prompt_name not in allowed_global_prompts:
                unmatched_global_prompts.append({'group':name,'global':group['global_name'],'prompt':prompt_name})
            norm_global.append({'name':prompt_name,'value':prompt_value})
        normalized_groups.append({'name':name,'global_name':group['global_name'],'global_prompts':norm_global})
    products = []
    ids, items = set(), set()
    for p in array(model['products'], 'products'):
        if 'prompts' not in p:
            p['prompts'] = []
        p.setdefault('comment', '')
        p.setdefault('quantity', '1')
        obj(p, ['id', 'record_id', 'name', 'location', 'group', 'item_number', 'quantity', 'width', 'height', 'depth', 'x', 'y', 'z', 'angle', 'prompts', 'comment'], 'product')
        quantity(p['quantity'], 'product.quantity')
        for key in ('id', 'record_id', 'name', 'location', 'group', 'item_number'): string(p[key], 'product.' + key)
        if p['id'] in ids or p['item_number'] in items: raise Invalid('duplicate product ID or item number')
        ids.add(p['id']); items.add(p['item_number'])
        if p['location'] not in locations or p['group'] not in group_names: raise Invalid(f"product {p['id']}: unknown location or group")
        rows = db.execute("SELECT record_id, library_link_id, fields_json FROM entities WHERE table_name='Products' AND record_id=? AND name=? COLLATE BINARY", (p['record_id'],p['name'])).fetchall()
        if len(rows) != 1: raise Invalid(f"product {p['id']}: exact record ID/name pair missing or ambiguous")
        if p['name'].strip().casefold() == 'wall': raise Invalid(f"product {p['id']}: Wall products are prohibited by this profile")
        if not isinstance(p['comment'], str): raise Invalid(f"product {p['id']}: comment must be text")
        if p['comment']:
            string(p['comment'], f"product {p['id']} comment")
        if not rows[0][1] or rows[0][1] != group_libraries[p['group']]:
            raise Invalid(f"product {p['id']}: product and group library scopes do not match")
        fields = json.loads(rows[0][2])
        if fields.get('Type') != 1: raise Invalid(f"product {p['id']}: record is not an observed library product type")
        wb = db.execute("SELECT compression, payload FROM workbooks WHERE table_name='Products' AND record_id=?", (p['record_id'],)).fetchall()
        if len(wb) != 1 or wb[0][0] not in ('complete', 'direct_zip', 'incomplete_stream'): raise Invalid(f"product {p['id']}: workbook missing")
        try:
            workbook = json.loads(zlib.decompress(wb[0][1]))
        except (ValueError, zlib.error, TypeError) as exc:
            raise Invalid(f"product {p['id']}: unreadable workbook index") from exc
        if workbook.get('status') != 'ok':
            raise Invalid(f"product {p['id']}: workbook indexing failed")
        if wb[0][0] == 'incomplete_stream' and p['prompts']:
            raise Invalid(f"product {p['id']}: prompt overrides are blocked for an incomplete workbook stream")
        prompt_names = {}
        for prompt_row in workbook.get('prompt_rows', []):
            for defined in prompt_row.get('defined_names_for_value_cell', []):
                prompt_names.setdefault(defined['name'], 0)
                prompt_names[defined['name']] += 1
        seen_prompts = set()
        for prompt in p['prompts']:
            obj(prompt, ['name', 'value'], f"product {p['id']} prompt")
            name = string(prompt['name'], 'prompt.name')
            string(prompt['value'], 'prompt.value')
            if name in seen_prompts or prompt_names.get(name) != 1:
                raise Invalid(f"product {p['id']}: prompt {name!r} is duplicate, missing or ambiguous in workbook")
            seen_prompts.add(name)
        item = dict(p)
        item['workbook_compression'] = wb[0][0]
        for k in ('width', 'height', 'depth'): item[k] = number(p[k], 'product.' + k, True)
        for k in ('x', 'y', 'z', 'angle'): item[k] = number(p[k], 'product.' + k)
        item['prompts'] = [{'name':x['name'],'value':x['value']} for x in p['prompts']]
        item['comment'] = p['comment']
        products.append(item)
    return {'project': project, 'locations': locations, 'groups': normalized_groups, 'unmatched_global_prompts': unmatched_global_prompts, 'products': products}


def serialize(model):
    root = ET.Element('Root', {'Application': 'Microvellum', 'ApplicationVersion': '7.0'})
    proj = ET.SubElement(root, 'Project', {'Name': model['project']['name']})
    ET.SubElement(proj, 'JobNumber').text = model['project']['job_number']
    for tag in ('JobAddress','JobPhone','JobFax','JobEmail','Architect','Contractor','Draftsman','Estimator','GeneralContact','ProjectManager'):
        if tag in model['project']['properties']:
            ET.SubElement(proj, tag).text = model['project']['properties'][tag]
    loc = ET.SubElement(proj, 'Locations')
    for name in model['locations']: ET.SubElement(loc, 'Location', {'Name': name})
    groups = ET.SubElement(proj, 'SpecificationGroups')
    for group in model['groups']:
        group_node = ET.SubElement(groups, 'SpecificationGroup', {'Name': group['name']})
        if group['global_name'] is not None:
            global_node = ET.SubElement(group_node, 'Global', {'Name':group['global_name']})
            prompt_container = ET.SubElement(global_node, 'Prompts')
            for override in group['global_prompts']:
                prompt = ET.SubElement(prompt_container, 'Prompt', {'Name':override['name']})
                ET.SubElement(prompt, 'Value').text = override['value']
    products = ET.SubElement(proj, 'Products')
    mapping = [('Quantity', 'quantity'), ('Width','width'), ('Height','height'), ('Depth','depth'), ('ItemNumber','item_number'), ('XOrigin','x'), ('YOrigin','y'), ('ZOrigin','z'), ('Angle','angle'), ('LinkIDLocation','location'), ('LinkIDSpecificationGroup','group')]
    for p in model['products']:
        node = ET.SubElement(products, 'Product', {'Name': p['name']})
        for tag, field in mapping:
            ET.SubElement(node, tag).text = p[field] if field else '1'
            if tag == 'ItemNumber':
                ET.SubElement(node, 'Comment').text = p['comment']
        prompt_container = ET.SubElement(node, 'Prompts')
        for override in p['prompts']:
            prompt = ET.SubElement(prompt_container, 'Prompt', {'Name':override['name']})
            ET.SubElement(prompt, 'Value').text = override['value']
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def parse_xml(data):
    if len(data) > 10_000_000 or b'<!' in data: raise Invalid('XML contains a declaration or exceeds size limit')
    try: root = ET.fromstring(data)
    except ET.ParseError as exc: raise Invalid(f'XML parse: {exc}') from exc
    def node(e, tag, attrs, children):
        if e.tag != tag or set(e.attrib) != set(attrs) or [x.tag for x in e] != children or (e.text or '').strip() and children:
            raise Invalid(f'XML unexpected structure at {tag}')
    node(root, 'Root', ['Application','ApplicationVersion'], ['Project'])
    if root.attrib != {'Application':'Microvellum','ApplicationVersion':'7.0'}: raise Invalid('XML envelope mismatch')
    pr = root[0]
    def leaf(e, tag):
        node(e, tag, [], []); return e.text or ''
    if pr.tag != 'Project' or set(pr.attrib) != {'Name'} or not pr.findall('JobNumber'):
        raise Invalid('XML Project header mismatch')
    props = {}
    prop_tags = ['JobAddress','JobPhone','JobFax','JobEmail','Architect','Contractor','Draftsman','Estimator','GeneralContact','ProjectManager']
    for tag in prop_tags:
        found = pr.findall(tag)
        if len(found)>1: raise Invalid(f'duplicate project property {tag}')
        if found: props[tag]=leaf(found[0],tag)
    expected_tags = ['JobNumber'] + [tag for tag in prop_tags if tag in props] + ['Locations','SpecificationGroups','Products']
    node(pr, 'Project', ['Name'], expected_tags)
    result = {'project': {'name': pr.attrib['Name'], 'job_number': leaf(pr.find('JobNumber'), 'JobNumber'), 'properties':props}, 'locations': [], 'groups': [], 'products': []}
    offset = len(expected_tags)-3
    container = pr[offset]
    node(container, 'Locations', [], ['Location'] * len(container))
    for e in container:
        node(e, 'Location', ['Name'], []); result['locations'].append(e.attrib['Name'])
    container = pr[offset+1]
    node(container, 'SpecificationGroups', [], ['SpecificationGroup'] * len(container))
    for e in container:
        child_tags = [x.tag for x in e]
        if child_tags not in ([], ['Global']): raise Invalid('XML unexpected SpecificationGroup content')
        node(e, 'SpecificationGroup', ['Name'], child_tags)
        group = {'name':e.attrib['Name'], 'global_name':None, 'global_prompts':[]}
        if child_tags:
            glob = e[0]
            node(glob, 'Global', ['Name'], ['Prompts'])
            group['global_name'] = glob.attrib['Name']
            prompt_box = glob[0]
            node(prompt_box, 'Prompts', [], ['Prompt'] * len(prompt_box))
            for override in prompt_box:
                node(override, 'Prompt', ['Name'], ['Value'])
                group['global_prompts'].append({'name':override.attrib['Name'],'value':leaf(override[0], 'Value')})
        result['groups'].append(group)
    ps = pr[offset+2]; tags = ['Quantity','Width','Height','Depth','ItemNumber','Comment','XOrigin','YOrigin','ZOrigin','Angle','LinkIDLocation','LinkIDSpecificationGroup','Prompts']
    node(ps, 'Products', [], ['Product'] * len(ps))
    for p in ps:
        node(p, 'Product', ['Name'], tags)
        vals = [leaf(p[i], tag) for i, tag in enumerate(tags[:-1])]
        node(p[-1], 'Prompts', [], ['Prompt'] * len(p[-1]))
        prompt_values = []
        for override in p[-1]:
            node(override, 'Prompt', ['Name'], ['Value'])
            prompt_values.append({'name':override.attrib['Name'],'value':leaf(override[0], 'Value')})
        quantity(vals[0], 'XML product quantity')
        result['products'].append(dict(zip(['name','quantity','width','height','depth','item_number','comment','x','y','z','angle','location','group'], [p.attrib['Name']] + vals)) | {'prompts':prompt_values})
    return result


def comparable(model):
    return {'project':{'name':model['project']['name'],'job_number':model['project']['job_number'],'properties':model['project']['properties']}, 'locations':model['locations'], 'groups':model['groups'], 'products': [{k:p[k] for k in ('name','quantity','width','height','depth','item_number','comment','x','y','z','angle','location','group','prompts')} for p in model['products']]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['validate','export'])
    ap.add_argument('--model', type=Path, required=True); ap.add_argument('--catalog', type=Path, required=True)
    ap.add_argument('--out', type=Path, help='new output directory for experimental artifact')
    ap.add_argument('--experimental', action='store_true', help='acknowledge target import behavior is unverified')
    args = ap.parse_args()
    try:
        model = load_json(args.model)
        catalog_hash = digest(args.catalog)
        if model.get('catalog_sha256') != catalog_hash: raise Invalid('catalog SHA-256 mismatch')
        uri = args.catalog.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise Invalid('catalog integrity check failed')
            schema_version = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if schema_version not in (('1',), ('2',)): raise Invalid('unsupported catalog schema')
            source = db.execute("SELECT value FROM metadata WHERE key='source_sqlite_sha256'").fetchone()
            if not source: raise Invalid('catalog source fingerprint missing')
            normalized = validate(model, db)
        data = serialize(normalized)
        if parse_xml(data) != comparable(normalized): raise Invalid('independent XML comparison failed')
        incomplete_workbooks = [{'item_number':p['item_number'],'name':p['name'],'compression':p['workbook_compression']} for p in normalized['products'] if p['workbook_compression'] == 'incomplete_stream']
        report = {'status':'insufficient_evidence', 'static_checks':'passed', 'target_import_tested':False, 'experimental_only':True, 'model_sha256':digest(args.model), 'catalog_sha256':catalog_hash, 'catalog_source_sqlite_sha256':source[0], 'model_source':model.get('source'), 'xml_sha256':hashlib.sha256(data).hexdigest(), 'product_count':len(normalized['products']), 'wall_xml_count':0, 'unmatched_global_prompts':normalized['unmatched_global_prompts'], 'incomplete_product_workbooks':incomplete_workbooks, 'target':model['target'], 'limitations':['Target library provenance, units, coordinates, importer semantics and workbook calculation are unverified.', 'The model records project groups without unique catalog matches as omitted; product metadata outside this XML schema is not represented.', 'At least one selected product workbook may be an incomplete research-catalog stream; without a complete instance workbook this limits dependency validation.', 'Prompt values may include literal text and numeric formula caches; global prompt candidates are also unverified. Their override necessity and target calculation/import behavior are unknown.', 'Walls are unsupported. Quantity greater than one is encoded in one product with one position; target behavior is unverified. Independent read-back is not target acceptance.']}
        if args.command == 'export':
            if not args.experimental or args.out is None: raise Invalid('export requires --experimental and --out NEW_DIRECTORY')
            args.out.mkdir(parents=False, exist_ok=False)
            (args.out / 'project.xml').write_bytes(data)
            (args.out / 'validation.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            (args.out / 'README.txt').write_text('EXPERIMENTAL CALIBRATION ONLY. Not verified for import or manufacture. Review validation.json and compare against the target in a disposable project. Do not append to production.\n', encoding='utf-8')
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except (Invalid, OSError, sqlite3.Error, json.JSONDecodeError, TypeError, AttributeError, KeyError) as exc:
        print(json.dumps({'status':'blocked','error':str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
