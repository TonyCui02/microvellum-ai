"""Publish a reviewed JSON model and its source drawing to the local review page."""
import argparse
import hashlib
import json
import os
from model_validation import validate_model
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--source', '--pdf', dest='pdf', type=Path, required=True, help='Source PDF or PNG')
    parser.add_argument('--render-manifest',type=Path,required=True,help='Manifest from render_drawing.py')
    args = parser.parse_args()
    model, pdf = args.model.resolve(strict=True), args.pdf.resolve(strict=True)
    if model.suffix.lower() != '.json' or pdf.suffix.lower() not in ('.pdf', '.png') or not model.is_file() or not pdf.is_file():
        parser.error('Provide an existing JSON model and source PDF or PNG.')
    data = json.loads(model.read_text(encoding='utf-8-sig'))
    # Publishing a review draft may include unresolved drawing entities;
    # resolving them is the purpose of the review. Export validation stays strict.
    errors = validate_model(data, allow_unresolved=True, require_visual_self_check=True)
    if errors:
        parser.error('Cannot publish review: ' + ' '.join(errors))
    if data.get('source', {}).get('file') != pdf.name:
        parser.error('Model source.file must match the supplied source filename.')
    manifest_path=args.render_manifest.resolve(strict=True)
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    source_hash=digest(pdf)
    if manifest.get('source_sha256')!=source_hash:
        parser.error('Rendered pages do not match the source hash.')
    pages=manifest.get('pages',[])
    if not isinstance(pages,list) or not pages or len(pages)!=manifest.get('page_count') or [x.get('page') for x in pages]!=list(range(1,len(pages)+1)):
        parser.error('Render manifest has missing or out-of-order pages.')
    for page in pages:
        filename=page.get('file','')
        if Path(filename).name!=filename or digest(manifest_path.parent / filename)!=page.get('sha256'):
            parser.error('Rendered page hash does not match the manifest.')
    review=data.get('source',{}).get('visual_review',{})
    if review.get('source_sha256')!=source_hash or sorted(review.get('inspected_pages',[]))!=list(range(1,len(pages)+1)):
        parser.error('Record visual inspection of every rendered source page before publishing.')
    source = {'model': str(model), 'pdf': str(pdf), 'pdf_sha256': source_hash, 'page_count': len(pages)}
    temp = ROOT / '.active-review.tmp'
    temp.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, ROOT / '.active-review.json')
    print('Published review:', data.get('project', {}).get('name', model.name))

if __name__ == '__main__':
    main()
