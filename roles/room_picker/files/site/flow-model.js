// Pure rules for the Flow field style: fine threads of light that follow a slowly
// changing noise field. Particles live in typed arrays (no allocation per frame).
// `px, py` hold where a particle was before the last step, so the painter strokes
// one short segment per thread. Night uses fewer threads (the active count eases down).

import { fbm3 } from "./noise-math.js";
import { lerp } from "./gen-color.js";

export const FLOW = Object.freeze({
  width: 320,
  height: 192,
  dayCount: 600,
  nightCount: 220,
  bins: 12,
  speed: 16, // canvas px per second (about 40 px a second on the 800 px screen)
  timeRate: 0.015, // noise time units per second: the currents change slowly
  scale: 0.00875, // noise frequency per canvas px
  respawnPerS: 0.24, // chance per second that a thread restarts somewhere else
  maxFps: 60,
});

export function flowAngle(x, y, t) {
  return fbm3(x * FLOW.scale, y * FLOW.scale, t) * Math.PI * 4;
}

export function activeCount(nightMix) {
  return Math.round(lerp(FLOW.dayCount, FLOW.nightCount, nightMix));
}

export function createFlow({ count = FLOW.dayCount, width, height, random }) {
  const f = {
    width,
    height,
    count,
    x: new Float32Array(count),
    y: new Float32Array(count),
    px: new Float32Array(count),
    py: new Float32Array(count),
    bin: new Uint8Array(count),
  };
  for (let i = 0; i < count; i += 1) {
    f.x[i] = random() * width;
    f.y[i] = random() * height;
    f.px[i] = f.x[i];
    f.py[i] = f.y[i];
    f.bin[i] = i % FLOW.bins;
  }
  return f;
}

// Move the first `active` threads by speed * dt along the field at time t. A thread
// that leaves the canvas, or draws a low random number, restarts somewhere else with
// no segment (px, py set to the new spot).
export function stepFlow(f, t, dt, active, random, cfg = FLOW) {
  const dist = cfg.speed * dt;
  const respawnP = cfg.respawnPerS * dt;
  const n = Math.min(active, f.count);
  for (let i = 0; i < n; i += 1) {
    const x = f.x[i];
    const y = f.y[i];
    const a = flowAngle(x, y, t);
    const nx = x + Math.cos(a) * dist;
    const ny = y + Math.sin(a) * dist;
    if (nx < 0 || nx > f.width || ny < 0 || ny > f.height || random() < respawnP) {
      f.x[i] = random() * f.width;
      f.y[i] = random() * f.height;
      f.px[i] = f.x[i];
      f.py[i] = f.y[i];
    } else {
      f.px[i] = x;
      f.py[i] = y;
      f.x[i] = nx;
      f.y[i] = ny;
    }
  }
}
