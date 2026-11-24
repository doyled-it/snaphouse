// The Harmonograph screensaver style: a two-pendulum figure drawn over about a minute,
// held 20 s, faded, then a new random figure, tinted by the album hues. Only the new
// stretch of line is drawn each frame; during the hold nothing is drawn at all. Night:
// a fainter line. Rules and the phase clock are in harmonograph-model.js.

import { createCanvasStyle } from "./canvas-style.js";
import { buildColors, lerp, rgba } from "./gen-color.js";
import { HARMONO, createHarmonograph, figurePoint } from "./harmonograph-model.js";

const BINS = 24;
const STEP_U = 0.35;

export function createHarmonographStyle(env) {
  return createCanvasStyle(env, {
    width: HARMONO.width,
    height: HARMONO.height,
    fps: () => HARMONO.maxFps,
    make({ ctx, width, height, random }) {
      const clock = createHarmonograph({ random });
      let colors = [];
      let keyVersion = -1;
      let keyNight = -1;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, width, height);
      ctx.lineWidth = 1;
      return {
        frame(dt, view) {
          const s = clock.advance(dt);
          if (s.fresh) {
            ctx.fillStyle = "#000";
            ctx.fillRect(0, 0, width, height);
          }
          if (view.hueVersion !== keyVersion || view.night !== keyNight) {
            keyVersion = view.hueVersion;
            keyNight = view.night;
            const a = lerp(HARMONO.dayAlpha, HARMONO.nightAlpha, view.night);
            colors = buildColors(view.hues, BINS, lerp(0.8, 0.5, view.night)).map((c) => rgba(c, a));
          }
          if (s.u1 > s.u0) {
            const fig = clock.figure();
            const steps = Math.max(1, Math.ceil((s.u1 - s.u0) / STEP_U));
            let p = figurePoint(fig, s.u0, width, height);
            ctx.beginPath();
            ctx.moveTo(p[0], p[1]);
            for (let k = 1; k <= steps; k += 1) {
              p = figurePoint(fig, s.u0 + ((s.u1 - s.u0) * k) / steps, width, height);
              ctx.lineTo(p[0], p[1]);
            }
            ctx.strokeStyle = colors[Math.min(BINS - 1, Math.floor(((s.u1 / 900) % 1) * (BINS - 1)))];
            ctx.stroke();
          }
          if (s.phase === "fade" && dt > 0) {
            ctx.fillStyle = `rgba(0,0,0,${(1 - Math.exp((Math.log(0.02) * dt) / s.fadeS)).toFixed(3)})`;
            ctx.fillRect(0, 0, width, height);
          }
        },
      };
    },
  });
}
