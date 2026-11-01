"""Check the minimal local prerequisites for the drawing-review prototype."""
import shutil
import sys
from mv import bundled_catalog


def main():
    python_ok = sys.version_info >= (3, 11)
    print(f'Python: {sys.version.split()[0]} ({"OK" if python_ok else "3.11 or newer required"})')
    pdf_tools = [shutil.which(name) for name in ('pdfinfo', 'pdftoppm')]
    for name, path in zip(('pdfinfo', 'pdftoppm'), pdf_tools):
        print(f'{name}: {path or "missing (required for PDF drawings)"}')
    print('Python packages: none required for the prototype')
    try:
        print(f'Bundled catalog: {bundled_catalog()} (checksum OK)')
        catalog_ok = True
    except ValueError as exc:
        print(f'Bundled catalog: {exc}')
        catalog_ok = False
    print(f'Node.js: {shutil.which("node") or "not installed (only needed for JavaScript tests)"}')
    return 0 if python_ok and all(pdf_tools) and catalog_ok else 1


if __name__ == '__main__':
    sys.exit(main())
