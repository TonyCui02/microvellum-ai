"""Create a shareable catalog copy with machine-local paths redacted.

This does not establish redistribution rights or prove that other sensitive data
is absent. Review the source and the generated file before sharing.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import zlib

LOCAL_PATH = re.compile(r'(?i)(?:[a-z]:[\\/]+(?:users|projects|documents|downloads)[\\/]+|file:/+[^\s]*[\\/]+users[\\/]+)')


def redact(value):
    if isinstance(value, dict):
        result, count = {}, 0
        for key, item in value.items():
            result[key], n = redact(item)
            count += n
        return result, count
    if isinstance(value, list):
        result, count = [], 0
        for item in value:
            item, n = redact(item)
            result.append(item)
            count += n
        return result, count
    if isinstance(value, str) and LOCAL_PATH.search(value):
        return '[machine-local path redacted]', 1
    return value, 0


def prepare(source, output):
    if output.exists():
        raise FileExistsError('Output already exists')
    with source.open('rb') as stream:
        source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    with output.open('xb') as out, source.open('rb') as src:
        shutil.copyfileobj(src, out)
    count = 0
    with closing(sqlite3.connect(output)) as db:
        for table, record, payload in db.execute('SELECT table_name,record_id,payload FROM workbooks').fetchall():
            value, n = redact(json.loads(zlib.decompress(payload)))
            if n:
                db.execute('UPDATE workbooks SET payload=? WHERE table_name=? AND record_id=?',
                           (zlib.compress(json.dumps(value, ensure_ascii=False).encode('utf-8')), table, record))
                count += n
        for table, record, fields in db.execute('SELECT table_name,record_id,fields_json FROM entities').fetchall():
            value, n = redact(json.loads(fields))
            if n:
                db.execute('UPDATE entities SET fields_json=? WHERE table_name=? AND record_id=?',
                           (json.dumps(value, ensure_ascii=False), table, record))
                count += n
        db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)', [
            ('bundle_source_catalog_sha256', source_hash), ('bundle_redacted_local_paths', str(count))])
        db.commit()
        db.execute('VACUUM')  # Remove old content from freed SQLite pages.
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Bundled catalog failed integrity check')
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(f'Redacted {prepare(args.source, args.output)} machine-local paths')


if __name__ == '__main__':
    main()
