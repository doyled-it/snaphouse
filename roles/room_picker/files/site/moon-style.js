// The Moon screensaver style: a full-moon photograph (moon.jpg, see MOON-CREDIT.md)
// shaded to tonight's phase with a soft terminator, the phase name, percent lit and
// days to the next full or new moon beside it, the whole composition drifting slowly
// so nothing burns in. The shading is done once per phase pixel on an offscreen canvas
// and the screen is redrawn only when something visible changed (drift pixel, phase
// pixel, text, night mix), so the loop is cheap. Rules in moon-model.js.

import { createCanvasStyle } from "./canvas-style.js";
import { lerp } from "./gen-color.js";
import { MOON, phaseAt, terminatorU, shadeAt, driftAt, textLines } from "./moon-model.js";
import { seededRandom } from "./sky-model.js";

const PHOTO = "moon.jpg";
const S = MOON.photoPx;
const FONT = '"Instrument Sans", system-ui, sans-serif';
const BACKDROP = Array.from({ length: 70 }, ((random) => () => [random() * MOON.width, random() * MOON.height, random()])(seededRandom(11)));

export function createMoonStyle(env) {
  return createCanvasStyle(env, {
    width: MOON.width,
    height: MOON.height,
    fps: (night) => (night > 0 && night < 1 ? 10 : MOON.fps),
    make({ ctx, width, height }) {
      const shaded = env.doc.createElement("canvas");
      shaded.width = S;
      shaded.height = S;
      const sctx = shaded.getContext("2d");
      let base = null; // the photo's pixels
      let shadedKey = null;
      let drawnKey = null;

      const img = env.doc.createElement("img");
      img.onload = () => {
        const probe = env.doc.createElement("canvas");
        probe.width = S;
        probe.height = S;
        const pctx = probe.getContext("2d");
        pctx.drawImage(img, 0, 0, S, S);
        base = pctx.getImageData(0, 0, S, S).data;
        shadedKey = null;
        drawnKey = null;
      };
      img.src = PHOTO;

      // Multiply the photo by the phase shading, once per phase pixel.
      function reshade(phase) {
        const out = sctx.createImageData(S, S);
        const rImg = MOON.discFraction * S;
        for (let y = 0; y < S; y += 1) {
          const v = (y + 0.5 - S / 2) / rImg;
          for (let x = 0; x < S; x += 1) {
            const u = (x + 0.5 - S / 2) / rImg;
            const k = u * u + v * v > 1.02 ? 1 : shadeAt(u, v, phase);
            const i = (y * S + x) * 4;
            out.data[i] = base[i] * k;
            out.data[i + 1] = base[i + 1] * k;
            out.data[i + 2] = base[i + 2] * k;
            out.data[i + 3] = 255;
          }
        }
        sctx.putImageData(out, 0, 0);
      }

      return {
        frame(dt, view) {
          const ms = env.now();
          const phase = phaseAt(ms);
          const night = view.night;
          const pos = driftAt(ms);
          const cx = Math.round(pos.cx);
          const cy = Math.round(pos.cy);
          const lines = textLines(ms);
          const phaseKey = `${phase < 0.5 ? "w" : "x"}${Math.round(terminatorU(0, phase) * MOON.radius)}`;
          const key = [base ? 1 : 0, phaseKey, cx, cy, lines.name, lines.lit, lines.next, night.toFixed(3)].join("|");
          if (key === drawnKey) return;
          drawnKey = key;
          if (base && shadedKey !== phaseKey) {
            shadedKey = phaseKey;
            reshade(phase);
          }

          ctx.globalCompositeOperation = "source-over";
          ctx.globalAlpha = 1;
          ctx.fillStyle = "#000";
          ctx.fillRect(0, 0, width, height);
          ctx.fillStyle = `rgb(${Math.round(lerp(230, 200, night))},${Math.round(lerp(235, 190, night))},${Math.round(lerp(255, 175, night))})`;
          for (const [x, y, b] of BACKDROP) {
            ctx.globalAlpha = b * lerp(0.6, 0.25, night);
            ctx.fillRect(x, y, 1.5, 1.5);
          }

          if (base) {
            // additive, so the photo's black corners add nothing to the backdrop stars
            ctx.globalCompositeOperation = "lighter";
            ctx.globalAlpha = lerp(1, 0.45, night);
            ctx.drawImage(shaded, cx - S / 2, cy - S / 2, S, S);
            ctx.globalCompositeOperation = "source-over";
          }

          ctx.globalAlpha = 1;
          ctx.textBaseline = "alphabetic";
          const tx = Math.round(pos.textX);
          const ty = cy;
          ctx.fillStyle = night > 0.5 ? "rgb(122,108,92)" : `rgb(${Math.round(lerp(236, 122, night * 2))},${Math.round(lerp(238, 108, night * 2))},${Math.round(lerp(244, 92, night * 2))})`;
          ctx.font = `600 34px ${FONT}`;
          ctx.fillText(lines.name, tx, ty);
          ctx.fillStyle = night > 0.5 ? "rgb(96,87,76)" : `rgb(${Math.round(lerp(170, 96, night * 2))},${Math.round(lerp(178, 87, night * 2))},${Math.round(lerp(195, 76, night * 2))})`;
          ctx.font = `24px ${FONT}`;
          ctx.fillText(lines.lit, tx, ty + 38);
          ctx.fillText(lines.next, tx, ty + 72);
        },
      };
    },
  });
}
