"""Validate the limited wall-relative model supported by the review views."""
import math


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def validate_model(model, allow_unresolved=False, require_visual_self_check=False):
    errors=[]
    if not isinstance(model,dict): return ['Model must be a JSON object.']
    project=model.get('project',{}); room=model.get('room',{}); review=model.get('review',{})
    if not isinstance(project,dict) or not isinstance(room,dict) or not isinstance(review,dict): return ['Project, room and review must be objects.']
    if not project.get('name') or project.get('units') not in ('mm','in'): errors.append('Project name and units (mm or in) are required.')
    source=model.get('source',{}); visual_review=source.get('visual_review',{}) if isinstance(source,dict) else {}; pages=visual_review.get('inspected_pages',[]) if isinstance(visual_review,dict) else []
    if not isinstance(pages,list) or not pages or not all(isinstance(n,int) and not isinstance(n,bool) and n>0 for n in pages) or len(set(pages))!=len(pages): errors.append('Record the inspected PNG page numbers in source.visual_review.inspected_pages.')
    if not isinstance(review.get('issues'),list) or not all(isinstance(x,str) for x in review.get('issues',[])): errors.append('Review issues must be a list of strings.')
    visual_check=review.get('visual_self_check')
    if require_visual_self_check and not isinstance(visual_check,dict):
        errors.append('Record the agent visual self-check for front, top and 3D before publishing for human review.')
    elif isinstance(visual_check,dict):
        for view in ('front','top','3d'):
            result=visual_check.get(view)
            if not isinstance(result,dict):
                errors.append(f'Agent visual self-check needs a {view} result.')
            elif result.get('status') in ('matches_source','consistent_with_orthographic_views'):
                pages=result.get('source_pages'); evidence=result.get('evidence')
                if not isinstance(pages,list) or not pages or not all(isinstance(n,int) and not isinstance(n,bool) and n>0 for n in pages):
                    errors.append(f'Agent {view} self-check needs source page numbers.')
                elif not isinstance(visual_review.get('inspected_pages'),list) or any(n not in visual_review['inspected_pages'] for n in pages):
                    errors.append(f'Agent {view} self-check must cite pages recorded as visually inspected.')
                if not isinstance(evidence,str) or not evidence.strip():
                    errors.append(f'Agent {view} self-check needs a comparison note.')
            elif result.get('status')=='no_comparable_source_view':
                if not isinstance(result.get('reason'),str) or not result['reason'].strip():
                    errors.append(f'Agent {view} self-check needs a reason when no comparable source view exists.')
            else:
                errors.append(f'Agent {view} self-check must pass or explain that no comparable source view exists.')
    walls=room.get('walls',[]);products=room.get('products');features=room.get('features',[]);inventory=room.get('drawing_inventory')
    if not isinstance(walls,list): return errors+['Walls must be a list; an empty list is valid.']
    if not isinstance(products,list) or not isinstance(features,list): return errors+['Products and features must be lists.']
    if not isinstance(inventory,list) or not inventory: errors.append('Record every visually identified drawing entity in room.drawing_inventory.')
    if room.get('openings'): errors.append('Openings are not represented in the review views; remove them only after recording them as an unresolved issue.')
    lengths={}
    for i,w in enumerate(walls):
        if not isinstance(w,dict) or not isinstance(w.get('id'),str) or not w['id'] or w['id'] in lengths:
            errors.append(f'Wall {i+1} has an invalid or duplicate ID.');continue
        pts=[w.get('start'),w.get('end')]
        if not all(isinstance(p,list) and len(p)==2 and all(number(v) for v in p) for p in pts):
            errors.append(f"Wall {w['id']} needs finite start and end coordinates.");continue
        length=math.hypot(pts[1][0]-pts[0][0],pts[1][1]-pts[0][1])
        if length<=0 or not number(w.get('height')) or w['height']<=0 or not number(w.get('thickness')) or w['thickness']<=0:
            errors.append(f"Wall {w['id']} needs positive length, height and thickness.");continue
        lengths[w['id']]=length
    seen=set()
    for i,item in enumerate(products+features):
        if not isinstance(item,dict): errors.append(f'Item {i+1} must be an object.');continue
        ident=item.get('id');wall=item.get('wall_id')
        if not isinstance(ident,str) or not ident or ident in seen: errors.append(f'Item {i+1} has an invalid or duplicate ID.');continue
        seen.add(ident)
        quantity = item.get('quantity', 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 999999999:
            errors.append(f'Item {ident} quantity must be a positive integer.')
        if wall is not None and wall not in lengths: errors.append(f'Item {ident} references an invalid wall.');continue
        if not isinstance(item.get('label'),str) or not item['label']:errors.append(f'Item {ident} needs a label.')
        if not number(item.get('offset')) or not number(item.get('elevation')) or item['elevation']<0:
            errors.append(f'Item {ident} needs finite offset and nonnegative elevation.');continue
        if item.get('rotation',0)!=0:errors.append(f'Item {ident}: rotated items are not supported by the 2D views.')
        if item in features and item.get('kind')!='rail':errors.append(f'Item {ident}: this feature type is not supported.')
        if 'visual_depth_offset' in item and (not number(item['visual_depth_offset']) or item['visual_depth_offset']<0): errors.append(f'Item {ident} needs a valid visual depth offset.')
        dimensions=item.get('dimensions');visual=item.get('visual_dimensions',{})
        if not isinstance(dimensions,dict) or not isinstance(visual,dict):errors.append(f'Item {ident} needs dimensions.');continue
        sizes={}
        for key in ('width','height','depth'):
            v=dimensions.get(key)
            if v is not None and (not number(v) or v<=0): errors.append(f'Item {ident} has an invalid {key}.');continue
            resolved=v if v is not None else visual.get(key)
            if not number(resolved) or resolved<=0: errors.append(f'Item {ident} needs a positive visual placeholder for unknown {key}.');continue
            sizes[key]=resolved
        if wall is not None and 'width' in sizes and (item['offset']<0 or item['offset']+sizes['width']>lengths[wall]+1e-7): errors.append(f'Item {ident} extends beyond wall {wall}; this placement is not supported.')
        evidence=item.get('dimension_evidence',{})
        for key in ('width','height','depth'):
            ev=evidence.get(key) if isinstance(evidence,dict) else None
            if not isinstance(ev,dict) or ev.get('status') not in ('observed','derived','confirmed','unknown') or not isinstance(ev.get('description'),str) or not ev['description']:
                errors.append(f'Item {ident} needs evidence and status for {key}.');continue
            if (dimensions.get(key) is None) != (ev['status']=='unknown'): errors.append(f'Item {ident} {key} value and evidence status disagree.')
            if ev['status'] in ('observed','derived') and ev.get('page') not in pages: errors.append(f'Item {ident} {key} needs an inspected source page.')
        if not isinstance(item.get('evidence',[]),list) or not isinstance(item.get('prompt_overrides',[]),list) or not all(isinstance(v,dict) and isinstance(v.get('name'),str) for v in item.get('prompt_overrides',[])):errors.append(f'Item {ident} needs evidence and named prompt lists.')
    if isinstance(inventory,list):
        seen_inventory=set(); mapped={}
        model_ids={str(x.get('id')) for x in products+features if isinstance(x,dict)}
        for idx,entry in enumerate(inventory):
            if not isinstance(entry,dict): errors.append(f'Drawing inventory entry {idx+1} must be an object.');continue
            ident=entry.get('id');resolution=entry.get('resolution');kind=entry.get('kind')
            if not isinstance(ident,str) or not ident or ident in seen_inventory: errors.append(f'Drawing inventory entry {idx+1} has an invalid or duplicate ID.');continue
            seen_inventory.add(ident)
            if not isinstance(entry.get('label'),str) or not entry['label'] or not isinstance(entry.get('evidence'),list) or not entry['evidence']:
                errors.append(f'Drawing inventory {ident} needs a label and source evidence.')
            if not isinstance(kind,str) or kind not in ('cabinet','part','appliance','countertop','hardware','rail','wall','annotation','other'):
                errors.append(f'Drawing inventory {ident} has an unsupported kind.')
            if resolution not in ('model_product','model_feature','explicit_exclusion','unresolved'):
                errors.append(f'Drawing inventory {ident} needs a resolution.')
            if resolution in ('model_product','model_feature'):
                model_id=entry.get('model_id')
                if model_id not in model_ids: errors.append(f'Drawing inventory {ident} references a missing model item.')
                mapped.setdefault(model_id,[]).append(ident)
            elif resolution=='explicit_exclusion' and (not isinstance(entry.get('reason'),str) or not entry['reason'].strip()):
                errors.append(f'Drawing inventory {ident} needs a reason for exclusion.')
            elif resolution=='unresolved' and not allow_unresolved:
                errors.append(f'Drawing inventory {ident} is unresolved; export is blocked.')
        for model_id in model_ids:
            count=len(mapped.get(model_id,[]))
            if count!=1: errors.append(f'Model item {model_id} must map to exactly one drawing inventory entry (found {count}).')
    return errors
