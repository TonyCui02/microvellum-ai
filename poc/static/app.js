import { applyItemDraft, cloneItem, hasItemChanges, selectCatalogProduct } from './edit_state.mjs';
const $ = selector => document.querySelector(selector);
const ns = 'http://www.w3.org/2000/svg';
let currentRevision = null, currentModelKey = null, model = null, selectedId = null, pdfDocument = null, pdfPage = 1, busy = false, displayOnly = false;
const editDrafts = new Map();
let saving = false, saveQueue = Promise.resolve();
let viewMode='front', topLayer='base', selectedWallId=null, sourceFormat='pdf', THREE, threeRenderer, threeScene, threeCamera, threeControls, threeMeshes=[], threeInit, threeObserver;
const text = value => value == null ? 'Not specified' : String(value);
// Null means unknown. Visual dimensions are placeholders used only to draw boxes.
const sizeOf=(p,key)=>Number.isFinite(p.dimensions?.[key])?p.dimensions[key]:(p.visual_dimensions?.[key]??0);
const dimensionText=(p,key)=>Number.isFinite(p.dimensions?.[key])?`${p.dimensions[key]}`:'??';

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(ns, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

function dimensionLine(svg, horizontal, start, end, cross, from, label) {
  const cls='cad-dimension';const line=(x1,y1,x2,y2)=>svg.append(svgElement('line',{x1,y1,x2,y2,class:cls}));
  if(horizontal){line(start,from,start,cross-20);line(end,from,end,cross-20);line(start,cross,end,cross);line(start-22,cross+22,start+22,cross-22);line(end-22,cross+22,end+22,cross-22);
    const t=svgElement('text',{x:(start+end)/2,y:cross-18,class:'cad-label'});t.textContent=label;svg.append(t);
  }else{line(from,start,cross-20,start);line(from,end,cross-20,end);line(cross,start,cross,end);line(cross-22,start+22,cross+22,start-22);line(cross-22,end+22,cross+22,end-22);
    const t=svgElement('text',{x:cross-20,y:(start+end)/2,class:'cad-label',transform:`rotate(-90 ${cross-20} ${(start+end)/2})`});t.textContent=label;svg.append(t);
  }
}
function drawLayout() {
  const svg=$('#diagram');svg.replaceChildren();const products=model.room.products.filter(p=>p.wall_id===selectedWallId);const features=(model.room.features||[]).filter(p=>p.wall_id===selectedWallId);
  const items=$('#items');items.replaceChildren();
  for(const product of [...products,...features]){const button=document.createElement('button');button.textContent=product.label;button.className=selectedId===product.id?'active':'';button.onclick=()=>selectProduct(product);items.append(button)}
  const wall=model.room.walls?.find(w=>w.id===selectedWallId);const width=wall?Math.hypot(wall.end[0]-wall.start[0],wall.end[1]-wall.start[1]):2000;
  const top=viewMode==='top';const maximumHeight=top?Math.max(650,...products.map(p=>sizeOf(p,'depth'))):Math.max(2300,...products.map(p=>(p.elevation||0)+sizeOf(p,'height')));
  svg.setAttribute('viewBox',`-290 -300 ${Math.max(width,1000)+580} ${maximumHeight+(top?650:650)}`);svg.setAttribute('preserveAspectRatio','xMidYMid meet');
  const baseline=top?0:maximumHeight;svg.append(svgElement('line',{x1:0,y1:baseline,x2:width,y2:baseline,stroke:'#8ea899','stroke-width':12}));
  if(top){const wallText=svgElement('text',{x:width/2,y:-165,class:'wall-caption'});wallText.textContent='WALL';svg.append(wallText)}
  const shown=top?products.filter(p=>topLayer==='upper'?(p.elevation||0)>0:(p.elevation||0)<=0):products;
  shown.forEach((product,index)=>{
    if(!Number.isFinite(product.offset)||!Number.isFinite(product.elevation))return;
    const w=sizeOf(product,'width'),h=top?sizeOf(product,'depth'):sizeOf(product,'height');if(!(w>0&&h>0))return;
    const x=product.offset,y=top?0:maximumHeight-product.elevation-h;
    const group=svgElement('g',{class:'item'+(selectedId===product.id?' active':''),tabindex:'0',role:'button','aria-label':product.label});
    group.append(svgElement('rect',{x,y,width:w,height:h,rx:4}));
    const id=svgElement('text',{x:x+w/2,y:y+h/2+20,class:'svg-id'});id.textContent=product.id;group.append(id);
    const select=()=>selectProduct(product);group.addEventListener('click',select);group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select()}});svg.append(group);
    const horizontal=top?y+h+105:((product.elevation||0)>0?y-105:y+h+105);
    dimensionLine(svg,true,x,x+w,horizontal,top?y+h:((product.elevation||0)>0?y:y+h),dimensionText(product,'width'));
    if(index===0 || index===shown.length-1){const side=index===0?x-115:x+w+115;dimensionLine(svg,false,y,y+h,side,index===0?x:x+w,dimensionText(product,top?'depth':'height'))}
  });
  for(const feature of features){if(feature.kind!=='rail'||(top&&topLayer!=='upper'))continue;
    const x=feature.offset, length=sizeOf(feature,'width');if(!Number.isFinite(x)||!length)continue;
    const y=top?(feature.visual_depth_offset||150):maximumHeight-(feature.elevation||0);
    const group=svgElement('g',{class:'rail-item'+(selectedId===feature.id?' active':''),tabindex:'0',role:'button','aria-label':feature.label});
    group.append(svgElement('line',{x1:x,y1:y,x2:x+length,y2:y}));const label=svgElement('text',{x:x+length/2,y:y-35,class:'rail-label'});label.textContent='R1';group.append(label);
    group.addEventListener('click',()=>selectProduct(feature));group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectProduct(feature)}});svg.append(group);
  }
  svg.setAttribute('aria-label',top?`Top view, ${topLayer} cabinets. Dimensions in ${model.project.units}.`:`Front view. Dimensions in ${model.project.units}.`);
}

function selectProduct(product) {
  selectedId=product.id;selectedWallId=product.wall_id;$('#wallSelect').value=selectedWallId;if(viewMode==='top'){const layer=(product.elevation||0)>0?'upper':'base';if(layer!==topLayer){topLayer=layer;for(const [id,active] of [['#baseLayer',layer==='base'],['#upperLayer',layer==='upper']]){const b=$(id);b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))}}}drawLayout();showProduct(editDrafts.get(product.id)||product);highlight3d();
}
function node(tag, className, content) {
  const element=document.createElement(tag);if(className)element.className=className;
  if(content!=null)element.textContent=content;return element;
}
function showProduct(product) {
  if(displayOnly){
    const target=$('#selected');target.replaceChildren();
    const head=node('div','detail-header');head.append(node('strong','',product.label),node('span','detail-pill','Normalized JSON'));target.append(head);
    const body=node('div','detail-body');body.append(node('div','detail-callout','Edit width, height, or depth below. Save writes those three values back to the selected JSON. The drawing overlay uses X as a relative offset and Z as elevation for preview only; coordinate meanings remain unverified.'));
    const dimensions=node('div','dimensions'),fields={};for(const [key,label] of [['width','Width'],['height','Height'],['depth','Depth']]){const box=node('label','edit-dimension');box.append(node('span','',`${label} (${model.project.units})`));const input=node('input','edit-field');input.type='number';input.min='0.01';input.step='any';input.value=product.dimensions?.[key]??'';input.setAttribute('aria-label',label);fields[key]=input;box.append(input);dimensions.append(box)}body.append(dimensions);
    body.append(node('div','detail-value',`Library candidate: ${product.library_product?.name||'Not specified'}${product.library_product?.record_id?` · record ${product.library_product.record_id}`:''}`));target.append(body);
    const actions=node('div','edit-actions'),save=node('button','edit-save','Save dimensions');save.type='button';save.disabled=true;
    const changed=()=>{save.disabled=Object.entries(fields).every(([key,input])=>Number(input.value)===Number(product.dimensions[key]))||Object.values(fields).some(input=>!input.value||!Number.isFinite(input.valueAsNumber)||input.valueAsNumber<=0)};
    Object.values(fields).forEach(input=>input.addEventListener('input',changed));save.onclick=()=>saveNormalizedDimensions(product.id,fields,save);actions.append(save);target.append(actions);return;
  }
  const persisted=findItem(product.id), dirty=hasItemChanges(product,persisted);
  const target=$('#selected');target.replaceChildren();
  const head=node('div','detail-header');head.append(node('strong','',product.label),node('span','detail-pill','Draft'));target.append(head);
  const body=node('div','detail-body');
  const size=node('div','detail-section');size.append(node('h3','','Size shown in the view'));
  const dimensions=node('div','dimensions');
  for(const [key,label] of [['width','Width'],['height','Height'],['depth','Depth']]) {
    const box=node('label','dimension edit-dimension');box.append(node('span','',`${label} (${model.project.units})`));
    const field=node('input','edit-field');field.type='number';field.min='0';field.step='any';field.placeholder='Unknown';field.value=product.dimensions?.[key]??'';field.setAttribute('aria-label',label);field.onchange=()=>editDimension(product.id,key,field.value);box.append(field);
    const note=product.review_details?.dimensions?.[key];if(note&&!hasCanonicalText(note))box.append(node('small','',note));dimensions.append(box);
  }
  size.append(dimensions);if(product.kind==='rail')size.querySelector('h3').textContent='Rail size';body.append(size);
  const productSection=node('div','detail-section');productSection.append(node('h3','',product.kind==='rail'?'Library material':'Library product'));
  const productPicker=makeCatalogPicker('products','Search products by name…',product.library_product?.name||'',choice=>editItem(product.id,p=>selectCatalogProduct(p,choice)));productSection.append(productPicker);
  const productRecord=product.library_product?.record_id||product.library_product?.link_id;
  productSection.append(node('small','material-source',productRecord?`SDF library record ${product.library_product?.link_id||productRecord}`:'No catalog record selected yet.'));
  body.append(productSection);
  const materialSection=node('div','detail-section');materialSection.append(node('h3','','Materials & finish'));
  const material=product.material;
  const materialNames={panel_finish:'Panels & fronts',interior_carcass:'Interior carcass',edging:'Edging',toe_kick:'Toe kick',benchtop:'Benchtop',handle:'Handle / profile'};
  const materialMap=material&&typeof material==='object'&&!Array.isArray(material)?material:{panel_finish:typeof material==='string'?material:''};
  const grid=node('div','material-editor');
  const productGroup=product.specification_group||product.library_product?.specification_group||model.project.specification_group||'Kitchen Library';
  const groupInfo=typeof productGroup==='object'?productGroup:{name:productGroup};
  for(const name of Object.keys(materialMap)){
    const row=node('div','material-edit-row');row.append(node('span','material-edit-label',materialNames[name]||name.replaceAll('_',' ')));
    row.append(node('small','material-drawing-spec',`Drawing: ${materialMap[name]||'Not specified'}`));
    if(name==='handle'){row.append(node('small','material-source','Construction note; no material catalog mapping.'));grid.append(row);continue}
    const selected=product.material_catalog?.[name];
    const picker=makeCatalogPicker('materials','Search library materials…',selected?.name||'',choice=>editItem(product.id,p=>{p.material_catalog??={};p.material_catalog[name]={record_id:choice.record_id,link_id:choice.link_id,name:choice.name,catalog_sha256:choice.catalog_sha256,source_sqlite_sha256:choice.source_sqlite_sha256,group:choice.group,group_link_id:choice.group_link_id,role:name,group_assigned:choice.group_assigned}}),false,{group:groupInfo.name,group_link_id:groupInfo.link_id||'',role:name});
    row.append(picker);const matchNote=product.material_match_notes?.[name];const pointerNote=selected?(selected.group_assigned?'The group workbook has a pointer to this material, but component use is unverified.':'This record is available in the group workbook, but no pointer assignment is recorded.') : '';row.append(node('small','material-source',selected?.record_id?`Catalog candidate: ${selected.name||selected.record_id}. ${matchNote||'Candidate equivalence still needs review.'} ${pointerNote} The current XML exporter does not serialize this assignment.`:(matchNote||'No library record selected. The drawing requirement stays in JSON; the current XML exporter does not serialize material assignments.')));grid.append(row);
  }
  if(!Object.keys(materialMap).length)grid.append(node('small','material-source','No material requirement is recorded for this item.'));
  materialSection.append(grid);
  body.append(materialSection);
  const evidence=node('div','detail-section');evidence.append(node('h3','','What the drawing shows'));
  const evidenceList=node('ul','detail-list');
  for(const item of product.review_details?.drawing || product.evidence || [])if(!hasCanonicalText(item))evidenceList.append(node('li','',item));
  if(!evidenceList.children.length)evidenceList.append(node('li','','No drawing note is available.'));
  evidence.append(evidenceList);body.append(evidence);
  const settings=node('div','detail-section');settings.append(node('h3','','Proposed settings'));
  const settingList=node('div','settings');const friendly={Toe_Kick_Height:'Toe kick height',Adj_Shelf_Qty:'Adjustable shelves'};
  for(const item of product.prompt_overrides || []){
    const row=node('div','setting');const label=item.display_label || friendly[item.name] || item.name.replaceAll('_',' ');
    const unit=item.unit || (item.name==='Toe_Kick_Height' ? model.project.units : '');
    row.append(node('strong','',`${label}: ${text(item.value)}${unit?' '+unit:''}`));
    if(item.reason&&!hasCanonicalText(item.reason))row.append(node('small','',item.reason));settingList.append(row);
  }
  if(!settingList.children.length)settingList.append(node('div','detail-value','No settings proposed yet.'));
  settings.append(settingList);body.append(settings);
  const concerns=product.review_details?.questions || [];
  const visibleConcerns=concerns.filter(q=>!hasCanonicalText(q));
  if(visibleConcerns.length){const section=node('div','detail-section');section.append(node('h3','','Please check'));const list=node('ul','detail-list');visibleConcerns.forEach(q=>list.append(node('li','',q)));section.append(list);body.append(section)}
  target.append(body);
  const actions=node('div','edit-actions');const save=node('button','edit-save','Save');save.type='button';save.disabled=!dirty;save.onclick=()=>commitItem(product.id);actions.append(save);
  if(dirty){const cancel=node('button','edit-cancel','Cancel');cancel.type='button';cancel.onclick=()=>{editDrafts.delete(product.id);showProduct(findItem(product.id))};actions.append(cancel)}
  target.append(actions);
}

async function saveNormalizedDimensions(itemId,fields,button){
  if(saving)return;saving=true;button.disabled=true;setSaveStatus('Saving dimensions to the selected JSON…');
  try{
    const dimensions=Object.fromEntries(Object.entries(fields).map(([key,input])=>[key,input.valueAsNumber]));
    const response=await fetch('/api/dimensions',{method:'POST',headers:{'Content-Type':'application/json','If-Match':currentRevision},body:JSON.stringify({item_id:itemId,dimensions})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'Could not save dimensions.');
    currentRevision=null;setSaveStatus('Saved to the selected JSON. Reloading preview…','saved');
  }catch(error){setSaveStatus(error.message,'error')}
  finally{saving=false;button.disabled=false}
  if(currentRevision===null)await refresh();
}

function findItem(id, source=model){return [...(source.room.products||[]),...(source.room.features||[])].find(x=>x.id===id)}
function editableItem(id){return editDrafts.get(id)||cloneItem(findItem(id))}
function editItem(id,mutate){const draft=editableItem(id);mutate(draft);if(!hasItemChanges(draft,findItem(id)))editDrafts.delete(id);else editDrafts.set(id,draft);if(selectedId===id)showProduct(editDrafts.get(id)||findItem(id))}
function editDimension(id,key,raw){const value=raw===''?null:Number(raw);if(value!==null&&(!Number.isFinite(value)||value<=0)){setSaveStatus('Enter a positive dimension or leave it blank.','error');return}
  editItem(id,item=>{const previous=item.dimensions[key];item.dimensions[key]=value;item.dimension_evidence??={};item.dimension_evidence[key]={status:value===null?'unknown':'confirmed',description:value===null?'Set to unknown in the review editor.':'Confirmed by reviewer in the local review editor.'};if(value===null&&item.visual_dimensions?.[key]==null&&Number.isFinite(previous)&&previous>0){item.visual_dimensions??={};item.visual_dimensions[key]=previous}});
}
async function commitItem(id){const draft=editDrafts.get(id);if(!draft)return;const next=applyItemDraft(cloneItem(model),id,draft);editDrafts.delete(id);model=next;try{await saveModel(next);if(selectedId===id)showProduct(findItem(id))}catch(error){editDrafts.set(id,draft);if(selectedId===id)showProduct(draft)}}

function makeCatalogPicker(kind,placeholder,value,onChoose,allowCustom=false,context={}){
  const wrap=node('div','catalog-picker');const input=node('input','edit-field catalog-search');input.type='search';input.placeholder=placeholder;input.value=value;input.setAttribute('aria-label',placeholder);input.setAttribute('autocomplete','off');
  const menu=node('div','catalog-menu');menu.hidden=true;menu.setAttribute('role','listbox');wrap.append(input,menu);
  let request=0,searchTerm='';
  const load=async(term,offset=0,append=false)=>{const token=++request;searchTerm=term;menu.hidden=false;if(!append){menu.replaceChildren(node('div','catalog-message','Loading choices…'))}
    try{const query=new URLSearchParams({q:term,offset:String(offset),...context});const response=await fetch(`/api/catalog/${kind}?${query}`,{cache:'no-store'});const data=await response.json();if(token!==request)return;if(!response.ok)throw new Error(data.error||'Catalog unavailable');if(!append){menu.replaceChildren();menu.append(node('div','catalog-scope',`${data.scope} · ${data.total} choices`))}
      const choices=data.results.slice();
      if(!append&&allowCustom){
        const norm=x=>String(x||'').trim().toLocaleLowerCase();
        if(value&&(!term||norm(value).includes(norm(term))))choices.unshift({name:value,is_custom:true,is_current:true});
        else if(term&&!choices.some(x=>norm(x.name)===norm(term)))choices.unshift({name:term,is_custom:true,is_typed:true});
      }
      if(!choices.length&&!append){menu.append(node('div','catalog-message','No matching choices.'))}
      for(const rawChoice of choices){const choice={...rawChoice,catalog_sha256:data.catalog_sha256,source_sqlite_sha256:data.source_sqlite_sha256,scope:data.scope,group:data.group,role:data.role};const option=node('button','catalog-option'+(choice.is_custom?' catalog-option-custom':''));option.type='button';option.setAttribute('role','option');const detail=choice.is_custom?(choice.is_current?'Current draft text · no SDF record':'Use as custom text · no SDF record'):kind==='materials'?(choice.group_assigned?'Assigned by a pointer in this group':'Available in this group’s material library'):`SDF record ${choice.record_id}`;option.append(node('strong','',choice.name),node('small','',detail));option.onclick=async()=>{input.value=choice.name;menu.hidden=true;await onChoose(choice)};menu.append(option)}
      const oldMore=menu.querySelector('.catalog-more');oldMore?.remove();if(data.total>offset+data.results.length){const more=node('button','catalog-more',`Show more · ${offset+data.results.length} of ${data.total}`);more.type='button';more.onclick=()=>load(searchTerm,offset+data.results.length,true);menu.append(more)}
    }catch(error){if(token===request){menu.replaceChildren(node('div','catalog-message',error.message));menu.hidden=false}}
  };
  input.onfocus=()=>{if(menu.hidden)load('');if(input.value)input.select()};
  input.oninput=()=>load(input.value.trim());
  input.onkeydown=e=>{if(e.key==='Escape'){menu.hidden=true;return}if(e.key==='ArrowDown'&&menu.firstElementChild?.classList.contains('catalog-option')){e.preventDefault();menu.querySelector('.catalog-option')?.focus()}};
  menu.onkeydown=e=>{const options=[...menu.querySelectorAll('.catalog-option')],index=options.indexOf(document.activeElement);if(e.key==='Escape'){menu.hidden=true;input.focus()}else if(e.key==='ArrowDown'){e.preventDefault();options[Math.min(index+1,options.length-1)]?.focus()}else if(e.key==='ArrowUp'){e.preventDefault();(index<=0?input:options[index-1]).focus()}};
  input.onblur=()=>setTimeout(()=>{if(!wrap.contains(document.activeElement))menu.hidden=true},120);
  return wrap;
}
function hasCanonicalText(value){return typeof value==='string'&&/canonical/i.test(value)}
function setSaveStatus(message,state='') {let status=$('#saveStatus');if(!status){status=node('div','save-status');status.id='saveStatus';$('#selected').prepend(status)}status.textContent=message;status.dataset.state=state}
async function saveModel(next){
  // Update the in-memory draft immediately so rapid successive edits build on
  // one another, then serialize writes so each If-Match uses the latest hash.
  model=next;
  saveQueue=saveQueue.catch(()=>{}).then(async()=>{
    saving=true;setSaveStatus('Saving…');
    try{next.project.revision=model.project.revision;const response=await fetch('/api/model',{method:'POST',headers:{'Content-Type':'application/json','If-Match':currentRevision},body:JSON.stringify(next)});const result=await response.json();if(!response.ok)throw new Error(result.error||'Save failed');currentRevision=result.revision;model.project.revision=result.project_revision;next.project.revision=result.project_revision;drawLayout();if(threeScene)build3d();setSaveStatus('Saved to the source JSON.','saved')}
    catch(error){setSaveStatus(error.message,'error');throw error}
    finally{saving=false}
  });
  return saveQueue;
}

async function ensure3d() {
  if(threeInit)return threeInit;
  threeInit=(async()=>{
    THREE=await import('three');
    const {OrbitControls}=await import('https://unpkg.com/three@0.169.0/examples/jsm/controls/OrbitControls.js');
    const host=$('#threeCanvas');threeScene=new THREE.Scene();threeScene.background=new THREE.Color('#f5f8f5');
    threeCamera=new THREE.PerspectiveCamera(38,1,10,50000);
    threeRenderer=new THREE.WebGLRenderer({antialias:true});threeRenderer.setPixelRatio(Math.min(window.devicePixelRatio,2));threeRenderer.outputColorSpace=THREE.SRGBColorSpace;host.append(threeRenderer.domElement);
    threeControls=new OrbitControls(threeCamera,threeRenderer.domElement);threeControls.enableDamping=true;threeControls.maxPolarAngle=Math.PI*.49;
    threeScene.add(new THREE.HemisphereLight(0xffffff,0xb5c9b9,2.2));const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(2000,4500,3000);threeScene.add(light);
    threeRenderer.domElement.addEventListener('click',event=>{
      const rect=threeRenderer.domElement.getBoundingClientRect();const pointer=new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
      const ray=new THREE.Raycaster();ray.setFromCamera(pointer,threeCamera);const hit=ray.intersectObjects(threeMeshes,false)[0];
      if(hit){const product=[...model.room.products,...(model.room.features||[])].find(p=>p.id===hit.object.userData.productId);if(product)selectProduct(product)}
    });
    threeObserver=new ResizeObserver(resize3d);threeObserver.observe(host);
    const animate=()=>{requestAnimationFrame(animate);if(viewMode==='3d'){threeControls.update();threeRenderer.render(threeScene,threeCamera)}};animate();
    build3d();resize3d();
  })().catch(error=>{threeInit=null;$('#threeError').hidden=false;$('#threeError').textContent='The 3D view is not available. Use the front view.';console.error(error)});
  return threeInit;
}
function resize3d(){if(!threeRenderer||viewMode!=='3d')return;const host=$('#threeCanvas'),w=host.clientWidth,h=host.clientHeight;if(!w||!h)return;threeRenderer.setSize(w,h,false);threeCamera.aspect=w/h;threeCamera.updateProjectionMatrix()}
function build3d(){
  if(!threeScene||!model)return;
  for(const object of [...threeScene.children])if(object.userData.dynamic){threeScene.remove(object);object.traverse(x=>{x.geometry?.dispose();if(x.material){for(const m of Array.isArray(x.material)?x.material:[x.material])m.dispose()}})}
  threeMeshes=[];
  const wall=model.room.walls?.find(w=>w.id===selectedWallId)||model.room.walls?.[0];const total=wall?Math.hypot(wall.end[0]-wall.start[0],wall.end[1]-wall.start[1]):2000;
  const center=wall?[(wall.start[0]+wall.end[0])/2,-(wall.start[1]+wall.end[1])/2]:[1000,0];
  const floor=new THREE.Mesh(new THREE.PlaneGeometry(Math.max(total*2,3500),3000),new THREE.MeshStandardMaterial({color:'#e9f0e9'}));floor.rotation.x=-Math.PI/2;floor.position.set(center[0],-5,center[1]);floor.userData.dynamic=true;threeScene.add(floor);
  for(const w of model.room.walls||[]){const length=Math.hypot(w.end[0]-w.start[0],w.end[1]-w.start[1]);if(!length)continue;const panel=new THREE.Mesh(new THREE.BoxGeometry(length,w.height||2400,w.thickness||100),new THREE.MeshStandardMaterial({color:'#cedbd1',transparent:true,opacity:.45,side:THREE.DoubleSide}));panel.position.set((w.start[0]+w.end[0])/2,(w.height||2400)/2,-(w.start[1]+w.end[1])/2);panel.rotation.y=Math.atan2(w.end[1]-w.start[1],w.end[0]-w.start[0]);panel.userData.dynamic=true;threeScene.add(panel)}
  for(const p of model.room.products){const w=model.room.walls?.find(x=>x.id===p.wall_id);if(!w||!p.dimensions)continue;const width=sizeOf(p,'width'),height=sizeOf(p,'height'),depth=sizeOf(p,'depth');if(!(width>0&&height>0&&depth>0))continue;
    const dx=w.end[0]-w.start[0],dy=w.end[1]-w.start[1],length=Math.hypot(dx,dy);if(!length)continue;const ux=dx/length,uy=dy/length,dist=(p.offset||0)+width/2,away=(w.thickness||0)/2+depth/2;
    const mesh=new THREE.Mesh(new THREE.BoxGeometry(width,height,depth),new THREE.MeshStandardMaterial({color:'#b9d3c2',roughness:.75}));
    // Put cabinets on the camera-facing side of the wall for the default front view.
    mesh.position.set(w.start[0]+ux*dist+uy*away,(p.elevation||0)+height/2,-(w.start[1]+uy*dist-ux*away));mesh.rotation.y=Math.atan2(dy,dx)-THREE.MathUtils.degToRad(p.rotation||0);mesh.userData={dynamic:true,productId:p.id};
    const edges=new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry),new THREE.LineBasicMaterial({color:'#56836c'}));mesh.add(edges);threeScene.add(mesh);threeMeshes.push(mesh);
  }
  for(const p of model.room.features||[]){if(p.kind!=='rail')continue;const w=model.room.walls?.find(x=>x.id===p.wall_id);if(!w)continue;const dx=w.end[0]-w.start[0],dy=w.end[1]-w.start[1],length=Math.hypot(dx,dy);if(!length)continue;const ux=dx/length,uy=dy/length,railLength=sizeOf(p,'width');if(!railLength)continue;const dist=(p.offset||0)+railLength/2,away=(w.thickness||0)/2+(p.visual_depth_offset||150);
    const mesh=new THREE.Mesh(new THREE.CylinderGeometry(14,14,railLength,16),new THREE.MeshStandardMaterial({color:'#8a9a93',metalness:.55,roughness:.35}));mesh.rotation.z=Math.PI/2;mesh.rotation.y=Math.atan2(dy,dx);mesh.position.set(w.start[0]+ux*dist+uy*away,p.elevation||0,-(w.start[1]+uy*dist-ux*away));mesh.userData={dynamic:true,productId:p.id};threeScene.add(mesh);threeMeshes.push(mesh);
  }
  const dx=wall?wall.end[0]-wall.start[0]:1,dy=wall?wall.end[1]-wall.start[1]:0,len=Math.hypot(dx,dy)||1,tx=dx/len,tz=-dy/len,nx=dy/len,nz=dx/len;const distance=Math.max(total*2.1,3500);threeControls.target.set(center[0],900,center[1]);threeCamera.position.set(center[0]+tx*total*.7+nx*distance,Math.max(total*1.3,2600),center[1]+tz*total*.7+nz*distance);threeControls.update();highlight3d();
}
function highlight3d(){for(const mesh of threeMeshes)mesh.material.color.set(mesh.userData.productId===selectedId?'#e4b77e':'#b9d3c2')}
function setView(mode){viewMode=mode;$('#viewNote').textContent=mode==='top'?'Top view shows one wall at a time. ?? means unknown. The shapes are approximate.':mode==='front'?'Front view shows one wall at a time. ?? means unknown. The shapes are approximate.':'Select an item to see its details. The shapes are approximate.';$('#diagram').style.display=mode==='3d'?'none':'';$('#threeContainer').hidden=mode!=='3d';$('#topLayers').hidden=mode!=='top';for(const [id,active] of [['#frontView',mode==='front'],['#topView',mode==='top'],['#threeView',mode==='3d']]){const b=$(id);b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))}if(mode==='3d')ensure3d().then(resize3d);else drawLayout()}
function setLayer(layer){topLayer=layer;for(const [id,active] of [['#baseLayer',layer==='base'],['#upperLayer',layer==='upper']]){const b=$(id);b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))}drawLayout()}
$('#wallSelect').onchange=e=>{selectedWallId=e.target.value;selectedId=null;drawLayout();if(threeScene)build3d();$('#selected').innerHTML='<div class="detail-empty"><div><strong>Choose an item</strong><span>Select an item to see its details.</span></div></div>'};
$('#frontView').onclick=()=>setView('front');$('#topView').onclick=()=>setView('top');$('#threeView').onclick=()=>setView('3d');$('#baseLayer').onclick=()=>setLayer('base');$('#upperLayer').onclick=()=>setLayer('upper');

async function loadModelChoices(activeKey) {
  const select=$('#modelSelect');
  try {
    const response=await fetch('/api/models',{cache:'no-store'});const data=await response.json();
    if(!response.ok)throw new Error(data.error||'Could not load JSON choices.');
    select.replaceChildren();
    for(const choice of data.models||[]){
      const option=node('option','',choice.selectable?`${choice.project_name} · ${choice.name}${choice.display_only?' · normalized preview':''}`:`${choice.name} · unavailable`);
      option.value=choice.key;option.disabled=!choice.selectable;option.title=choice.reason||choice.pdf_name||'';option.selected=choice.key===activeKey;select.append(option);
    }
    if(!select.options.length){const option=node('option','','No compatible drawing-review JSON files found');option.disabled=true;select.append(option)}
    $('#modelSelectStatus').textContent='Drawing-review models use the full editor; normalized previews allow dimension edits.';
  } catch(error) { $('#modelSelectStatus').textContent=error.message }
}

$('#modelSelect').onchange=async event=>{
  const select=event.currentTarget,key=select.value;select.disabled=true;$('#modelSelectStatus').textContent='Switching review model…';
  try {
    const response=await fetch('/api/select-model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'Could not select that JSON.');
    currentRevision=null;await refresh();
  } catch(error) { $('#modelSelectStatus').textContent=error.message;await loadModelChoices(currentModelKey) }
  finally { select.disabled=false }
};

async function renderPdf() {
  if (!pdfDocument) return;
  const page=await pdfDocument.getPage(pdfPage); const viewport=page.getViewport({scale:1.2});
  const canvas=$('#pdfCanvas');canvas.width=viewport.width;canvas.height=viewport.height;
  await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
  $('#pageNum').textContent=`${pdfPage} / ${pdfDocument.numPages}`;
  $('#prev').disabled=pdfPage===1;$('#next').disabled=pdfPage===pdfDocument.numPages;
}
async function loadPdf() {
  $('#pdfStatus').textContent='Loading drawing…';$('#pdfStatus').hidden=false;
  try {
    if(sourceFormat==='png'){pdfDocument=null;const image=new Image();image.src='/api/drawing';await image.decode();const canvas=$('#pdfCanvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.getContext('2d').drawImage(image,0,0);$('#pageNum').textContent='1 / 1';$('#prev').disabled=true;$('#next').disabled=true;$('#pdfStatus').hidden=true;return}
    const pdfjs=await import('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs');
    pdfjs.GlobalWorkerOptions.workerSrc='https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs';
    pdfDocument=await pdfjs.getDocument('/api/drawing').promise;pdfPage=1;await renderPdf();$('#pdfStatus').hidden=true;
  } catch(error){$('#pdfStatus').textContent='Preview unavailable. Use “Open original PDF” above.'}
}
$('#prev').onclick=async()=>{if(pdfPage>1){pdfPage--;await renderPdf()}};
$('#next').onclick=async()=>{if(pdfDocument && pdfPage<pdfDocument.numPages){pdfPage++;await renderPdf()}};
async function refresh() {
  if(busy||saving)return;busy=true;
  try {
    const response=await fetch('/api/review',{cache:'no-store'});
    if(!response.ok){const info=await response.json();throw new Error(info.error)}
    const info=await response.json();if(info.revision===currentRevision&&info.model_key===currentModelKey)return;
    const result=await fetch('/api/model',{cache:'no-store'});if(!result.ok)throw new Error('Cannot read draft JSON.');
    const incoming=await result.json();if(!incoming?.project || !Array.isArray(incoming?.room?.products) || !Array.isArray(incoming?.review?.issues))throw new Error('Draft JSON is missing required fields.');
    model=incoming;editDrafts.clear();currentRevision=info.revision;currentModelKey=info.model_key||info.model_name;sourceFormat=info.source_format||'pdf';displayOnly=Boolean(info.display_only);$('#readOnlyNotice').hidden=!displayOnly;selectedId=null;selectedWallId=model.room.walls[0]?.id??null;$('#wallSelect').replaceChildren();for(const w of model.room.walls){const option=node('option','',w.name||w.id);option.value=w.id;$('#wallSelect').append(option)}$('#wallPicker').hidden=model.room.walls.length<2;
    await loadModelChoices(currentModelKey);
    $('#projectName').textContent=model.project.name;$('#questions').replaceChildren();const measures=(model.drawing_measurements||[]).filter(x=>!hasCanonicalText(x));$('#measurementsCard').hidden=!measures.length;$('#measurements').replaceChildren();for(const measure of measures)$('#measurements').append(node('li','',measure));
    let visibleQuestionCount=0;for(const issue of model.review.issues){if(hasCanonicalText(issue))continue;const li=document.createElement('li');li.textContent=issue;$('#questions').append(li);visibleQuestionCount++}
    for(const entry of model.room.drawing_inventory||[]){if(entry.resolution!=='unresolved'||hasCanonicalText(entry.label))continue;const evidence=(entry.evidence||[]).filter(x=>!hasCanonicalText(x)).join(' ');const li=document.createElement('li');li.textContent=`Unresolved drawing item: ${entry.label}. ${evidence||'No evidence recorded.'}`;$('#questions').append(li);visibleQuestionCount++}
    if(!visibleQuestionCount){const li=document.createElement('li');li.textContent='No questions listed. Review the drawing and proposed products with the agent.';$('#questions').append(li)}
    const rules=(model.review.applied_rules||[]).filter(rule=>![rule.title,rule.detail,rule.source].some(hasCanonicalText));$('#rulesCard').hidden=!rules.length;$('#appliedRules').replaceChildren();for(const rule of rules){const li=document.createElement('li');li.append(node('strong','rule-title',rule.title||'Applied rule'),node('span','rule-detail',rule.detail||String(rule)));if(rule.source)li.append(node('small','rule-source',rule.source));$('#appliedRules').append(li)}
    $('#selected').innerHTML='<div class="detail-empty"><span class="detail-icon">↖</span><div><strong>Choose a cabinet</strong><span>See its size, drawing notes and proposed settings here.</span></div></div>';
    $('#loading').hidden=true;$('#review').hidden=false;drawLayout();if(threeScene)build3d();await loadPdf();
  } catch(error){$('#review').hidden=true;$('#loading').hidden=false;$('#loadingText').textContent='No current review is available. '+error.message;currentRevision=null}
  finally{busy=false}
}
refresh();setInterval(refresh,3000);
