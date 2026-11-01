"""Recover one workbook from a SQLite analysis copy; never changes the database."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import zipfile
import zlib

TABLES = ('Products', 'Subassemblies', 'DoorWizardFiles', 'GlobalFiles',
          'HardwareFiles', 'CutPartsFiles', 'EdgebandFiles', 'ProjectWizardFiles')
LIMIT = 64 * 1024 * 1024


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('database', type=Path)
    p.add_argument('table', choices=TABLES)
    p.add_argument('link_id', help='Exact LinkID of the source record')
    p.add_argument('output', type=Path, help='New .xlsx output path')
    a = p.parse_args()
    if a.output.exists():
        p.error('output already exists')
    uri = a.database.resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(f'SELECT Name, WorkBook FROM "{a.table}" WHERE LinkID = ?', (a.link_id,)).fetchall()
    if len(rows) != 1 or not rows[0][1]:
        p.error(f'expected one record with a workbook, found {len(rows)}')
    name, blob = rows[0]
    obj = zlib.decompressobj()
    data = obj.decompress(blob, LIMIT + 1)
    if len(data) > LIMIT or obj.unconsumed_tail:
        p.error('decompressed payload exceeds safety limit')
    if obj.unused_data:
        p.error('unexpected data after compressed stream')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > LIMIT:
            p.error('unpacked ZIP exceeds safety limit')
        if 'xl/workbook.xml' not in archive.namelist() or archive.testzip() is not None:
            p.error('not a valid workbook ZIP or ZIP CRC failed')
    with a.output.open('xb') as f:
        f.write(data)
    print(json.dumps({'table': a.table, 'link_id': a.link_id, 'name': name,
                      'source_blob_sha256': hashlib.sha256(blob).hexdigest(),
                      'workbook_sha256': hashlib.sha256(data).hexdigest(),
                      'zlib_end_marker_present': obj.eof,
                      'zip_member_crc_check': 'ok', 'output': str(a.output.resolve())}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, sqlite3.Error, zlib.error, zipfile.BadZipFile) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
