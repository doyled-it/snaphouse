import { test } from "node:test";
import assert from "node:assert/strict";
import { fbm3, hash3, noise3 } from "../../roles/room_picker/files/site/noise-math.js";

test("the hash is deterministic and in 0..1", () => {
  assert.equal(hash3(3, 4, 5), hash3(3, 4, 5));
  assert.notEqual(hash3(3, 4, 5), hash3(4, 3, 5));
  for (let i = -20; i < 20; i += 1) {
    const v = hash3(i, i * 3, i * 7);
    assert.ok(v >= 0 && v <= 1);
  }
});

test("noise and fbm stay in 0..1 and repeat for the same input", () => {
  for (let i = 0; i < 400; i += 1) {
    const x = Math.sin(i) * 40;
    const y = Math.cos(i * 1.3) * 40;
    const z = i * 0.37;
    for (const fn of [noise3, fbm3]) {
      const v = fn(x, y, z);
      assert.ok(v >= 0 && v <= 1, `${fn.name}(${x},${y},${z}) = ${v}`);
      assert.equal(v, fn(x, y, z));
    }
  }
});

test("noise is continuous: a tiny step changes the value a tiny amount", () => {
  for (let i = 0; i < 100; i += 1) {
    const x = i * 0.173;
    assert.ok(Math.abs(noise3(x, 1.1, 2.2) - noise3(x + 0.001, 1.1, 2.2)) < 0.02);
    assert.ok(Math.abs(fbm3(x, 1.1, 2.2) - fbm3(x, 1.1, 2.2 + 0.001)) < 0.02);
  }
});

test("the field varies across space and time", () => {
  const seen = new Set();
  for (let i = 0; i < 50; i += 1) seen.add(fbm3(i * 0.5, 0, 0).toFixed(3));
  assert.ok(seen.size > 30);
  assert.notEqual(fbm3(1.5, 2.5, 0), fbm3(1.5, 2.5, 3));
});
