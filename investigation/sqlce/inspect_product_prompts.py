"""Read indexed product prompts without calculating or approving overrides."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import zlib


def inspect(catalog, record_id, requested):
    with closing(sqlite3.connect(catalog.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        entity=db.execute("SELECT name,link_id,category_link_id,library_link_id FROM entities WHERE table_name='Products' AND record_id=?",(record_id,)).fetchone()
        if entity is None: raise ValueError('Product record ID not found')
        row=db.execute("SELECT payload FROM workbooks WHERE table_name='Products' AND record_id=?",(record_id,)).fetchone()
        if row is None: raise ValueError('Product workbook not indexed')
        wb=json.loads(zlib.decompress(row['payload']))
        if wb.get('status')!='ok': raise ValueError('Product workbook index is not readable')
        prompts=[]
        for prompt in wb.get('prompt_rows',[]):
            for named in prompt.get('defined_names_for_value_cell',[]):
                cell=prompt.get('cells',{}).get('B',{})
                prompts.append({'name':named['name'],'row':prompt['row'],
                    'label':prompt.get('cells',{}).get('A',{}).get('value'),
                    'cached_value':cell.get('value'),'formula':cell.get('formula'),
                    'formula_status':cell.get('formula_status')})
        checks=[]
        for name in requested:
            matches=[p for p in prompts if p['name']==name]
            checks.append({'name':name,'status':'unique_name_match' if len(matches)==1 else ('missing' if not matches else 'ambiguous'), 'matches':len(matches)})
        return {'product':dict(entity),'record_id':record_id,'workbook_compression':wb.get('compression'),
                'workbook_sha256':wb.get('workbook_sha256'),'prompts':prompts,'requested_checks':checks,
                'warning':'Names and caches are research evidence only; no formula, constraint, compatibility or XML override has been validated.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('catalog',type=Path);p.add_argument('record_id');p.add_argument('--prompt',action='append',default=[]);a=p.parse_args()
    try: print(json.dumps(inspect(a.catalog,a.record_id,a.prompt),indent=2,ensure_ascii=False))
    except (OSError,ValueError,sqlite3.Error,zlib.error) as e:p.exit(2,f'Prompt inspection failed: {e}\n')
if __name__=='__main__':main()
