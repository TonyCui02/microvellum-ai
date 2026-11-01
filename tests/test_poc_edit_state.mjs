import test from 'node:test';
import assert from 'node:assert/strict';
import { applyItemDraft, cloneItem, hasItemChanges, selectCatalogProduct } from '../poc/static/edit_state.mjs';

test('item is clean against its JSON baseline and dirty after a field edit', () => {
  const original = { id: 'P1', dimensions: { width: 500 }, material_catalog: { edging: { name: 'White' } } };
  const draft = cloneItem(original);
  assert.equal(hasItemChanges(draft, original), false);
  draft.dimensions.width = 510;
  assert.equal(hasItemChanges(draft, original), true);
  assert.equal(original.dimensions.width, 500);
});

test('cancel is a no-op and applying a saved draft changes only its item', () => {
  const original = { room: { products: [{ id: 'P1', value: 1 }, { id: 'P2', value: 2 }], features: [] } };
  const pending = cloneItem(original.room.products[0]);
  pending.value = 3;
  // Cancel discards the isolated draft; the JSON model remains unchanged.
  assert.equal(original.room.products[0].value, 1);
  applyItemDraft(original, 'P1', pending);
  assert.deepEqual(original.room.products, [{ id: 'P1', value: 3 }, { id: 'P2', value: 2 }]);
});

test('save supports feature items and rejects stale item IDs', () => {
  const model = { room: { products: [], features: [{ id: 'F1', width: 100 }] } };
  applyItemDraft(model, 'F1', { id: 'F1', width: 120 });
  assert.equal(model.room.features[0].width, 120);
  assert.throws(() => applyItemDraft(model, 'missing', {}), /Unknown item/);
});

test('product choice follows its specification group and drops only stale material candidates', () => {
  const item = {
    library_product: { name: 'Old', specification_group: { link_id: 'group-a' } },
    specification_group: { name: 'Kitchen Library', link_id: 'group-a' },
    material: { panel_finish: 'White melamine' },
    material_catalog: { panel_finish: { record_id: 'm1', link_id: 'mat-a', name: 'White' } },
  };
  selectCatalogProduct(item, { name: 'New product', record_id: 'p2', link_id: 'prod-b', specification_group: { name: 'Alt group', link_id: 'group-b' } });
  assert.equal(item.specification_group.link_id, 'group-b');
  assert.equal(item.library_product.record_id, 'p2');
  assert.equal(item.material_catalog, undefined);
  assert.equal(item.material.panel_finish, 'White melamine');
});
