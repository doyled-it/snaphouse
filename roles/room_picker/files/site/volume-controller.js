// The volume slider's send/hold state machine. Pure: no DOM, with the clock and
// the transport injected, so tests/js/volume-controller.test.js can pin it with a
// fake clock.
//
// Sending: at most one write per `throttleMs`, with a trailing send of the latest
// value, and at most one request in flight (a send that returns a promise holds
// the slot until it settles; one that returns nothing holds it for the gap only).
// Holding: after release the user's value stays on screen until the hub reports
// within `tolerance` of it or `holdMs` pass, so a stale status never snaps the
// thumb back.

export function createVolumeController({
  send,
  now,
  setTimer,
  clearTimer,
  throttleMs = 100,
  holdMs = 2000,
  tolerance = 1,
}) {
  let latest = 0;
  let hasPending = false;
  let isDragging = false;
  let inFlight = false;
  let lastSendAt = -Infinity;
  let timer = null;
  let isHeld = false;
  let heldSince = 0;
  let disposed = false;

  function cancelTimer() {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  }

  function doSend() {
    cancelTimer();
    hasPending = false;
    lastSendAt = now();
    let result;
    try {
      result = send(latest);
    } catch (_) {
      return;
    }
    if (result && typeof result.then === "function") {
      inFlight = true;
      const settle = () => {
        inFlight = false;
        if (!disposed) flush(false);
      };
      result.then(settle, settle);
    }
  }

  // Send the pending value now if the slot and the gap allow, otherwise keep one
  // trailing timer that will. `ignoreGap` is for the first send of a drag.
  function flush(ignoreGap) {
    if (disposed || !hasPending || inFlight) return;
    const wait = ignoreGap ? 0 : lastSendAt + throttleMs - now();
    if (wait <= 0) {
      doSend();
    } else if (timer === null) {
      timer = setTimer(() => {
        timer = null;
        flush(false);
      }, wait);
    }
  }

  return {
    begin(value) {
      isDragging = true;
      isHeld = false;
      latest = value;
      hasPending = true;
      flush(true);
    },
    move(value) {
      latest = value;
      hasPending = true;
      flush(false);
    },
    end() {
      flush(false);
      if (!isDragging) return;
      isDragging = false;
      isHeld = true;
      heldSince = now();
    },
    incoming(hubValue) {
      if (isDragging) return { show: false, value: latest };
      if (isHeld) {
        if (Math.abs(hubValue - latest) <= tolerance || now() - heldSince >= holdMs) {
          isHeld = false;
          return { show: true, value: hubValue };
        }
        return { show: false, value: latest };
      }
      return { show: true, value: hubValue };
    },
    dragging: () => isDragging,
    held: () => isHeld && now() - heldSince < holdMs,
    dispose() {
      disposed = true;
      hasPending = false;
      cancelTimer();
    },
  };
}

export function valueFromPointer(clientX, left, width) {
  if (!(width > 0)) return 0;
  const pct = Math.round(((clientX - left) / width) * 100);
  return Math.max(0, Math.min(100, pct));
}

export function keyStep(key) {
  switch (key) {
    case "ArrowRight":
    case "ArrowUp":
      return 2;
    case "ArrowLeft":
    case "ArrowDown":
      return -2;
    case "PageUp":
      return 10;
    case "PageDown":
      return -10;
    default:
      return null;
  }
}
