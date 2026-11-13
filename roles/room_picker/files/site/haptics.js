// Small vibration cues for Android Chrome. Every call is feature-detected and
// wrapped, and the page only calls these from a user gesture (a pointer or key
// handler, a tap), never from a status update.

export function createHaptics(nav = globalThis.navigator) {
  function buzz(ms) {
    try {
      if (nav && typeof nav.vibrate === "function") nav.vibrate(ms);
    } catch (_) {
      // vibrate can throw when blocked by policy; the cue is optional.
    }
  }
  return {
    volume(prev, next) {
      if ((next === 0 || next === 100) && next !== prev) {
        buzz(14);
      } else if (Math.floor(prev / 10) !== Math.floor(next / 10)) {
        buzz(5);
      }
    },
    roomOn: () => buzz(10),
    roomOff: () => buzz(6),
    playPause: () => buzz(8),
  };
}
