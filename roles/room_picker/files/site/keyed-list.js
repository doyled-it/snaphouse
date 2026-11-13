// Keyed in-place list updates, so a poll that changes one row leaves the others
// (and the focus, hover and image decode state inside them) alone.

// Pure plan. Items are { key, sig }; `sig` is a string of everything that
// affects a row's DOM. Duplicate keys in `newItems` keep the first only.
export function planKeyedUpdate(oldItems, newItems) {
  const oldSigs = new Map();
  for (const item of oldItems) {
    oldSigs.set(item.key, item.sig);
  }
  const order = [];
  const seen = new Set();
  const create = [];
  const replace = [];
  for (const item of newItems) {
    if (seen.has(item.key)) {
      continue;
    }
    seen.add(item.key);
    order.push(item.key);
    if (!oldSigs.has(item.key)) {
      create.push(item.key);
    } else if (oldSigs.get(item.key) !== item.sig) {
      replace.push(item.key);
    }
  }
  const remove = [...oldSigs.keys()].filter((key) => !seen.has(key));
  return { remove, create, replace, order };
}

const registries = new WeakMap();

// Apply `items` to `parent`'s children. Surviving nodes are kept; only
// changed-sig rows are replaced; only nodes out of position are moved.
export function patchKeyed(parent, items, { key, sig, create, update }) {
  let known = registries.get(parent);
  if (!known) {
    known = new Map();
    registries.set(parent, known);
  }
  const wanted = [];
  const wantedKeys = new Set();
  for (const item of items) {
    const k = key(item);
    if (!wantedKeys.has(k)) {
      wantedKeys.add(k);
      wanted.push({ item, key: k, sig: sig(item) });
    }
  }
  const oldItems = [...known].map(([k, v]) => ({ key: k, sig: v.sig }));
  const plan = planKeyedUpdate(oldItems, wanted);

  for (const k of plan.remove) {
    known.get(k).node.remove();
    known.delete(k);
  }
  const byKey = new Map(wanted.map((w) => [w.key, w]));
  for (const k of plan.replace) {
    const w = byKey.get(k);
    const node = create(w.item);
    known.get(k).node.replaceWith(node);
    known.set(k, { node, sig: w.sig });
  }
  for (const k of plan.create) {
    const w = byKey.get(k);
    known.set(k, { node: create(w.item), sig: w.sig });
  }
  if (update) {
    const touched = new Set([...plan.replace, ...plan.create]);
    for (const w of wanted) {
      if (!touched.has(w.key)) {
        update(known.get(w.key).node, w.item);
      }
    }
  }
  // Walk in order, moving only nodes that are not already in place.
  let cursor = parent.firstChild;
  for (const k of plan.order) {
    const node = known.get(k).node;
    if (node === cursor) {
      cursor = cursor.nextSibling;
    } else {
      parent.insertBefore(node, cursor);
    }
  }
}
