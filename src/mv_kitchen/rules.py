"""Persistent shop rules and review-model evaluation checks."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

DEFAULT_PATH = Path(os.environ.get('MV_RULEBOOK', Path.home() / '.microvellum-ai' / 'rules.json'))
DEFAULT_RULES = Path(__file__).resolve().parents[2] / 'data/default-rules.json'


def fingerprint(book):
    return hashlib.sha256(json.dumps(book, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def validate_book(book):
    if not isinstance(book, dict) or book.get('schema_version') != 1 or not isinstance(book.get('rules'), list):
        raise ValueError('Invalid rulebook schema.')
    if not isinstance(book.get('history', []), list):
        raise ValueError('Rule history must be a list.')
    ids = set()
    for rule in book['rules']:
        if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', rule['id']) or rule['id'] in ids:
            raise ValueError('Rule IDs must be unique lowercase hyphenated identifiers.')
        ids.add(rule['id'])
        if type(rule.get('version')) is not int or rule['version'] < 1 or type(rule.get('enabled')) is not bool:
            raise ValueError('Rule version or enabled status is invalid.')
        if not all(isinstance(rule.get(k), str) and rule[k].strip() for k in ('title', 'instruction', 'scope', 'source')):
            raise ValueError('Each rule needs a title, instruction, scope and source.')
    return book


def load(path):
    defaults = validate_book(json.loads(DEFAULT_RULES.read_text(encoding='utf-8-sig')))
    if not path.exists():
        return copy.deepcopy(defaults)
    book = validate_book(json.loads(path.read_text(encoding='utf-8-sig')))
    existing = {rule['id'] for rule in book['rules']}
    book['rules'] = copy.deepcopy([rule for rule in defaults['rules'] if rule['id'] not in existing]) + book['rules']
    return book


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
            tmp = Path(f.name)
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(tmp, path)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()


def snapshot(book):
    enabled = [dict(rule) for rule in book['rules'] if rule['enabled']]
    return {'rulebook': book, 'sha256': fingerprint(book), 'evaluations': [
        {'id': r['id'], 'version': r['version'], 'status': 'pending', 'reason': '', 'affected_items': []} for r in enabled]}


def check(record, current, use_saved=False):
    if not isinstance(record, dict):
        raise ValueError('Missing review.rules snapshot; run rules snapshot and evaluate every enabled rule.')
    book = validate_book(record.get('rulebook'))
    digest = fingerprint(book)
    if record.get('sha256') != digest:
        raise ValueError('Rulebook snapshot fingerprint does not match its contents.')
    if not use_saved and digest != fingerprint(current):
        raise ValueError('Rulebook changed since the job snapshot; re-evaluate or explicitly use --use-saved-rules.')
    evaluations = record.get('evaluations')
    if not isinstance(evaluations, list):
        raise ValueError('Rule evaluations must be a list.')
    expected = {r['id']: r for r in book['rules'] if r['enabled']}
    seen = set()
    for entry in evaluations:
        if not isinstance(entry, dict) or entry.get('id') not in expected or entry['id'] in seen:
            raise ValueError('Rule evaluations contain an unexpected or duplicate ID.')
        ident = entry['id']; seen.add(ident)
        if type(entry.get('version')) is not int or entry['version'] != expected[ident]['version']:
            raise ValueError(f'Rule {ident} evaluation version does not match.')
        if entry.get('status') not in ('applied', 'not_applicable'):
            raise ValueError(f'Rule {ident} is unresolved; export is blocked.')
        if not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
            raise ValueError(f'Rule {ident} needs an evaluation reason and evidence.')
        if not isinstance(entry.get('affected_items'), list) or not all(isinstance(x, str) and x.strip() for x in entry['affected_items']):
            raise ValueError(f'Rule {ident} affected_items must be a list of IDs.')
    if seen != set(expected):
        raise ValueError('Every enabled rule must have an evaluation.')
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rulebook', type=Path, default=DEFAULT_PATH, help='private rulebook (or set MV_RULEBOOK)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    for command in ('add', 'update'):
        p = sub.add_parser(command); p.add_argument('id')
        for field in ('title', 'instruction', 'scope', 'source'):
            p.add_argument('--' + field, required=command == 'add')
    for command in ('disable', 'enable'):
        sub.add_parser(command).add_argument('id')
    p = sub.add_parser('snapshot'); p.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    try:
        book = load(args.rulebook)
        if args.command == 'list':
            print(json.dumps({'path': str(args.rulebook), 'sha256': fingerprint(book), **book}, indent=2, ensure_ascii=False)); return
        if args.command == 'snapshot':
            model = json.loads(args.model.read_text(encoding='utf-8-sig'))
            model['review']['rules'] = snapshot(book)
            model['project']['revision'] = model['project'].get('revision', 0) + 1
            for key in ('approved_by', 'approved_at', 'approved_revision'):
                model['review'].pop(key, None)
            write(args.model, model)
            print('Snapshot saved; evaluate every enabled rule and obtain a new review approval.'); return
        existing = next((r for r in book['rules'] if r['id'] == args.id), None)
        if args.command == 'add':
            if existing: raise ValueError('Rule ID already exists.')
            book['rules'].append({'id': args.id, 'version': 1, 'enabled': True, **{k: getattr(args, k) for k in ('title', 'instruction', 'scope', 'source')}})
        else:
            if not existing: raise ValueError('Rule ID not found.')
            previous = copy.deepcopy(existing)
            if args.command == 'update':
                changes = {k: getattr(args, k) for k in ('title', 'instruction', 'scope', 'source') if getattr(args, k) is not None}
                if not changes: raise ValueError('Provide at least one field to update.')
                existing.update(changes)
            else:
                existing['enabled'] = args.command == 'enable'
            existing['version'] += 1
            book.setdefault('history', []).append(previous)
        validate_book(book); write(args.rulebook, book)
        print(json.dumps(book, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'Rules error: {exc}\n')


if __name__ == '__main__':
    main()
