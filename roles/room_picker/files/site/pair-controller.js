// The pairing controller behind Settings > "Pair a phone".
//
// It owns the page's pairing state and every request to /api/pair/, with fetch
// injected, so the view (rooms.js) is a thin renderer over `controller.state` and
// the behaviour can be tested without a DOM (tests/js/pair-controller.test.js).
//
// Two rules came out of a real-phone failure where the page stopped responding
// until it was reloaded:
//   1. Every request has a deadline. The in-flight flag (`pending`) used to be
//      cleared only when fetch settled, and fetch has no timeout, so one request
//      that never came back left every button disabled and every poll yielding.
//      Now a request that does not answer in time is abandoned and the flag clears.
//   2. A poll never waits for a POST, and its answer is dropped if an action
//      started or settled after the poll was sent, so a stale "idle" can neither
//      block the page nor overwrite a result that is newer than it.

import {
  parsePairState,
  pairStartRequest,
  pairStopRequest,
  interpretPairResponse,
  pairLostContact,
} from "./rooms-model.js";

// How long one pairing request may take before the page gives up on it. The API
// answers in milliseconds on the LAN, so this is generous.
export const PAIR_REQUEST_TIMEOUT_MS = 8000;

// One request with a deadline, shared with the devices controller. Resolves to
// { status, text } or rejects (network error or timeout). The timer races the
// request itself, so a fetch that ignores the abort signal still cannot hold the
// caller forever.
export function createExchange(fetchImpl, timeoutMs = PAIR_REQUEST_TIMEOUT_MS) {
  return async function exchange(request) {
    const abort = typeof AbortController === "function" ? new AbortController() : null;
    let timer;
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        abort?.abort();
        reject(new Error("timeout"));
      }, timeoutMs);
    });
    const run = (async () => {
      const response = await fetchImpl(request.url, { ...request.init, signal: abort?.signal });
      return { status: response.status, text: await response.text() };
    })();
    run.catch(() => {});
    try {
      return await Promise.race([run, deadline]);
    } finally {
      clearTimeout(timer);
    }
  };
}

const IDLE = Object.freeze({ state: "idle", until: null, passkey: null, device: null, message: null });

export function createPairController({
  fetch: fetchImpl,
  onChange = () => {},
  timeoutMs = PAIR_REQUEST_TIMEOUT_MS,
} = {}) {
  const state = {
    parsed: { ...IDLE },
    // Whether this page opened a window, so one that falls back to idle reads as
    // "closed" rather than as the start view.
    started: false,
    // A POST is in flight: the action buttons are disabled, nothing else waits.
    pending: false,
    // Consecutive failed polls, and whether the service is now treated as gone.
    failures: 0,
    lost: false,
    error: null,
  };
  // Bumped when an action starts and again when it settles. A poll applies its
  // answer only if this is unchanged since the poll was sent.
  let epoch = 0;
  let polling = false;

  function changed() {
    onChange(state);
  }

  const exchange = createExchange(fetchImpl, timeoutMs);

  // Send one action. Returns the interpreted result, or { ok: false, error }.
  async function post(request, endpoint) {
    state.pending = true;
    epoch += 1;
    changed();
    try {
      const { status, text } = await exchange(request);
      return interpretPairResponse(status, text, endpoint);
    } catch (err) {
      return {
        ok: false,
        error:
          err && err.message === "timeout"
            ? "That took too long. Try again."
            : "Couldn't connect. Try again.",
      };
    } finally {
      state.pending = false;
      epoch += 1;
    }
  }

  async function poll() {
    if (state.lost || polling) {
      return;
    }
    polling = true;
    const sent = epoch;
    try {
      const { status, text } = await exchange({ url: "/api/pair/state", init: { cache: "no-store" } });
      if (status < 200 || status >= 300) {
        failPoll();
        return;
      }
      if (sent !== epoch) {
        return; // an action began or settled since: its answer is newer than this
      }
      state.parsed = parsePairState(text);
      state.failures = 0;
      state.lost = false;
      state.error = null;
      changed();
    } catch (err) {
      failPoll();
    } finally {
      polling = false;
    }
  }

  function failPoll() {
    state.failures += 1;
    if (pairLostContact(state.failures)) {
      state.lost = true;
    }
    changed();
  }

  async function start() {
    if (state.pending) {
      return;
    }
    state.error = null;
    state.failures = 0;
    state.lost = false;
    const result = await post(pairStartRequest(), "start");
    if (!result.ok) {
      if (result.conflict) {
        // A window is already open (this tab, another tab, or the CLI): adopt it.
        state.started = true;
        changed();
        await poll();
        return;
      }
      state.error = result.error;
      changed();
      return;
    }
    state.started = true;
    state.parsed = result.state;
    changed();
  }

  async function stop() {
    if (state.pending) {
      return;
    }
    state.error = null;
    const result = await post(pairStopRequest(), "stop");
    if (!result.ok) {
      state.error = result.error;
    } else {
      state.parsed = result.state;
    }
    changed();
  }

  async function retry() {
    state.failures = 0;
    state.lost = false;
    state.error = null;
    changed();
    await poll();
  }

  // Give up on a lost window: back to the start view at once, and ask the hub to
  // close the window too, best effort.
  async function close() {
    state.failures = 0;
    state.lost = false;
    state.started = false;
    state.parsed = { ...IDLE };
    state.error = null;
    changed();
    try {
      await exchange(pairStopRequest());
    } catch (err) {
      // Already gave up; the hub closes the window on its own timeout regardless.
    }
  }

  return { state, start, stop, poll, retry, close };
}
