import { test } from "node:test";
import assert from "node:assert/strict";
import { createPanelSettings } from "../../roles/room_picker/files/site/panel-settings-controller.js";
import { SETTINGS_DEFAULTS } from "../../roles/room_picker/files/site/panel-settings-model.js";

const doc = (entry, pid = "panel") => ({ version: 1, panels: { [pid]: { ...SETTINGS_DEFAULTS, ...entry } } });
const tick = () => new Promise((r) => setImmediate(r));

function make({ pid = "panel", fallback = { ...SETTINGS_DEFAULTS, brightness_pct: 60 }, hub = doc({}), postImpl } = {}) {
  const log = { posts: [], locals: [], changes: [], states: [] };
  const holder = { hub };
  const ctl = createPanelSettings({
    pid,
    fallback,
    getDoc: async () => {
      if (holder.hub instanceof Error) throw holder.hub;
      return holder.hub;
    },
    post: async (body) => {
      log.posts.push(body);
      if (postImpl) return postImpl(body, log);
      return { ok: true, status: 200, data: { panel: body.panel, settings: { ...SETTINGS_DEFAULTS, ...holder.hubEntry, ...strip(body) } } };
    },
    local: async (path, body) => {
      log.locals.push([path, body]);
      if (holder.localFails) throw new Error("no daemon");
    },
    onChange: (s) => log.changes.push(s),
    onSaveState: (s) => log.states.push(s),
    setTimer: (fn) => { holder.timer = fn; return 1; },
    clearTimer: () => {},
  });
  return { ctl, log, holder };
}
const strip = (body) => Object.fromEntries(Object.entries(body).filter(([k]) => k !== "panel"));

test("reads its own entry by pid and applies it", async () => {
  const { ctl, log } = make({ hub: doc({ brightness_pct: 70, screensaver_wait_s: 120 }) });
  assert.equal(ctl.settings().brightness_pct, 60); // the URL fallback until the hub answers
  await ctl.load();
  assert.equal(ctl.settings().brightness_pct, 70);
  assert.equal(ctl.settings().screensaver_wait_s, 120);
  assert.equal(log.changes.at(-1).brightness_pct, 70);
});

test("falls back to the URL values when the document is missing or malformed", async () => {
  for (const hub of [new Error("404"), "junk", { version: 1, panels: {} }, null]) {
    const { ctl } = make({ hub });
    await ctl.load();
    assert.equal(ctl.settings().brightness_pct, 60, String(hub));
  }
});

test("with no pid it is unavailable and never posts", async () => {
  const { ctl, log } = make({ pid: "" });
  assert.equal(ctl.available(), false);
  const out = ctl.edit({ brightness_pct: 70 });
  assert.equal(out.ok, false);
  await tick();
  assert.deepEqual(log.posts, []);
});

test("edit applies locally at once and posts only the changed keys", async () => {
  const { ctl, log } = make();
  await ctl.load();
  const out = ctl.edit({ brightness_pct: 70 });
  assert.equal(out.ok, true);
  assert.equal(ctl.settings().brightness_pct, 70);
  await tick();
  assert.deepEqual(log.posts, [{ panel: "panel", brightness_pct: 70 }]);
});

test("an invalid edit is refused before any write", async () => {
  const { ctl, log } = make();
  await ctl.load();
  const out = ctl.edit({ night_start: "06:00" });
  assert.equal(out.ok, false);
  assert.ok(out.error);
  await tick();
  assert.deepEqual(log.posts, []);
  assert.equal(ctl.settings().night_start, "22:00");
});

test("repeated steps merge, latest wins, one write in flight", async () => {
  let release;
  const { ctl, log } = make({
    postImpl: (body) =>
      new Promise((resolve) => {
        release = () => resolve({ ok: true, status: 200, data: { panel: "panel", settings: { ...SETTINGS_DEFAULTS, ...strip(body) } } });
      }),
  });
  await ctl.load();
  ctl.edit({ brightness_pct: 70 });
  ctl.edit({ brightness_pct: 65 });
  ctl.edit({ night_dim_pct: 10 });
  await tick();
  assert.equal(log.posts.length, 1);
  assert.deepEqual(log.posts[0], { panel: "panel", brightness_pct: 70 });
  release();
  await tick();
  await tick();
  assert.equal(log.posts.length, 2);
  assert.deepEqual(log.posts[1], { panel: "panel", brightness_pct: 65, night_dim_pct: 10 });
  release();
  await tick();
});

test("a good write asks the daemon to read the settings now", async () => {
  const { ctl, log } = make();
  await ctl.load();
  ctl.edit({ screensaver_on: false });
  await tick();
  await tick();
  assert.deepEqual(log.locals.at(-1), ["/refresh", {}]);
});

test("a failed write reports failed and retry re-sends it", async () => {
  let fail = true;
  const { ctl, log } = make({
    postImpl: (body) => (fail ? { ok: false, status: 500, data: { error: "Something went wrong." } } : { ok: true, status: 200, data: { panel: "panel", settings: { ...SETTINGS_DEFAULTS, ...strip(body) } } }),
  });
  await ctl.load();
  ctl.edit({ brightness_pct: 70 });
  await tick();
  await tick();
  assert.equal(log.states.at(-1), "failed");
  assert.equal(ctl.settings().brightness_pct, 70);
  fail = false;
  ctl.retry();
  await tick();
  await tick();
  assert.equal(log.posts.length, 2);
  assert.deepEqual(log.posts[1], { panel: "panel", brightness_pct: 70 });
  assert.equal(log.states.at(-1), "saved");
});

test("a failed local call is ignored", async () => {
  const { ctl, log, holder } = make();
  holder.localFails = true;
  await ctl.load();
  ctl.edit({ brightness_pct: 70 });
  await tick();
  await tick();
  assert.notEqual(log.states.at(-1), "failed");
  await ctl.previewBrightness(40); // must not throw
});

test("previewBrightness sends the percentage for 3 seconds", async () => {
  const { ctl, log } = make();
  await ctl.previewBrightness(35);
  assert.deepEqual(log.locals, [["/preview", { pct: 35, seconds: 3 }]]);
});

test("previewScreensaver holds the screensaver brightness for 15 seconds, then cancels", async () => {
  const { ctl, log } = make({ hub: doc({ screensaver_pct: 25 }) });
  await ctl.load();
  await ctl.previewScreensaver(true);
  await ctl.previewScreensaver(false);
  assert.deepEqual(log.locals, [
    ["/preview", { pct: 25, seconds: 15 }],
    ["/preview", { pct: 25, seconds: 0 }],
  ]);
});

test("a poll applies a change made elsewhere while idle", async () => {
  const { ctl, holder } = make();
  await ctl.load();
  holder.hub = doc({ brightness_pct: 40 });
  await ctl.load();
  assert.equal(ctl.settings().brightness_pct, 40);
});

test("a poll never overwrites a queue that is pending or saving", async () => {
  let release;
  const { ctl, holder } = make({
    postImpl: (body) => new Promise((resolve) => { release = () => resolve({ ok: true, status: 200, data: { panel: "panel", settings: { ...SETTINGS_DEFAULTS, ...strip(body) } } }); }),
  });
  await ctl.load();
  ctl.edit({ brightness_pct: 70 });
  holder.hub = doc({ brightness_pct: 40 });
  await ctl.load();
  assert.equal(ctl.settings().brightness_pct, 70);
  release();
  await tick();
  await tick();
});
