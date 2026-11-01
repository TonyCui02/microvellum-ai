"""Render every PDF page to PNG for a visual drawing review; never change the input."""
import argparse
import hashlib
import json
import re
import shutil
import struct
import zlib
import subprocess
from pathlib import Path


def sha256(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def png_size(path):
    with path.open('rb') as f:
        if f.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError(f'Invalid PNG signature: {path.name}')
        width=height=None
        has_data=False
        chunk_index=0
        while True:
            header=f.read(8)
            if len(header)!=8:
                raise ValueError(f'Incomplete PNG: {path.name}')
            length,kind=struct.unpack('>I4s',header)
            if length>128*1024*1024:
                raise ValueError('PNG chunk exceeds size limit')
            data=f.read(length); crc=f.read(4)
            if len(data)!=length or len(crc)!=4 or zlib.crc32(kind+data)!=struct.unpack('>I',crc)[0]:
                raise ValueError(f'PNG chunk failed integrity check: {path.name}')
            if chunk_index==0:
                if kind!=b'IHDR' or length!=13: raise ValueError('Invalid PNG header')
                width,height=struct.unpack('>II',data[:8])
                if not width or not height: raise ValueError('PNG has an invalid size')
            if kind==b'IDAT':has_data=True
            if kind==b'IEND':
                if length or not has_data or f.read(1):raise ValueError('Invalid PNG ending')
                return width,height
            chunk_index+=1


def render(source, output, dpi=200):
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in ('.pdf', '.png'):
        raise ValueError('Source must be an existing PDF or PNG')
    if not 72 <= dpi <= 600:
        raise ValueError('DPI must be between 72 and 600')
    if output.exists():
        raise ValueError('Output directory already exists; choose a new directory')
    source_hash = sha256(source)
    info_text = None
    if source.suffix.lower() == '.pdf':
        if not shutil.which('pdfinfo') or not shutil.which('pdftoppm'):
            raise ValueError('pdfinfo and pdftoppm are required on PATH')
        info = subprocess.run(['pdfinfo', str(source)], check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        info_text = info.stdout
        match = re.search(r'^Pages:\s*(\d+)\s*$', info_text, re.M)
        if not match or int(match.group(1)) < 1:
            raise ValueError('Could not determine PDF page count')
        expected = int(match.group(1))
    else:
        png_size(source)
        expected = 1
    output.mkdir(parents=True, exist_ok=False)
    if source.suffix.lower() == '.pdf':
        subprocess.run(['pdftoppm', '-png', '-r', str(dpi), str(source), str(output / 'page')], check=True, capture_output=True)
        generated = list(output.glob('page-*.png'))
        def page_number(path):
            match = re.fullmatch(r'page-(\d+)\.png', path.name)
            if not match:
                raise ValueError('Unexpected PNG filename')
            return int(match.group(1))
        generated.sort(key=page_number)
        if [page_number(x) for x in generated] != list(range(1, expected + 1)):
            raise ValueError('Rendered pages do not match the PDF page count')
    else:
        target = output / 'page-1.png'
        shutil.copyfile(source, target)
        generated = [target]
    pages=[]
    for number, path in enumerate(generated, 1):
        width, height = png_size(path)
        pages.append({'page':number, 'file':path.name, 'width_px':width, 'height_px':height, 'sha256':sha256(path)})
    if sha256(source) != source_hash:
        raise ValueError('Source changed during rendering; discard this output')
    manifest={'source_name':source.name,'source_sha256':source_hash,'source_format':source.suffix.lower()[1:],
              'dpi':dpi if source.suffix.lower()=='.pdf' else None,'page_count':expected,'pages':pages,
              'review_status':'not_visually_reviewed'}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n',encoding='utf-8')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--dpi',type=int,default=200)
    args=parser.parse_args()
    try:
        print(json.dumps(render(args.source,args.out,args.dpi),indent=2))
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        parser.exit(2,f'Rendering failed: {exc}\n')

if __name__=='__main__': main()
