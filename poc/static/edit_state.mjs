export function hasItemChanges(item, persisted) {
  return JSON.stringify(item) !== JSON.stringify(persisted);
}

export function cloneItem(item) {
  return structuredClone(item);
}

export function selectCatalogProduct(item, choice) {
  const previousGroup = item.specification_group?.link_id || item.library_product?.specification_group?.link_id || null;
  const nextGroup = choice.specification_group?.link_id || null;
  item.library_product = {
    ...item.library_product,
    name: choice.name,
    record_id: choice.record_id,
    link_id: choice.link_id,
    match_status: 'selected',
    match_reason: 'Selected in local review UI.',
    ...(choice.specification_group ? { specification_group: cloneItem(choice.specification_group) } : {}),
  };
  if (choice.specification_group) item.specification_group = cloneItem(choice.specification_group);
  else delete item.specification_group;
  if (previousGroup !== nextGroup) delete item.material_catalog;
  return item;
}

export function applyItemDraft(model, id, draft) {
  const item = [...(model.room.products || []), ...(model.room.features || [])].find(entry => entry.id === id);
  if (!item) throw new Error(`Unknown item: ${id}`);
  for (const key of Object.keys(item)) delete item[key];
  Object.assign(item, cloneItem(draft));
  return model;
}
