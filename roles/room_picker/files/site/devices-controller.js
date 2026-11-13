// The Bluetooth devices controller behind Settings > "Bluetooth devices".
//
// It owns the list, the in-flight flag, the inline forget confirm, and every
// request to /api/pair/devices, with fetch injected so the behaviour is testable
// without a DOM (tests/js/devices-controller.test.js). It follows the pairing
// controller's rules:
//   - Every request has a deadline (createExchange), so one stalled request can
//     never leave the buttons disabled until a reload.
//   - A poll never waits for a POST, and its answer is dropped if an action
//     started or settled after the poll was sent, so a stale list can neither
//     bring back a forgotten phone nor undo a disconnect.
//   - Forgetting needs a second tap: askForget opens the confirm, confirmForget
//     sends.

import {
  parseDevices,
  deviceDisconnectRequest,
  deviceForgetRequest,
  interpretDeviceResponse,
  pairLostContact,
} from "./rooms-model.js";
import { createExchange, PAIR_REQUEST_TIMEOUT_MS } from "./pair-controller.js";

export function createDevicesController({
  fetch: fetchImpl,
  onChange = () => {},
  timeoutMs = PAIR_REQUEST_TIMEOUT_MS,
} = {}) {
  const state = {
    devices: [],
    // Whether the list has been read at least once, so the view can tell "still
    // loading" from "nothing paired".
    loaded: false,
    // A POST is in flight: every row button is disabled, nothing else waits.
    pending: false,
    // The device whose forget is awaiting a second tap.
    confirmingId: null,
    // The name of the phone just forgotten, for the phone-side reminder.
    forgotten: null,
    // Consecutive failed polls; `stale` once the list can no longer be trusted
    // to be current.
    failures: 0,
    stale: false,
    error: null,
  };
  const exchange = createExchange(fetchImpl, timeoutMs);
  let epoch = 0;
  let polling = false;

  function changed() {
    onChange(state);
  }

  function find(id) {
    return state.devices.find((d) => d.id === id) || null;
  }

  async function post(request, kind) {
    state.pending = true;
    epoch += 1;
    changed();
    try {
      const { status, text } = await exchange(request);
      return interpretDeviceResponse(status, text, kind);
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
    if (polling) {
      return;
    }
    polling = true;
    const sent = epoch;
    try {
      const { status, text } = await exchange({ url: "/api/pair/devices", init: { cache: "no-store" } });
      const devices = status >= 200 && status < 300 ? parseDevices(text) : null;
      if (devices === null) {
        failPoll();
        return;
      }
      if (sent !== epoch) {
        return; // an action began or settled since: its answer is newer than this
      }
      state.devices = devices;
      state.loaded = true;
      state.failures = 0;
      state.stale = false;
      if (state.confirmingId !== null && !find(state.confirmingId)) {
        state.confirmingId = null;
      }
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
      state.stale = true;
    }
    changed();
  }

  async function disconnect(id) {
    if (state.pending || !find(id)) {
      return;
    }
    state.error = null;
    state.forgotten = null;
    const result = await post(deviceDisconnectRequest(id), "disconnect");
    if (result.ok) {
      if (result.device) {
        state.devices = state.devices.map((d) => (d.id === id ? result.device : d));
      }
      changed();
      return;
    }
    state.error = result.error;
    changed();
    if (result.stale || result.gone) {
      await poll();
    }
  }

  function askForget(id) {
    if (state.pending || !find(id)) {
      return;
    }
    state.error = null;
    state.confirmingId = id;
    changed();
  }

  function cancelForget() {
    state.confirmingId = null;
    changed();
  }

  async function confirmForget() {
    const id = state.confirmingId;
    const device = id === null ? null : find(id);
    if (state.pending || !device) {
      return;
    }
    state.error = null;
    const result = await post(deviceForgetRequest(id), "forget");
    state.confirmingId = null;
    if (result.ok) {
      state.devices = state.devices.filter((d) => d.id !== id);
      state.forgotten = device.name;
    } else {
      state.error = result.error;
      if (result.gone) {
        state.devices = state.devices.filter((d) => d.id !== id);
      }
    }
    changed();
  }

  function dismissReminder() {
    state.forgotten = null;
    changed();
  }

  return { state, poll, disconnect, askForget, cancelForget, confirmForget, dismissReminder };
}
