// Aurora behind the style interface: { node, start, stop, setHues, setNight }.
// The drawing is unchanged (aurora.js); its look does not change at night.

import { createAurora } from "./aurora.js";

export function createAuroraStyle({ doc, win }) {
  const canvas = doc.createElement("canvas");
  const aurora = createAurora({
    canvas,
    getSize: () => ({ width: win.innerWidth, height: win.innerHeight }),
    raf: (fn) => win.requestAnimationFrame(fn),
    caf: (id) => win.cancelAnimationFrame(id),
    nowMs: () => win.performance.now(),
  });
  return {
    node: canvas,
    start: () => aurora.start(),
    stop: () => aurora.stop(),
    setHues: (h) => aurora.setHues(h),
    setNight() {},
  };
}
