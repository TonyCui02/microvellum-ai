"""Render and publish the review page for a source-of-truth kitchen JSON."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path, help='Kitchen review JSON; source.path points to its PDF or PNG')
    parser.add_argument('--dpi', type=int, default=200, help='PDF render resolution (default: 200)')
    args = parser.parse_args()
    try:
        model = args.model.resolve(strict=True)
        data = json.loads(model.read_text(encoding='utf-8-sig'))
        source = data.get('source', {})
        source_path = source.get('path')
        if not isinstance(source_path, str) or not source_path.strip():
            parser.error('Add source.path to the JSON. It may be absolute or relative to the JSON file.')
        drawing = Path(source_path)
        if not drawing.is_absolute():
            drawing = model.parent / drawing
        drawing = drawing.resolve(strict=True)
        if source.get('file') != drawing.name:
            parser.error('source.file must match the filename at source.path.')

        with tempfile.TemporaryDirectory(prefix='mv-review-') as temp:
            render_dir = Path(temp) / 'rendered-pages'
            subprocess.run([
                sys.executable, str(WORKSPACE / 'src/mv_kitchen/render_drawing.py'),
                str(drawing), '--out', str(render_dir), '--dpi', str(args.dpi),
            ], check=True)
            subprocess.run([
                sys.executable, str(ROOT / 'publish_review.py'), '--model', str(model),
                '--source', str(drawing), '--render-manifest', str(render_dir / 'manifest.json'),
            ], check=True)
        print('Review page is ready. Run `py poc/server.py` and open http://127.0.0.1:8000/.')
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        parser.exit(2, f'Review rendering failed: {exc}\n')


if __name__ == '__main__':
    main()
