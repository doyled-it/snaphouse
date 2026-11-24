// The Planets screensaver style: the sun and the eight planets where they really are
// today, on evenly spaced tilted rings (not to scale), names beside them and the date
// in a corner. Real time, so it barely moves; redrawn once a second (faster only while
// the night fade runs). Night: names off, everything dimmed. Rules in planets-model.js.

import { createCanvasStyle } from "./canvas-style.js";
import { lerp } from "./gen-color.js";
import { ORRERY, PLANETS, DATE_SPOT, placeLabels, ringLayout, dateLabel, driftAt } from "./planets-model.js";

const BG_DAY = [10, 14, 24];
const FONT = '"Instrument Sans", system-ui, sans-serif';

export function createPlanetsStyle(env) {
  return createCanvasStyle(env, {
    width: ORRERY.width,
    height: ORRERY.height,
    fps: (night) => (night > 0 && night < 1 ? 8 : ORRERY.fps),
    make({ ctx, width, height }) {
      const rings = ringLayout();
      return {
        frame(dt, view) {
          const night = view.night;
          const dim = lerp(1, 0.5, night);
          const ms = env.now();
          ctx.font = `${ORRERY.fontPx}px ${FONT}`;
          const { dx, dy } = driftAt(ms);
          const cx = ORRERY.cx + dx;
          const cy = ORRERY.cy + dy;
          const spots = placeLabels({ ms, measure: (n) => ctx.measureText(n).width || n.length * 10, fontPx: ORRERY.fontPx, cx, cy });

          const bg = BG_DAY.map((v) => Math.round(lerp(v, 0, night)));
          ctx.globalAlpha = 1;
          ctx.fillStyle = `rgb(${bg[0]},${bg[1]},${bg[2]})`;
          ctx.fillRect(0, 0, width, height);

          const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, 40);
          glow.addColorStop(0, `rgba(255,240,200,${(0.95 * dim).toFixed(3)})`);
          glow.addColorStop(0.3, `rgba(255,200,110,${(0.6 * dim).toFixed(3)})`);
          glow.addColorStop(1, "rgba(0,0,0,0)");
          ctx.fillStyle = glow;
          ctx.fillRect(cx - 40, cy - 40, 80, 80);

          ctx.strokeStyle = `rgba(110,125,150,${lerp(0.3, 0.15, night).toFixed(3)})`;
          ctx.lineWidth = 1;
          for (const r of rings) {
            ctx.beginPath();
            ctx.ellipse(cx, cy, r.rx, r.ry, 0, 0, Math.PI * 2);
            ctx.stroke();
          }

          ctx.textBaseline = "middle";
          spots.forEach((s, i) => {
            const c = PLANETS[i].rgb;
            ctx.fillStyle = `rgb(${Math.round(c[0] * dim)},${Math.round(c[1] * dim)},${Math.round(c[2] * dim)})`;
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.size, 0, 7);
            ctx.fill();
          });
          const nameAlpha = 0.75 * (1 - night);
          if (nameAlpha > 0.01) {
            ctx.fillStyle = `rgba(165,175,195,${nameAlpha.toFixed(3)})`;
            for (const s of spots) ctx.fillText(s.name, s.label.x, s.label.y + s.label.h / 2);
          }

          const grey = Math.round(lerp(175, 90, night));
          ctx.fillStyle = `rgb(${grey},${grey + 5},${grey + 18})`;
          ctx.font = `20px ${FONT}`;
          ctx.fillText(dateLabel(new Date(ms)), DATE_SPOT.x + dx, DATE_SPOT.y + dy);
        },
      };
    },
  });
}
