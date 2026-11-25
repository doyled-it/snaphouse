import { test } from "node:test";
import assert from "node:assert/strict";
import { createCoverFollower } from "../../roles/room_picker/files/site/cover-follow.js";

const DEF = { h1: 255, h2: 285, chroma: 0 };
const RED = { h1: 10, h2: 40, chroma: 0.5 };
const TEAL = { h1: 180, h2: 200, chroma: 0.6 };

function setup() {
  const page = [];
  const saver = [];
  const pending = new Map();
  const follow = createCoverFollower({
    defaults: DEF,
    load: (url) =>
      new Promise((resolve) => {
        pending.set(url, resolve);
      }),
    setPage: (h) => page.push(h),
    setSaver: (h) => saver.push(h),
  });
  return { page, saver, pending, follow };
}

test("a cover sets the page and the screensaver hues", async () => {
  const { page, saver, pending, follow } = setup();
  follow("/cover/a.jpg");
  pending.get("/cover/a.jpg")(RED);
  await Promise.resolve();
  assert.deepEqual(page, [RED]);
  assert.deepEqual(saver, [RED]);
});

test("clearing resets the page tint but the screensaver keeps the last cover", async () => {
  const { page, saver, pending, follow } = setup();
  follow("/cover/a.jpg");
  pending.get("/cover/a.jpg")(RED);
  await Promise.resolve();
  follow(null);
  assert.deepEqual(page, [RED, DEF]);
  assert.deepEqual(saver, [RED]);
});

test("a clear before any cover leaves the screensaver on its defaults", () => {
  const { page, saver, follow } = setup();
  follow(null);
  assert.deepEqual(page, [DEF]);
  assert.deepEqual(saver, []);
});

test("a new cover after a clear replaces the kept hues", async () => {
  const { saver, pending, follow } = setup();
  follow("/cover/a.jpg");
  pending.get("/cover/a.jpg")(RED);
  await Promise.resolve();
  follow(null);
  follow("/cover/b.jpg");
  pending.get("/cover/b.jpg")(TEAL);
  await Promise.resolve();
  assert.deepEqual(saver, [RED, TEAL]);
});

test("an out of order result is dropped by the token", async () => {
  const { page, saver, pending, follow } = setup();
  follow("/cover/a.jpg");
  follow("/cover/b.jpg");
  pending.get("/cover/b.jpg")(TEAL);
  await Promise.resolve();
  pending.get("/cover/a.jpg")(RED);
  await Promise.resolve();
  assert.deepEqual(page, [TEAL]);
  assert.deepEqual(saver, [TEAL]);
});

test("a result that lands after a clear is dropped", async () => {
  const { page, saver, pending, follow } = setup();
  follow("/cover/a.jpg");
  follow(null);
  pending.get("/cover/a.jpg")(RED);
  await Promise.resolve();
  assert.deepEqual(page, [DEF]);
  assert.deepEqual(saver, []);
});
