"""Serve the active agent-prepared drawing review on localhost."""
import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from model_validation import validate_model
from material_catalog import search_materials
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
MANIFEST = ROOT / '.active-review.json'
CATALOG = None
CATALOG_SHA256 = None
MODELS_DIR = None


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pdf_page_count(path):
    if path.suffix.lower() == '.png':
        return 1
    info = subprocess.run(['pdfinfo', str(path)], check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
    match = re.search(r'^Pages:\s*(\d+)\s*$', info.stdout, re.M)
    if not match:
        raise ValueError('Could not determine the source PDF page count.')
    return int(match.group(1))


def normalized_preview_info(model_data, model_path):
    """Recognize the separate XML model shape for a schematic preview."""
    if (model_data.get('schema_version') != '1' or not isinstance(model_data.get('target'), dict)
            or not isinstance(model_data.get('project'), dict) or not isinstance(model_data.get('products'), list)
            or not model_data['products']):
        return None
    project_name = model_data['project'].get('name')
    units = model_data['target'].get('output_units')
    if not isinstance(project_name, str) or units not in ('mm', 'in'):
        return None
    paired = [p for p in model_path.parent.iterdir() if p.is_file() and p.suffix.lower() in ('.pdf', '.png') and p.stem == project_name]
    if len(paired) != 1:
        return None
    rows=[]
    try:
        for index, product in enumerate(model_data['products'], 1):
            ident = str(product.get('item_number') or product.get('id') or index)
            dims = {key: float(product[key]) for key in ('width','height','depth')}
            pose = {key: float(product[key]) for key in ('x','y','z','angle')}
            if any(not (value > 0 and value < float('inf')) for value in dims.values()):
                return None
            if any(not (abs(value) < float('inf')) for value in pose.values()):
                return None
            rows.append((product, ident, dims, pose))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return {'pdf': paired[0], 'project_name': project_name, 'units': units, 'rows': rows}


def adapt_normalized_model(model_data, model_path, pdf):
    info = normalized_preview_info(model_data, model_path)
    if not info or info['pdf'].resolve() != pdf.resolve():
        raise ValueError('Selected normalized JSON no longer has a unique matching source drawing.')
    rows=info['rows']
    min_x=min(row[3]['x'] for row in rows)
    max_x=max(row[3]['x']+row[2]['width'] for row in rows)
    max_height=max(row[3]['z']+row[2]['height'] for row in rows)
    wall_id='W1'
    products=[]
    inventory=[]
    for raw, ident, dims, pose in rows:
        name=raw.get('name') or 'Unspecified product'
        part=ident[:1].upper() in ('P','B','S','A','T')
        prompt_overrides=[{'name':p.get('name',''), 'value':p.get('value'), 'unit':None,
                           'reason':'Copied from the selected normalized JSON; importer behavior is unverified.',
                           'confidence':0.5, 'status':'proposed'} for p in raw.get('prompts',[])]
        products.append({
            'id':ident, 'label':f'{ident} · {name}', 'kind':'part' if part else ('upper' if pose['z'] > 0 else 'base'),
            'library_product':{'name':name,'record_id':raw.get('record_id'),'link_id':None,'match_status':'candidate',
                               'match_reason':'Copied from normalized JSON; active-library match and coordinate semantics are unverified.'},
            'wall_id':wall_id, 'offset':pose['x']-min_x, 'dimensions':dims, 'visual_dimensions':{},
            'elevation':pose['z'], 'rotation':pose['angle'], 'quantity':1, 'status':'needs_review', 'confidence':0.5,
            'evidence':['Schematic preview copied from the selected normalized JSON; values were not reinterpreted.'],
            'prompt_overrides':prompt_overrides,
            'dimension_evidence':{key:{'status':'confirmed','description':'Copied from normalized JSON; drawing match is not verified.'} for key in dims},
            'review_details':{'dimensions':{key:'Copied from selected JSON; verify against the source drawing.' for key in dims},'drawing':[],'questions':[]},
            'material':None,
        })
        inventory.append({'id':f'item-{ident}','label':f'{ident} · {name}','kind':'part' if part else 'cabinet',
                          'resolution':'model_product','model_id':ident,
                          'evidence':['Copied from normalized JSON for read-only schematic display.']})
    page_count=pdf_page_count(pdf)
    pdf_hash=file_hash(pdf)
    return {
        'schema_version':'preview-adapter-1',
        'project':{'name':info['project_name']+' · normalized JSON preview','units':info['units'],'review_status':'read_only','revision':0},
        'source':{'file':pdf.name,'path':str(pdf),'visual_review':{'source_sha256':pdf_hash,'inspected_pages':list(range(1,page_count+1))}},
        'room':{'id':'preview-room','name':'Preview','height':max(2400,max_height),'walls':[{'id':wall_id,'name':'Schematic X/Z layout','start':[0,0],'end':[max(max_x-min_x,1),0],'thickness':1,'height':max(2400,max_height)}],
                'openings':[],'products':products,'features':[],'drawing_inventory':inventory},
        'drawing_measurements':[],
        'review':{'issues':['Normalized JSON preview. X/Z values were copied from the source JSON; origin, axes, rotations and target import behavior are unverified.'], 'applied_rules':[]},
    }


def active():
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    model = Path(data['model']).resolve(strict=True)
    pdf = Path(data['pdf']).resolve(strict=True)
    if not model.is_file() or not pdf.is_file() or model.suffix.lower() != '.json' or pdf.suffix.lower() not in ('.pdf', '.png'):
        raise ValueError('Review files must be an existing JSON and PDF or PNG.')
    model_data=json.loads(model.read_text(encoding='utf-8-sig'))
    if data.get('display_only'):
        choice=model_choice(model)
        if not choice.get('selectable') or not choice.get('display_only'):
            raise ValueError(choice.get('reason','Selected normalized JSON cannot be previewed.'))
        if choice['pdf_name'] != pdf.name or file_hash(pdf) != data.get('pdf_sha256'):
            raise ValueError('The selected model or source drawing changed. Re-select the JSON to refresh its preview.')
        return data, model, pdf
    # An incomplete drawing model is useful for review. Keep unresolved entities
    # visible here; the XML exporter still calls strict validation and blocks them.
    errors = validate_model(model_data, allow_unresolved=True)
    if errors:
        raise ValueError('Invalid review model: ' + ' '.join(errors))
    review=model_data.get('source',{}).get('visual_review',{})
    if model_data.get('source',{}).get('file') != pdf.name or review.get('source_sha256') != data.get('pdf_sha256') or sorted(review.get('inspected_pages',[])) != list(range(1,data.get('page_count',0)+1)):
        errors.append('Source inspection does not match the published drawing.')
    if errors:
        raise ValueError('Invalid review model: ' + ' '.join(errors))
    if file_hash(pdf) != data['pdf_sha256']:
        raise ValueError('The source PDF has changed since this draft was published. Ask the agent to refresh the review.')
    return data, model, pdf


def model_choice(model_path):
    """Describe a JSON that can safely drive the drawing-review UI."""
    result = {'key': model_path.name, 'name': model_path.name, 'selectable': False}
    try:
        model_data = json.loads(model_path.read_text(encoding='utf-8-sig'))
        errors = validate_model(model_data, allow_unresolved=True)
        if errors:
            normalized=normalized_preview_info(model_data, model_path)
            if normalized:
                result.update({'selectable':True,'display_only':True,'project_name':normalized['project_name'],
                               'pdf_name':normalized['pdf'].name,'source_format':normalized['pdf'].suffix.lower()[1:],
                               'revision':file_hash(model_path),
            'reason':'Normalized JSON preview. Dimensions are editable; X/Z placement is copied without coordinate calibration.'})
                return result
            result['reason'] = 'Not a supported review or normalized product JSON: ' + ' '.join(errors[:2])
            return result
        source = model_data.get('source', {})
        pdf_name = source.get('file')
        visual = source.get('visual_review', {})
        pages = visual.get('inspected_pages', [])
        pdf = model_path.parent / pdf_name if isinstance(pdf_name, str) and Path(pdf_name).name == pdf_name else None
        if pdf is None or not pdf.is_file() or pdf.suffix.lower() not in ('.pdf', '.png'):
            result['reason'] = 'The matching PDF or PNG is not beside this JSON.'
            return result
        if not pages or sorted(pages) != list(range(1, len(pages) + 1)):
            result['reason'] = 'The model has no complete visual-review page record.'
            return result
        if visual.get('source_sha256') != file_hash(pdf):
            result['reason'] = 'The source drawing changed since this model was reviewed.'
            return result
        result.update({
            'selectable': True,
            'display_only': False,
            'project_name': model_data['project']['name'],
            'pdf_name': pdf.name,
            'source_format': pdf.suffix.lower()[1:],
            'revision': file_hash(model_path),
        })
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        result['reason'] = f'Cannot use this JSON: {exc}'
    return result


def model_choices():
    choices = []
    if MODELS_DIR and MODELS_DIR.is_dir():
        for path in sorted(MODELS_DIR.glob('*.json'), key=lambda p: p.name.casefold()):
            choices.append(model_choice(path))
    try:
        _, model, _ = active()
        if not any((MODELS_DIR / choice['key']).resolve() == model.resolve() for choice in choices):
            choices.append(model_choice(model))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return choices


def select_model(key):
    if not isinstance(key, str) or not key or Path(key).name != key or MODELS_DIR is None:
        raise ValueError('Choose a JSON from the configured model folder.')
    model_path = (MODELS_DIR / key).resolve(strict=True)
    if model_path.parent != MODELS_DIR.resolve() or model_path.suffix.lower() != '.json':
        raise ValueError('The selected JSON is outside the configured model folder.')
    choice = model_choice(model_path)
    if not choice.get('selectable'):
        raise ValueError(choice.get('reason', 'This JSON cannot be used by the drawing-review page.'))
    model_data = json.loads(model_path.read_text(encoding='utf-8-sig'))
    if choice.get('display_only'):
        preview=normalized_preview_info(model_data, model_path)
        pdf_path=preview['pdf'].resolve(strict=True)
    else:
        pdf_path = (model_path.parent / model_data['source']['file']).resolve(strict=True)
    manifest = {
        'model': str(model_path),
        'pdf': str(pdf_path),
        'pdf_sha256': file_hash(pdf_path),
        'page_count': pdf_page_count(pdf_path),
        'display_only': bool(choice.get('display_only')),
    }
    temp = MANIFEST.with_suffix('.tmp')
    temp.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, MANIFEST)
    return choice


def save_normalized_dimensions(model_path, expected_revision, payload):
    if not expected_revision or expected_revision != file_hash(model_path):
        raise ValueError('This JSON changed since you opened it. Reload before saving.')
    if not isinstance(payload, dict) or set(payload) != {'item_id', 'dimensions'}:
        raise ValueError('Expected an item ID and width, height, and depth.')
    item_id=payload['item_id']
    dimensions=payload['dimensions']
    if not isinstance(item_id, str) or not isinstance(dimensions, dict) or set(dimensions) != {'width','height','depth'}:
        raise ValueError('Expected width, height, and depth values for one item.')
    normalized={}
    for key, value in dimensions.items():
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{key.title()} must be a finite positive number.')
        normalized[key]=value
    model_data=json.loads(model_path.read_text(encoding='utf-8-sig'))
    matches=[p for p in model_data.get('products',[]) if str(p.get('item_number') or p.get('id')) == item_id]
    if len(matches) != 1:
        raise ValueError('The selected product no longer resolves uniquely in this JSON.')
    for key, value in normalized.items():
        old=matches[0].get(key)
        matches[0][key]=format(value, '.15g') if isinstance(old, str) else value
    temp_name=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',newline='\n',dir=model_path.parent,delete=False) as out:
            temp_name=out.name
            json.dump(model_data,out,ensure_ascii=False,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
        if file_hash(model_path) != expected_revision:
            raise ValueError('This JSON changed while saving. Reload before saving.')
        os.replace(temp_name,model_path)
    finally:
        if temp_name and os.path.exists(temp_name): os.unlink(temp_name)
    return file_hash(model_path)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def write_response(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path).path
        if route.startswith('/.'):
            return self.send_error(404)
        if route == '/api/models':
            return self.write_response(200, json.dumps({'models': model_choices()}, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
        if route.startswith('/api/catalog/'):
            return self.catalog_search(route)
        if route not in ('/api/review', '/api/model', '/api/drawing'):
            return super().do_GET()
        try:
            data, model, pdf = active()
            model_data=json.loads(model.read_text(encoding='utf-8-sig'))
            if route == '/api/review':
                body = json.dumps({'model_name': model.name, 'pdf_name': pdf.name, 'source_format':pdf.suffix.lower()[1:],
                                   'revision': file_hash(model), 'model_key': model.name,
                                   'project_name': model_data.get('project', {}).get('name', model.name),
                                   'display_only': bool(data.get('display_only')),
                                   'pdf_sha256': data['pdf_sha256']}).encode('utf-8')
                return self.write_response(200, body, 'application/json; charset=utf-8')
            if route == '/api/model' and data.get('display_only'):
                preview=adapt_normalized_model(model_data, model, pdf)
                body=json.dumps(preview,ensure_ascii=False).encode('utf-8')
                return self.write_response(200, body, 'application/json; charset=utf-8')
            path = model if route == '/api/model' else pdf
            content = 'application/json; charset=utf-8' if route == '/api/model' else ('application/pdf' if pdf.suffix.lower()=='.pdf' else 'image/png')
            return self.write_response(200, path.read_bytes(), content)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.write_response(404, json.dumps({'error': str(exc)}).encode('utf-8'), 'application/json; charset=utf-8')

    def do_POST(self):
        route = urlparse(self.path).path
        if route not in ('/api/model', '/api/select-model', '/api/dimensions'):
            return self.send_error(404)
        try:
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return self.write_response(403, b'{"error":"Cross origin edits are not allowed."}', 'application/json; charset=utf-8')
            if route == '/api/select-model':
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 10000:
                    return self.write_response(400, b'{"error":"Invalid selection request."}', 'application/json; charset=utf-8')
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                choice = select_model(payload.get('key') if isinstance(payload, dict) else None)
                return self.write_response(200, json.dumps(choice).encode('utf-8'), 'application/json; charset=utf-8')
            data, model_path, _ = active()
            if route == '/api/dimensions':
                if not data.get('display_only'):
                    return self.write_response(400,b'{"error":"Dimension-only updates are for normalized product previews."}','application/json; charset=utf-8')
                length=int(self.headers.get('Content-Length','0'))
                if length <= 0 or length > 10000:
                    return self.write_response(400,b'{"error":"Invalid dimension update."}','application/json; charset=utf-8')
                payload=json.loads(self.rfile.read(length).decode('utf-8'))
                revision=save_normalized_dimensions(model_path,self.headers.get('If-Match','').strip('"'),payload)
                return self.write_response(200,json.dumps({'revision':revision}).encode('utf-8'),'application/json; charset=utf-8')
            if data.get('display_only'):
                return self.write_response(403, b'{"error":"This normalized JSON is shown in read-only preview mode."}', 'application/json; charset=utf-8')
            expected = self.headers.get('If-Match', '').strip('"')
            if not expected or expected != file_hash(model_path):
                return self.write_response(409, b'{"error":"This JSON changed since you opened it. Reload before saving."}', 'application/json; charset=utf-8')
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 2_000_000:
                return self.write_response(400, b'{"error":"Invalid request size."}', 'application/json; charset=utf-8')
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            errors = validate_model(payload, allow_unresolved=True)
            if errors:
                return self.write_response(400, json.dumps({'error': ' '.join(errors)}).encode('utf-8'), 'application/json; charset=utf-8')
            if payload.get('source', {}).get('file') != Path(data['pdf']).name:
                raise ValueError('The drawing reference cannot be changed from this editor.')
            current_model = json.loads(model_path.read_text(encoding='utf-8-sig'))
            payload.setdefault('project', {})['revision'] = int(current_model.get('project', {}).get('revision', 0)) + 1
            project_revision = payload['project']['revision']
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', dir=model_path.parent, delete=False) as out:
                    temp_name = out.name
                    json.dump(payload, out, ensure_ascii=False, indent=2)
                    out.write('\n')
                    out.flush()
                    os.fsync(out.fileno())
                # Recheck immediately before replacement to avoid clobbering an agent edit.
                if file_hash(model_path) != expected:
                    return self.write_response(409, b'{"error":"This JSON changed while saving. Reload before saving."}', 'application/json; charset=utf-8')
                os.replace(temp_name, model_path)
            finally:
                if temp_name and os.path.exists(temp_name):
                    os.unlink(temp_name)
            return self.write_response(200, json.dumps({'revision': file_hash(model_path), 'project_revision': project_revision}).encode('utf-8'), 'application/json; charset=utf-8')
        except (OSError, ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return self.write_response(400, json.dumps({'error': str(exc)}).encode('utf-8'), 'application/json; charset=utf-8')

    def catalog_search(self, route):
        kind = route.removeprefix('/api/catalog/')
        table = {'products': 'Products', 'materials': 'Materials', 'groups': 'SpecificationGroups'}.get(kind)
        if not table:
            return self.send_error(404)
        if CATALOG is None:
            return self.write_response(503, b'{"error":"No catalog configured. Start server with --catalog <catalog.sqlite>."}', 'application/json; charset=utf-8')
        query = urlparse(self.path).query
        from urllib.parse import parse_qs
        params = parse_qs(query)
        term = params.get('q', [''])[0][:100]
        group_name = params.get('group', [''])[0][:200]
        group_link_id = params.get('group_link_id', [''])[0][:100]
        role = params.get('role', [''])[0][:80]
        try:
            offset = max(0, min(int(params.get('offset', ['0'])[0]), 100000))
        except ValueError:
            return self.write_response(400, b'{"error":"Invalid catalog offset."}', 'application/json; charset=utf-8')
        try:
            uri = CATALOG.resolve(strict=True).as_uri() + '?mode=ro'
            with closing(sqlite3.connect(uri, uri=True)) as db:
                db.row_factory = sqlite3.Row
                if table == 'Products':
                    libraries = db.execute("SELECT link_id,name FROM entities WHERE table_name='Libraries' ORDER BY name COLLATE NOCASE").fetchall()
                    if len(libraries) != 1:
                        return self.write_response(503, b'{"error":"Catalog must contain exactly one configured library for product choices."}', 'application/json; charset=utf-8')
                    library_id, library_name = libraries[0]
                    # Keep project-linked records that are stored under this library.
                    # In this snapshot the Template project contains legitimate catalog
                    # products (including the canonical “2 Door Upper” choices).
                    base_sql = '''FROM entities e WHERE e.table_name='Products' AND e.library_link_id=?
                          AND (instr(lower(coalesce(e.name,'')),lower(?))>0
                            OR instr(lower(e.record_id),lower(?))>0 OR instr(lower(coalesce(e.link_id,'')),lower(?))>0
                            OR instr(lower(e.fields_json),lower(?))>0)'''
                    params = (library_id, term, term, term, term)
                    total = db.execute('SELECT count(*) ' + base_sql, params).fetchone()[0]
                    rows = db.execute('''SELECT e.record_id, e.link_id, e.name, e.category_link_id, e.library_link_id, e.project_link_id,
                                json_extract(e.fields_json,'$.LinkIDSpecificationGroup') AS specification_group_link,
                                (SELECT s.record_id FROM entities s WHERE s.table_name='SpecificationGroups' AND s.link_id=json_extract(e.fields_json,'$.LinkIDSpecificationGroup') LIMIT 1),
                                (SELECT s.link_id FROM entities s WHERE s.table_name='SpecificationGroups' AND s.link_id=json_extract(e.fields_json,'$.LinkIDSpecificationGroup') LIMIT 1),
                                (SELECT s.name FROM entities s WHERE s.table_name='SpecificationGroups' AND s.link_id=json_extract(e.fields_json,'$.LinkIDSpecificationGroup') LIMIT 1)
                        ''' + base_sql + ''' ORDER BY e.name COLLATE NOCASE, e.record_id LIMIT 50 OFFSET ?''', (*params, offset)).fetchall()
                elif table == 'Materials':
                    if not group_name or not role:
                        return self.write_response(400, b'{"error":"Material search requires an exact specification group and role."}', 'application/json; charset=utf-8')
                    rows, total = search_materials(db, group_name, role, term, 50, offset, group_link_id or None)
                    source = db.execute("SELECT value FROM metadata WHERE key='source_sqlite_sha256'").fetchone()
                    return self.write_response(200, json.dumps({
                        'results': [{**row, 'is_custom': False} for row in rows],
                        'scope': f'{group_name} · {role} candidates', 'total': total, 'offset': offset,
                        'group': group_name, 'group_link_id': group_link_id or None, 'role': role, 'catalog_sha256': CATALOG_SHA256,
                        'source_sqlite_sha256': source[0] if source else None,
                        'warning': 'Candidates are members of the selected specification group workbook in this analysis snapshot. They may be available but not assigned to a product pointer; active workstation provenance is unverified.'
                    }, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
                else:
                    libraries = db.execute("SELECT link_id,name FROM entities WHERE table_name='Libraries' ORDER BY name COLLATE NOCASE").fetchall()
                    if len(libraries) != 1:
                        return self.write_response(503, b'{"error":"Catalog must contain exactly one configured library for group choices."}', 'application/json; charset=utf-8')
                    library_id, library_name = libraries[0]
                    base_sql = "FROM entities WHERE table_name='SpecificationGroups' AND library_link_id=? AND instr(lower(coalesce(name,'')),lower(?))>0"
                    total = db.execute('SELECT count(*) ' + base_sql, (library_id, term)).fetchone()[0]
                    rows = db.execute('SELECT record_id,link_id,name,category_link_id,library_link_id,project_link_id ' + base_sql + ' ORDER BY name COLLATE NOCASE,record_id LIMIT 50 OFFSET ?', (library_id, term, offset)).fetchall()
                    results = [{'record_id': r[0], 'link_id': r[1], 'name': r[2], 'category_link_id': r[3],
                                'library_link_id': r[4], 'project_link_id': r[5], 'is_custom': False} for r in rows]
                    return self.write_response(200, json.dumps({'results': results, 'scope': library_name + ' specification groups',
                        'total': total, 'offset': offset, 'catalog_sha256': CATALOG_SHA256}, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
            results = []
            for r in rows:
                group = {'record_id': r[7], 'link_id': r[8], 'name': r[9]} if table == 'Products' and r[7] else None
                results.append({'record_id': r[0], 'link_id': r[1], 'name': r[2], 'category_link_id': r[3],
                                'library_link_id': r[4], 'project_link_id': r[5], 'specification_group': group, 'is_custom': False})
            return self.write_response(200, json.dumps({'results': results, 'scope': library_name, 'total': total, 'offset': offset,
                'catalog_sha256': CATALOG_SHA256,
                'warning': 'Choices are exact records from the configured SDF analysis snapshot; active workstation provenance is unverified.'}, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
        except (OSError, sqlite3.Error) as exc:
            return self.write_response(503, json.dumps({'error': f'Catalog search failed: {exc}'}).encode('utf-8'), 'application/json; charset=utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--catalog', type=Path, help='Optional normalized read-only catalog SQLite file')
    parser.add_argument('--models-dir', type=Path, help='Folder whose drawing-review JSON files appear in the page selector (defaults to the active model folder)')
    args = parser.parse_args()
    CATALOG = args.catalog.resolve() if args.catalog else None
    CATALOG_SHA256 = file_hash(CATALOG) if CATALOG else None
    if args.models_dir:
        MODELS_DIR = args.models_dir.resolve()
    else:
        try:
            MODELS_DIR = Path(json.loads(MANIFEST.read_text(encoding='utf-8'))['model']).resolve().parent
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            MODELS_DIR = ROOT
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Review: http://127.0.0.1:{args.port}/ (Ctrl+C to stop)', flush=True)
    server.serve_forever()
