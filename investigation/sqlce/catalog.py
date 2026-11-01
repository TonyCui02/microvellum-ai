"""Build and query a local, evidence-preserving Microvellum research catalog."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import zlib

from decode_workbook import TABLES

ENTITY_TABLES = ('Libraries', 'Categories', 'Projects', 'SpecificationGroups', 'Materials', 'Sheets') + TABLES
WARNING = ('Research snapshot; not verified against the target workstation. '
           'Cached values are not calculated results. Prompt override and import behavior are unverified.')


def sha_file(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def connect_readonly(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def build(args):
    source = args.source.resolve()
    dest = args.catalog.resolve()
    if dest.exists():
        raise ValueError('catalog output already exists')
    source_hash = sha_file(source)
    index_hash = sha_file(args.index)
    # Exclusive creation prevents accidentally overwriting any database.
    with dest.open('xb'):
        pass
    result = sqlite3.connect(dest)
    try:
        result.executescript('''
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE entities (table_name TEXT NOT NULL, record_id TEXT NOT NULL, link_id TEXT,
          name TEXT, category_link_id TEXT, library_link_id TEXT, project_link_id TEXT,
          fields_json TEXT NOT NULL, PRIMARY KEY(table_name, record_id));
        CREATE INDEX entity_name ON entities(table_name, name COLLATE NOCASE);
        CREATE INDEX entity_link ON entities(table_name, link_id);
        CREATE TABLE workbooks (table_name TEXT NOT NULL, record_id TEXT NOT NULL,
          link_id TEXT, blob_sha256 TEXT NOT NULL, compression TEXT, payload BLOB NOT NULL,
          PRIMARY KEY(table_name, record_id));
        CREATE TABLE prompt_names (table_name TEXT NOT NULL, record_id TEXT NOT NULL,
          row_number INTEGER, defined_name TEXT, display_label TEXT);
        CREATE INDEX prompt_name_index ON prompt_names(defined_name COLLATE NOCASE);
        ''')
        result.executemany('INSERT INTO metadata VALUES (?,?)', [('source_sqlite_sha256', source_hash),
                           ('workbook_index_sha256', index_hash), ('schema_version', '2'), ('warning', WARNING)])
        with connect_readonly(source) as db:
            for table in ENTITY_TABLES:
                for row in db.execute(f'SELECT * FROM "{table}"'):
                    fields = {k: row[k] for k in row.keys() if not isinstance(row[k], bytes)}
                    result.execute('INSERT INTO entities VALUES (?,?,?,?,?,?,?,?)',
                                   (table, str(row['ID']), row['LinkID'], row['Name'] if 'Name' in row.keys() else None,
                                    fields.get('LinkIDCategory'), fields.get('LinkIDLibrary'), fields.get('LinkIDProject'),
                                    json.dumps(fields, ensure_ascii=False)))
            good = errors = 0
            with args.index.open(encoding='utf-8') as f:
                for line in f:
                    item = json.loads(line)
                    if item['source_database_sha256'] != source_hash:
                        raise ValueError('workbook index fingerprint does not match source database')
                    if item['table'] not in TABLES:
                        raise ValueError('unexpected workbook table')
                    source_rows = db.execute(f'SELECT WorkBook FROM "{item["table"]}" WHERE ID=?', (item['record_id'],)).fetchall()
                    if len(source_rows) != 1 or source_rows[0][0] is None or hashlib.sha256(source_rows[0][0]).hexdigest() != item['blob_sha256']:
                        raise ValueError('workbook index does not match source record')
                    if item['status'] != 'ok':
                        errors += 1
                    else:
                        good += 1
                    result.execute('INSERT INTO workbooks VALUES (?,?,?,?,?,?)',
                        (item['table'], item['record_id'], item['link_id'], item['blob_sha256'], item.get('compression'), zlib.compress(line.encode('utf-8'))))
                    for row in item.get('prompt_rows', []):
                        label = row['cells'].get('A', {}).get('value')
                        for named in row['defined_names_for_value_cell']:
                            result.execute('INSERT INTO prompt_names VALUES (?,?,?,?,?)',
                                           (item['table'], item['record_id'], row['row'], named['name'], label))
            expected = sum(db.execute(f'SELECT count(*) FROM "{table}" WHERE WorkBook IS NOT NULL').fetchone()[0] for table in TABLES)
            if good + errors != expected:
                raise ValueError(f'workbook index count differs from source: {good + errors} vs {expected}')
        result.executemany('INSERT INTO metadata VALUES (?,?)', [('indexed_workbooks', str(good)), ('failed_workbooks', str(errors))])
        result.commit()
        print(json.dumps({'catalog': str(dest), 'source_sha256': source_hash, 'indexed_workbooks': good, 'failed_workbooks': errors, 'warning': WARNING}))
        return int(errors > 0)
    finally:
        result.close()


def search(args):
    with connect_readonly(args.catalog) as db:
        # Literal substring matching, not SQL wildcard interpretation.
        rows = db.execute('''SELECT record_id, link_id, name, category_link_id, library_link_id, project_link_id
                            FROM entities WHERE table_name='Products' AND instr(lower(name), lower(?)) > 0
                            ORDER BY name COLLATE NOCASE, record_id LIMIT ?''', (args.query, args.limit)).fetchall()
        print(json.dumps({'query': args.query, 'products': [dict(row) for row in rows], 'limit': args.limit, 'warning': WARNING}, ensure_ascii=False, indent=2))


def search_prompts(args):
    with connect_readonly(args.catalog) as db:
        rows = db.execute('''SELECT p.record_id, e.name AS product_name, p.row_number,
                                    p.defined_name, p.display_label
                             FROM prompt_names p JOIN entities e
                               ON e.table_name=p.table_name AND e.record_id=p.record_id
                             WHERE p.table_name='Products'
                               AND (instr(lower(p.defined_name), lower(?)) > 0
                                    OR instr(lower(coalesce(p.display_label,'')), lower(?)) > 0)
                             ORDER BY e.name COLLATE NOCASE, p.row_number LIMIT ?''',
                          (args.query, args.query, args.limit)).fetchall()
        print(json.dumps({'query': args.query, 'matches': [dict(row) for row in rows],
                          'limit': args.limit, 'warning': WARNING}, ensure_ascii=False, indent=2))


def product(args):
    with connect_readonly(args.catalog) as db:
        row = db.execute("SELECT * FROM entities WHERE table_name='Products' AND record_id=?", (args.record_id,)).fetchone()
        if row is None:
            raise ValueError('product record ID not found; use search first')
        raw = json.loads(row['fields_json'])
        wb = db.execute("SELECT payload FROM workbooks WHERE table_name='Products' AND record_id=?", (args.record_id,)).fetchone()
        item = json.loads(zlib.decompress(wb['payload'])) if wb else None
        prompts = []
        if item and item['status'] == 'ok':
            for r in item['prompt_rows']:
                if r['defined_names_for_value_cell']:
                    prompts.append({'row': r['row'], 'defined_names': r['defined_names_for_value_cell'],
                                    'display_label': r['cells'].get('A'), 'value_cell': r['cells'].get('B'),
                                    'row_cells': r['cells']})
        categories = [dict(x) for x in db.execute("SELECT record_id, name, link_id FROM entities WHERE table_name='Categories' AND link_id=?", (row['category_link_id'],))]
        subs = [dict(x) for x in db.execute("SELECT record_id, link_id, name FROM entities WHERE table_name='Subassemblies' AND json_extract(fields_json,'$.LinkIDParentProduct')=?", (row['link_id'],))]
        spec = [dict(x) for x in db.execute("SELECT record_id, name, link_id, fields_json FROM entities WHERE table_name='SpecificationGroups' AND link_id=?", (raw.get('LinkIDSpecificationGroup'),))]
        for match in spec:
            match['fields'] = json.loads(match.pop('fields_json'))
        print(json.dumps({'product': raw, 'category_matches': categories, 'specification_group_matches': spec,
                          'subassembly_parent_link_matches': subs,
                          'workbook': {'status': item['status'], 'compression': item.get('compression'),
                          'blob_sha256': item.get('blob_sha256'), 'workbook_sha256': item.get('workbook_sha256'),
                          'sheets': item.get('sheets'), 'defined_names': item.get('defined_names'),
                          'external_relationships': item.get('external_relationships')} if item else None,
                          'prompts_with_simple_defined_names': prompts, 'warning': WARNING}, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build'); b.add_argument('source', type=Path); b.add_argument('index', type=Path); b.add_argument('catalog', type=Path); b.set_defaults(func=build)
    s = sub.add_parser('search'); s.add_argument('catalog', type=Path); s.add_argument('query'); s.add_argument('--limit', type=int, default=20); s.set_defaults(func=search)
    ps = sub.add_parser('search-prompts'); ps.add_argument('catalog', type=Path); ps.add_argument('query'); ps.add_argument('--limit', type=int, default=20); ps.set_defaults(func=search_prompts)
    q = sub.add_parser('product'); q.add_argument('catalog', type=Path); q.add_argument('record_id'); q.set_defaults(func=product)
    a = p.parse_args()
    if a.command in ('search', 'search-prompts') and not 1 <= a.limit <= 100:
        p.error('limit must be between 1 and 100')
    try:
        return a.func(a) or 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
