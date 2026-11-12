// Tests for the Bluetooth devices controller. Same rules as the pairing one:
// every request has a deadline, a poll never waits for a POST, a poll answered
// after an action is dropped, and forgetting asks first.
import { test } from "node:test";
import assert from "node:assert/strict";

import { createDevicesController } from "../../roles/room_picker/files/site/devices-controller.js";

const PIXEL = { id: "a".repeat(32), name: "Pixel 7", connected: true, last_connected: "2026-11-12T19:59:40Z" };
const IPHONE = { id: "b".repeat(32), name: "Alex's iPhone", connected: false, last_connected: null };
const LIST = { devices: [PIXEL, IPHONE] };

const reply = (status, body) => ({ status, text: async () => JSON.stringify(body) });

function makeFetch(routes) {
  const calls = [];
  const fetch = (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body });
    const route = routes[url];
    if (route === "hang") {
      return new Promise((_, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }
    return Promise.resolve(typeof route === "function" ? route(init) : route);
  };
  fetch.calls = calls;
  return fetch;
}

test("poll loads the list", async () => {
  const c = createDevicesController({ fetch: makeFetch({ "/api/pair/devices": reply(200, LIST) }) });
  assert.equal(c.state.loaded, false);
  await c.poll();
  assert.equal(c.state.loaded, true);
  assert.deepEqual(c.state.devices.map((d) => d.name), ["Pixel 7", "Alex's iPhone"]);
});

test("a failed poll keeps the last list and flags it stale after a run of failures", async () => {
  let ok = true;
  const fetch = makeFetch({ "/api/pair/devices": () => (ok ? reply(200, LIST) : reply(500, {})) });
  const c = createDevicesController({ fetch });
  await c.poll();
  ok = false;
  for (let i = 0; i < 5; i += 1) await c.poll();
  assert.equal(c.state.devices.length, 2);
  assert.equal(c.state.stale, true);
  ok = true;
  await c.poll();
  assert.equal(c.state.stale, false);
});

test("a poll that never answers times out and the next poll still goes out", async () => {
  const fetch = makeFetch({ "/api/pair/devices": "hang" });
  const c = createDevicesController({ fetch, timeoutMs: 20 });
  await c.poll();
  await c.poll();
  assert.equal(fetch.calls.length, 2);
});

test("disconnect posts the id and updates that device from the answer", async () => {
  const fetch = makeFetch({
    "/api/pair/devices": reply(200, LIST),
    "/api/pair/devices/disconnect": reply(200, { ...PIXEL, connected: false }),
  });
  const c = createDevicesController({ fetch });
  await c.poll();
  await c.disconnect(PIXEL.id);
  const post = fetch.calls.find((x) => x.method === "POST");
  assert.deepEqual(JSON.parse(post.body), { id: PIXEL.id });
  assert.equal(c.state.devices[0].connected, false);
  assert.equal(c.state.pending, false);
  assert.equal(c.state.error, null);
});

test("a disconnect that conflicts (already gone) refreshes the list and says so", async () => {
  let calls = 0;
  const fetch = makeFetch({
    "/api/pair/devices": () => reply(200, calls++ === 0 ? LIST : { devices: [{ ...PIXEL, connected: false }, IPHONE] }),
    "/api/pair/devices/disconnect": reply(409, { error: "x" }),
  });
  const c = createDevicesController({ fetch });
  await c.poll();
  await c.disconnect(PIXEL.id);
  assert.match(c.state.error, /not connected/i);
  assert.equal(c.state.devices[0].connected, false);
});

test("forget needs a confirm: the first tap sends nothing", async () => {
  const fetch = makeFetch({ "/api/pair/devices": reply(200, LIST) });
  const c = createDevicesController({ fetch });
  await c.poll();
  c.askForget(PIXEL.id);
  assert.equal(c.state.confirmingId, PIXEL.id);
  assert.equal(fetch.calls.filter((x) => x.method === "POST").length, 0);
});

test("keep cancels the confirm without sending anything", async () => {
  const fetch = makeFetch({ "/api/pair/devices": reply(200, LIST) });
  const c = createDevicesController({ fetch });
  await c.poll();
  c.askForget(PIXEL.id);
  c.cancelForget();
  assert.equal(c.state.confirmingId, null);
  assert.equal(fetch.calls.filter((x) => x.method === "POST").length, 0);
});

test("confirming sends the forget, removes the row and raises the phone-side reminder", async () => {
  const fetch = makeFetch({
    "/api/pair/devices": reply(200, LIST),
    "/api/pair/devices/forget": reply(200, { forgotten: true }),
  });
  const c = createDevicesController({ fetch });
  await c.poll();
  c.askForget(PIXEL.id);
  await c.confirmForget();
  assert.deepEqual(c.state.devices.map((d) => d.name), ["Alex's iPhone"]);
  assert.equal(c.state.confirmingId, null);
  assert.equal(c.state.forgotten, "Pixel 7");
  c.dismissReminder();
  assert.equal(c.state.forgotten, null);
});

test("confirmForget with nothing awaiting confirmation sends nothing", async () => {
  const fetch = makeFetch({});
  const c = createDevicesController({ fetch });
  await c.confirmForget();
  assert.equal(fetch.calls.length, 0);
});

test("a forget for a device already gone drops the row without the reminder", async () => {
  const fetch = makeFetch({
    "/api/pair/devices": reply(200, LIST),
    "/api/pair/devices/forget": reply(404, { error: "x" }),
  });
  const c = createDevicesController({ fetch });
  await c.poll();
  c.askForget(PIXEL.id);
  await c.confirmForget();
  assert.equal(c.state.devices.some((d) => d.id === PIXEL.id), false);
  assert.equal(c.state.forgotten, null);
  assert.match(c.state.error, /no longer paired/i);
});

test("a forget that never answers times out, clears pending and keeps the row", async () => {
  const fetch = makeFetch({ "/api/pair/devices": reply(200, LIST), "/api/pair/devices/forget": "hang" });
  const c = createDevicesController({ fetch, timeoutMs: 20 });
  await c.poll();
  c.askForget(PIXEL.id);
  await c.confirmForget();
  assert.equal(c.state.pending, false);
  assert.match(c.state.error, /did not answer/i);
  assert.equal(c.state.devices.length, 2);
});

test("polls keep going while a POST is in flight", async () => {
  const fetch = makeFetch({
    "/api/pair/devices": reply(200, LIST),
    "/api/pair/devices/disconnect": "hang",
  });
  const c = createDevicesController({ fetch, timeoutMs: 100 });
  await c.poll();
  const pending = c.disconnect(PIXEL.id);
  assert.equal(c.state.pending, true);
  await c.poll();
  assert.equal(fetch.calls.filter((x) => x.url === "/api/pair/devices").length, 2);
  await pending;
});

test("a second tap while a POST is in flight sends nothing", async () => {
  const fetch = makeFetch({ "/api/pair/devices": reply(200, LIST), "/api/pair/devices/disconnect": "hang" });
  const c = createDevicesController({ fetch, timeoutMs: 20 });
  await c.poll();
  const first = c.disconnect(PIXEL.id);
  await c.disconnect(PIXEL.id);
  await first;
  assert.equal(fetch.calls.filter((x) => x.method === "POST").length, 1);
});

test("a poll answered after an action settled does not overwrite the action", async () => {
  let release;
  let n = 0;
  const fetch = makeFetch({
    "/api/pair/devices": () => {
      n += 1;
      return n === 1 ? reply(200, LIST) : new Promise((resolve) => { release = () => resolve(reply(200, LIST)); });
    },
    "/api/pair/devices/forget": reply(200, { forgotten: true }),
  });
  const c = createDevicesController({ fetch });
  await c.poll();
  const slow = c.poll();
  await new Promise((r) => setTimeout(r, 0));
  c.askForget(PIXEL.id);
  await c.confirmForget();
  release();
  await slow;
  assert.equal(c.state.devices.some((d) => d.id === PIXEL.id), false, "the stale poll must not bring the row back");
});

test("a poll that no longer lists the device being confirmed closes the confirm", async () => {
  let n = 0;
  const fetch = makeFetch({ "/api/pair/devices": () => reply(200, n++ === 0 ? LIST : { devices: [IPHONE] }) });
  const c = createDevicesController({ fetch });
  await c.poll();
  c.askForget(PIXEL.id);
  await c.poll();
  assert.equal(c.state.confirmingId, null);
});

test("askForget ignores an id that is not listed", async () => {
  const c = createDevicesController({ fetch: makeFetch({ "/api/pair/devices": reply(200, LIST) }) });
  await c.poll();
  c.askForget("z".repeat(32));
  assert.equal(c.state.confirmingId, null);
});
