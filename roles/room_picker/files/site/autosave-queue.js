// Autosave for one settings object. Pure: the write, the timers and the state
// callback are injected, so the rules are tested without a DOM or a clock.
//
// The rules (one queue per settings object, per frame for per-frame settings):
//   - A field change is merged into the latest known object, so two quick edits
//     to different fields make one write that carries both.
//   - At most one write is in flight. An edit made meanwhile waits for it to
//     finish, then the next write carries the latest merged object (latest wins).
//   - Switches and chips pass { delayMs: 0 } and write at once; text and time
//     fields wait delayMs (600) after the last keystroke, or flush on blur.
//   - A failed write keeps its patch; retry() re-sends it merged with any newer
//     edits.
//   - seed() records what the server last said without writing, and never drops
//     a patch that is unsent or in flight.
//
// onState receives "idle" | "pending" | "saving" | "saved" | "failed".

export function createAutosave({
  write,
  delayMs = 600,
  savedMs = 1500,
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
  onState = () => {},
}) {
  let latest = {};
  let pending = null; // edits not yet sent
  let sent = null; // the patch in the write that is in flight
  let inflight = false;
  let running = null; // promise of the write in flight
  let due = false; // the delay for the pending edits has elapsed
  let timer = null;
  let idleTimer = null;
  let state = "idle";

  function setState(next) {
    state = next;
    onState(next);
  }

  function clearTimers() {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  }

  function send() {
    clearTimers();
    const patch = pending;
    pending = null;
    sent = patch;
    due = false;
    inflight = true;
    setState("saving");
    let result;
    try {
      result = Promise.resolve(write({ ...latest }));
    } catch (err) {
      result = Promise.reject(err);
    }
    running = result.then(
        () => {
          inflight = false;
          sent = null;
          if (pending) {
            setState("pending");
            if (due) {
              send();
            }
            return;
          }
          setState("saved");
          idleTimer = setTimer(() => {
            idleTimer = null;
            if (state === "saved") {
              setState("idle");
            }
          }, savedMs);
        },
        () => {
          inflight = false;
          pending = { ...sent, ...pending };
          sent = null;
          setState("failed");
        }
      );
  }

  function kick() {
    if (inflight || !pending) {
      return;
    }
    send();
  }

  function edit(patch, options = {}) {
    if (idleTimer !== null) {
      clearTimer(idleTimer);
      idleTimer = null;
    }
    latest = { ...latest, ...patch };
    pending = { ...pending, ...patch };
    clearTimers();
    due = false;
    setState("pending");
    const wait = options.delayMs ?? delayMs;
    if (wait <= 0) {
      due = true;
      kick();
      return;
    }
    timer = setTimer(() => {
      timer = null;
      due = true;
      kick();
    }, wait);
  }

  async function flush() {
    clearTimers();
    due = true;
    for (;;) {
      if (!inflight && pending) {
        send();
      }
      if (!inflight) {
        return;
      }
      await running;
      if (state === "failed") {
        return;
      }
    }
  }

  function retry() {
    if (!inflight && pending) {
      due = true;
      send();
    }
  }

  function seed(obj) {
    latest = { ...obj, ...sent, ...pending };
  }

  return { edit, flush, retry, seed };
}

// The keys of `next` whose value differs from `prev` (by JSON), so a write that
// carries the whole latest object can still send only what the server lacks.
export function changedKeys(prev, next) {
  const before = prev && typeof prev === "object" ? prev : {};
  return Object.keys(next || {}).filter(
    (key) => JSON.stringify(before[key]) !== JSON.stringify(next[key])
  );
}

// What to show next to a setting for a queue state.
export function saveStatus(state) {
  if (state === "saved") {
    return { text: "Saved", retry: false };
  }
  if (state === "failed") {
    return { text: "Couldn't save.", retry: true };
  }
  return { text: "", retry: false };
}
