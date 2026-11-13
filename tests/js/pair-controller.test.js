// Tests for the pairing controller behind Settings > "Pair a phone". Run with:
//   node --test tests/js
//
// The controller owns the page's pairing state (the last service answer, whether
// this page opened a window, whether a POST is in flight, lost contact) and every
// request, with fetch injected, so the behaviour that went wrong on a real phone
// can be pinned without a DOM:
//   - a request that never answers must not freeze the page (the in-flight flag
//     cleared only when fetch settled, and fetch had no timeout, so one stalled
//     POST disabled every button and stopped every poll until a reload);
//   - a poll keeps running while a POST is in flight;
//   - an answer to a poll sent before an action never overwrites the action.

import { test } from "node:test";
import assert from "node:assert/strict";

import { createPairController } from "../../roles/room_picker/files/site/pair-controller.js";

const OPEN = { state: "open", until: "2026-11-12T10:02:00Z", passkey: null, device: null, message: null };
const IDLE = { state: "idle", until: null, passkey: null, device: null, message: null };

function reply(status, body) {
  return { status, text: async () => JSON.stringify(body) };
}

// A fetch whose answers the test scripts per URL. `hang` never answers but honours
// the abort signal the way the real fetch does.
function makeFetch(routes) {
  const calls = [];
  const fetch = (url, init = {}) => {
    calls.push({ url, method: init.method || "GET" });
    const route = routes[url];
    if (route === "hang") {
      return new Promise((resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }
    return Promise.resolve(typeof route === "function" ? route(init) : route);
  };
  fetch.calls = calls;
  return fetch;
}

const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

test("start opens the window and remembers this page opened it", async () => {
  const fetch = makeFetch({ "/api/pair/start": reply(202, OPEN) });
  const pair = createPairController({ fetch });
  await pair.start();
  assert.equal(pair.state.parsed.state, "open");
  assert.equal(pair.state.started, true);
  assert.equal(pair.state.pending, false);
  assert.equal(pair.state.error, null);
});

test("a start that never answers times out, clears pending and says so", async () => {
  const fetch = makeFetch({ "/api/pair/start": "hang" });
  const pair = createPairController({ fetch, timeoutMs: 20 });
  const started = pair.start();
  assert.equal(pair.state.pending, true);
  await started;
  assert.equal(pair.state.pending, false, "the in-flight flag must clear when the request times out");
  assert.match(pair.state.error, /took too long/i);
});

test("polls keep reaching the service while a POST is in flight", async () => {
  const fetch = makeFetch({ "/api/pair/start": "hang", "/api/pair/state": reply(200, OPEN) });
  const pair = createPairController({ fetch, timeoutMs: 200 });
  const started = pair.start();
  await pair.poll();
  assert.equal(pair.state.parsed.state, "open", "a stalled POST must not stop the page learning the real state");
  await started;
});

test("a second tap while a POST is in flight sends nothing", async () => {
  const fetch = makeFetch({ "/api/pair/start": "hang" });
  const pair = createPairController({ fetch, timeoutMs: 20 });
  const first = pair.start();
  await pair.start();
  await first;
  assert.equal(fetch.calls.filter((c) => c.method === "POST").length, 1);
});

test("a poll answered after the action settled does not overwrite the action", async () => {
  let releasePoll;
  const fetch = makeFetch({
    "/api/pair/state": () => new Promise((resolve) => { releasePoll = () => resolve(reply(200, IDLE)); }),
    "/api/pair/start": reply(202, OPEN),
  });
  const pair = createPairController({ fetch });
  const polling = pair.poll(); // sent before the start, will answer idle
  await tick();
  await pair.start();
  releasePoll();
  await polling;
  assert.equal(pair.state.parsed.state, "open", "the stale idle answer must be dropped");
});

test("a poll that never answers times out and does not block the next one", async () => {
  let n = 0;
  const fetch = makeFetch({ "/api/pair/state": () => (n++ === 0 ? new Promise(() => {}) : reply(200, OPEN)) });
  // The first poll never settles and ignores abort, so the controller's own timer
  // must give up on it; the second poll then goes through.
  const pair = createPairController({ fetch, timeoutMs: 20 });
  await pair.poll();
  assert.equal(pair.state.failures, 1);
  await pair.poll();
  assert.equal(pair.state.parsed.state, "open");
  assert.equal(pair.state.failures, 0);
});

test("a start that conflicts adopts the live window without an error", async () => {
  const fetch = makeFetch({
    "/api/pair/start": reply(409, { error: "already open" }),
    "/api/pair/state": reply(200, OPEN),
  });
  const pair = createPairController({ fetch });
  await pair.start();
  assert.equal(pair.state.error, null);
  assert.equal(pair.state.parsed.state, "open");
  assert.equal(pair.state.started, true);
});

test("five failed polls in a row mean lost contact; retry recovers", async () => {
  let up = false;
  const fetch = makeFetch({ "/api/pair/state": () => (up ? reply(200, OPEN) : reply(502, {})) });
  const pair = createPairController({ fetch });
  for (let i = 0; i < 5; i += 1) await pair.poll();
  assert.equal(pair.state.lost, true);
  up = true;
  await pair.retry();
  assert.equal(pair.state.lost, false);
  assert.equal(pair.state.parsed.state, "open");
});

test("stop returns the service's idle answer and the page reads it as closed", async () => {
  const fetch = makeFetch({ "/api/pair/start": reply(202, OPEN), "/api/pair/stop": reply(200, IDLE) });
  const pair = createPairController({ fetch });
  await pair.start();
  await pair.stop();
  assert.equal(pair.state.parsed.state, "idle");
  assert.equal(pair.state.started, true);
});

test("close goes back to the start view even if the service cannot be reached", async () => {
  const fetch = makeFetch({ "/api/pair/stop": "hang" });
  const pair = createPairController({ fetch, timeoutMs: 20 });
  pair.state.started = true;
  pair.state.lost = true;
  await pair.close();
  assert.equal(pair.state.started, false);
  assert.equal(pair.state.lost, false);
  assert.equal(pair.state.parsed.state, "idle");
});

test("onChange fires when the state changes", async () => {
  let changes = 0;
  const fetch = makeFetch({ "/api/pair/start": reply(202, OPEN) });
  const pair = createPairController({ fetch, onChange: () => { changes += 1; } });
  await pair.start();
  assert.ok(changes >= 2, "once for pending, once for the result");
});
