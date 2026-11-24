// The Game of Life screensaver style: Conway's rules on an 80 by 48 grid, a step about
// every half second (1.5 s at night), dying cells leaving a fading ghost, tinted by
// the album hues; a patch is reseeded when it stalls. Redrawn 10 times a second so
// the ghosts fade smoothly. Night: slower steps and dim cells. Rules in life-model.js.

import { createCanvasStyle } from "./canvas-style.js";
import { lerp, rgba } from "./gen-color.js";
import { tintColor } from "./aurora-math.js";
import { LIFE, createLife, stepIntervalS } from "./life-model.js";

const AGE_BINS = 16;
const GHOST_LEVELS = 6;

export function createLifeStyle(env) {
  return createCanvasStyle(env, {
    width: LIFE.width,
    height: LIFE.height,
    fps: () => LIFE.maxFps,
    make({ ctx, width, height, random }) {
      const life = createLife({ random });
      const size = LIFE.cell - 1;
      let alive = [];
      let ghosts = [];
      let keyVersion = -1;
      let keyNight = -1;
      let acc = stepIntervalS(0);
      return {
        frame(dt, view) {
          acc += dt;
          if (acc >= stepIntervalS(view.night)) {
            acc = 0;
            life.step();
          }
          life.decayGhost(dt);
          if (view.hueVersion !== keyVersion || view.night !== keyNight) {
            keyVersion = view.hueVersion;
            keyNight = view.night;
            alive = [];
            for (let b = 0; b < AGE_BINS; b += 1) {
              const k = b / (AGE_BINS - 1);
              alive.push(rgba(tintColor(view.hues, lerp(0.82 - 0.2 * k, 0.38, view.night), k), 1));
            }
            const ghostColor = tintColor(view.hues, 0.5, 1);
            const peak = lerp(0.3, 0.12, view.night);
            ghosts = [];
            for (let q = 0; q <= GHOST_LEVELS; q += 1) ghosts.push(rgba(ghostColor, (q / GHOST_LEVELS) * peak));
          }
          ctx.fillStyle = "#000";
          ctx.fillRect(0, 0, width, height);
          const { cells, age, ghost, cols } = life;
          let last = -1;
          for (let i = 0; i < cells.length; i += 1) {
            if (cells[i]) continue;
            const g = ghost[i];
            if (g <= 0) continue;
            const q = Math.max(1, Math.round(g * GHOST_LEVELS));
            if (q !== last) {
              ctx.fillStyle = ghosts[q];
              last = q;
            }
            ctx.fillRect((i % cols) * LIFE.cell, Math.floor(i / cols) * LIFE.cell, size, size);
          }
          last = -1;
          for (let i = 0; i < cells.length; i += 1) {
            if (!cells[i]) continue;
            const b = Math.min(AGE_BINS - 1, Math.floor((Math.min(1, age[i] / 20)) * (AGE_BINS - 1)));
            if (b !== last) {
              ctx.fillStyle = alive[b];
              last = b;
            }
            ctx.fillRect((i % cols) * LIFE.cell, Math.floor(i / cols) * LIFE.cell, size, size);
          }
        },
      };
    },
  });
}
