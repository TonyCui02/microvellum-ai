"""Resolve material candidates through indexed specification-group workbooks."""
import json
import zlib


ROLE_SHEETS = {
    'panel_finish': {'SheetStockLibrary', 'BuyOutLibrary'},
    'interior_carcass': {'SheetStockLibrary'},
    'toe_kick': {'SheetStockLibrary'},
    'benchtop': {'SheetStockLibrary', 'SolidStockLibrary', 'BuyOutLibrary'},
    'edging': {'EdgeBandLibrary'},
    'hardware': {'HardwareLibrary'},
}


def _workbook(db, table, link_id, purpose):
    if not link_id:
        raise ValueError(f"selected specification group has no {purpose} workbook")
    rows = db.execute(
        'SELECT record_id,compression,payload FROM workbooks WHERE table_name=? AND link_id=?',
        (table, link_id),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{purpose} workbook is missing or ambiguous in this catalog")
    if rows[0]['compression'] not in ('complete', 'direct_zip', 'incomplete_stream'):
        raise ValueError(f"{purpose} workbook has no usable index")
    data = json.loads(zlib.decompress(rows[0]['payload']))
    if data.get('status') != 'ok' or not isinstance(data.get('library_rows'), list):
        raise ValueError(f"{purpose} workbook lacks a complete material index; rebuild the catalog")
    return data


def material_scope(db, group_name, role, group_link_id=None):
    sheets = ROLE_SHEETS.get(role)
    if sheets is None:
        raise ValueError(f"{role!r} is not a material-catalog role")
    if group_link_id:
        groups = db.execute("SELECT fields_json FROM entities WHERE table_name='SpecificationGroups' AND link_id=?", (group_link_id,)).fetchall()
    else:
        groups = db.execute("SELECT fields_json FROM entities WHERE table_name='SpecificationGroups' AND name=? COLLATE BINARY", (group_name,)).fetchall()
    if len(groups) != 1:
        raise ValueError(f"specification group {group_name!r} is missing or ambiguous")
    group = json.loads(groups[0]['fields_json'])
    memberships = set()
    relevant = {s.casefold() for s in sheets}
    for table, link_key, purpose, wanted in (
        ('CutPartsFiles', 'LinkIDCutPartsFileName', 'cut-parts', {'sheetstocklibrary','solidstocklibrary','buyoutlibrary'}),
        ('EdgebandFiles', 'LinkIDEdgeBandFileName', 'edgeband', {'edgebandlibrary'}),
        ('HardwareFiles', 'LinkIDHardwareFileName', 'hardware', {'hardwarelibrary'}),
    ):
        if not relevant.intersection(wanted):
            continue
        wb = _workbook(db, table, group.get(link_key), purpose)
        for row in wb['library_rows']:
            if row.get('sheet') in sheets:
                # Library sheets store material LinkID in column E.
                link_id = row.get('cells', {}).get('E')
                if isinstance(link_id, str) and link_id:
                    memberships.add(link_id)

    assigned = set()
    pointer_specs = (
        ('CutPartsFiles', 'LinkIDCutPartsFileName', {'CutParts'}, 'D'),
        ('EdgebandFiles', 'LinkIDEdgeBandFileName', {'EdgebandParts'}, 'D'),
    )
    for table, key, tabs, column in pointer_specs:
        if not group.get(key):
            continue
        try:
            wb = _workbook(db, table, group.get(key), table)
        except ValueError:
            continue
        for row in wb['library_rows']:
            if row.get('sheet') in tabs:
                link_id = row.get('cells', {}).get(column)
                if isinstance(link_id, str) and link_id:
                    assigned.add(link_id)
    return memberships, assigned


def search_materials(db, group_name, role, term='', limit=50, offset=0, group_link_id=None):
    memberships, assigned = material_scope(db, group_name, role, group_link_id)
    if not memberships:
        raise ValueError(f"no {role} material records were indexed for group {group_name!r}")
    rows = db.execute(
        "SELECT record_id,link_id,name,category_link_id,library_link_id,project_link_id,fields_json "
        "FROM entities WHERE table_name='Materials' AND project_link_id IS NULL "
        "AND instr(lower(coalesce(name,'')),lower(?))>0 ORDER BY name COLLATE NOCASE,record_id",
        (term,),
    ).fetchall()
    result = []
    for row in rows:
        if row['link_id'] not in memberships:
            continue
        fields = json.loads(row['fields_json'])
        result.append({
            'record_id': row['record_id'], 'link_id': row['link_id'], 'name': row['name'],
            'category_link_id': row['category_link_id'], 'library_link_id': row['library_link_id'],
            'project_link_id': row['project_link_id'], 'type': fields.get('Type'),
            'thickness': fields.get('Thickness'), 'group_assigned': row['link_id'] in assigned,
        })
    total = len(result)
    return result[offset:offset + limit], total
