// The Contours screensaver style: topographic lines of a slowly changing noise field,
// every fourth line heavier, tinted by the album hues. The field is recomputed and the
// lines redrawn about 12 times a second (it moves slowly). Night: every other line
// fades out and the rest are dimmer, on black. Rules in contours-model.js.

import { createCanvasStyle } from "./canvas-style.js";
import { lerp, rgba } from "./gen-color.js";
import { tintColor } from "./aurora-math.js";
import { CONTOURS, fillField, levelView, marchLevel } from "./contours-model.js";

const DAY_BG = [8, 13, 22];

export function createContoursStyle(env) {
  return createCanvasStyle(env, {
    width: CONTOURS.width,
    height: CONTOURS.height,
    fps: () => CONTOURS.maxFps,
    make({ ctx, width, height, random }) {
      const values = new Float32Array(CONTOURS.cols * CONTOURS.rows);
      const seg = [];
      const n = CONTOURS.levels.length;
      let t = random() * 100;
      ctx.lineCap = "round";
      return {
        frame(dt, view) {
          t += dt * CONTOURS.timeRate;
          fillField(values, CONTOURS.cols, CONTOURS.rows, t);
          const bg = DAY_BG.map((c) => Math.round(lerp(c, 0, view.night)));
          ctx.fillStyle = `rgb(${bg[0]},${bg[1]},${bg[2]})`;
          ctx.fillRect(0, 0, width, height);
          const alpha = lerp(0.85, 0.5, view.night);
          for (let li = 0; li < n; li += 1) {
            const look = levelView(li, view.night);
            if (look.weight <= 0.01) continue;
            seg.length = 0;
            const count = marchLevel(values, CONTOURS.cols, CONTOURS.rows, CONTOURS.levels[li], CONTOURS.cell, seg);
            if (count === 0) continue;
            ctx.beginPath();
            for (let s = 0; s < seg.length; s += 4) {
              ctx.moveTo(seg[s], seg[s + 1]);
              ctx.lineTo(seg[s + 2], seg[s + 3]);
            }
            const k = li / (n - 1);
            ctx.strokeStyle = rgba(tintColor(view.hues, lerp(0.62 + 0.2 * k, 0.45, view.night), k), alpha * look.weight);
            ctx.lineWidth = look.heavy ? 1 : 0.6;
            ctx.stroke();
          }
        },
      };
    },
  });
}
