"""Entry point for the local, experimental drawing-review workflow."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_CATALOG = ROOT / 'data/catalog/kitchen-library-v7.1.sqlite'
CATALOG_MANIFEST = ROOT / 'data/catalog/manifest.json'
COMMANDS = {
    'check': 'scripts/check_setup.py',
    'render': 'src/mv_kitchen/render_drawing.py',
    'catalog': 'investigation/sqlce/catalog.py',
    'prompts': 'investigation/sqlce/inspect_product_prompts.py',
    'publish': 'poc/render_review.py',
    'serve': 'poc/server.py',
    'export': 'poc/export_experimental_xml.py',
}


def bundled_catalog():
    try:
        manifest = json.loads(CATALOG_MANIFEST.read_text(encoding='utf-8'))
        with BUNDLED_CATALOG.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if manifest['catalog'] != BUNDLED_CATALOG.name or actual != manifest['catalog_sha256']:
            raise ValueError('bundled catalog does not match its manifest')
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot use bundled catalog: {exc}') from exc
    return str(BUNDLED_CATALOG)


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print('Usage: python scripts/mv.py COMMAND [arguments]')
        print('Commands: ' + ', '.join(COMMANDS))
        print('Run a command with --help for its arguments. Export is experimental.')
        return 0
    command, *args = argv
    if command not in COMMANDS:
        print(f'Unknown command: {command}. Run with --help.', file=sys.stderr)
        return 2
    try:
        if command in ('serve', 'export') and not any(x == '--catalog' or x.startswith('--catalog=') for x in args):
            if not any(x in ('-h', '--help') for x in args):
                args = ['--catalog', bundled_catalog(), *args]
        elif command == 'catalog' and len(args) >= 2 and args[0] in ('search', 'search-prompts', 'product'):
            if args[1] not in ('-h', '--help') and not args[1].lower().endswith(('.sqlite', '.sqlite3', '.db')):
                args.insert(1, bundled_catalog())
        elif command == 'prompts' and args and args[0] not in ('-h', '--help'):
            if not args[0].lower().endswith(('.sqlite', '.sqlite3', '.db')):
                args.insert(0, bundled_catalog())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(ROOT / COMMANDS[command]), *args])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
