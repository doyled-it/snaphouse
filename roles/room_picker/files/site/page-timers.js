// Polling timers that sleep while the tab is hidden. Pure: the clock functions
// and the hidden check are injected. `add` registers an interval; `start` and
// `resume` begin every interval that is not already running, once; `pause`
// clears them all and is safe before `start`. Nothing here calls a callback
// itself: the page's visibilitychange handler does its own refresh on return.

export function createPageTimers({ setInterval, clearInterval, isHidden = () => false }) {
  const entries = [];
  let started = false;
  let paused = false;

  function run(entry) {
    if (entry.id === null) {
      entry.id = setInterval(entry.fn, entry.ms);
    }
  }

  function begin() {
    if (paused || isHidden()) {
      return;
    }
    for (const entry of entries) {
      run(entry);
    }
  }

  return {
    add(fn, ms) {
      const entry = { fn, ms, id: null };
      entries.push(entry);
      if (started && !paused && !isHidden()) {
        run(entry);
      }
    },
    start() {
      started = true;
      begin();
    },
    pause() {
      paused = true;
      for (const entry of entries) {
        if (entry.id !== null) {
          clearInterval(entry.id);
          entry.id = null;
        }
      }
    },
    resume() {
      paused = false;
      if (started) {
        begin();
      }
    },
  };
}
