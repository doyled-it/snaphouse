import { test } from "node:test";
import assert from "node:assert/strict";
import { FACTORIES, createStyle } from "../../roles/room_picker/files/site/style-registry.js";
import { SHIPPED_IDS } from "../../roles/room_picker/files/site/style-list.js";

test("every shipped style has a factory and every factory is shipped", () => {
  assert.deepEqual(Object.keys(FACTORIES).sort(), [...SHIPPED_IDS].sort());
});

test("a factory builds the common interface with a node to mount", () => {
  const made = [];
  const env = {
    doc: { createElement(tag) { const n = { tag, children: [], style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} }, append() {}, getContext: () => ({ createRadialGradient: () => ({ addColorStop() {} }) }) }; made.push(n); return n; } },
    win: { innerWidth: 800, innerHeight: 480, requestAnimationFrame() {}, cancelAnimationFrame() {}, performance: { now: () => 0 } },
    now: () => 0,
    setTimeout() { return 1; },
    clearTimeout() {},
    random: () => 0.5,
  };
  for (const id of SHIPPED_IDS) {
    const style = createStyle(id, env);
    for (const fn of ["start", "stop", "setHues", "setNight"]) assert.equal(typeof style[fn], "function", `${id}.${fn}`);
    assert.ok(style.node, `${id}.node`);
  }
});

test("an id with no module has no style", () => {
  assert.equal(createStyle("facts", {}), null);
});
