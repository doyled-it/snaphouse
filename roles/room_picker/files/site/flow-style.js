// The Flow field screensaver style: fine threads of light following a slowly changing
// noise field, leaving fading trails, tinted by the album hues. Trails are kept by
// fading the canvas a little each frame and drawing only this frame's short segments,
// batched into one stroke per color. Night: fewer, fainter threads. Rules in
// flow-model.js, loop and hues in canvas-style.js.

import { createCanvasStyle } from "./canvas-style.js";
import { buildColors, lerp, rgba } from "./gen-color.js";
import { FLOW, activeCount, createFlow, stepFlow, trailAlpha, trailFade } from "./flow-model.js";

export function createFlowStyle(env) {
  return createCanvasStyle(env, {
    width: FLOW.width,
    height: FLOW.height,
    fps: () => FLOW.maxFps,
    make({ ctx, width, height, random }) {
      const flow = createFlow({ width, height, random });
      let t = random() * 100;
      let styles = [];
      let keyVersion = -1;
      let keyNight = -1;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, width, height);
      ctx.lineWidth = 0.7;
      return {
        frame(dt, view) {
          t += dt * FLOW.timeRate;
          const active = activeCount(view.night);
          stepFlow(flow, t, dt, active, random);
          ctx.fillStyle = `rgba(0,0,0,${trailFade(view.night, dt).toFixed(3)})`;
          ctx.fillRect(0, 0, width, height);
          if (view.hueVersion !== keyVersion || view.night !== keyNight) {
            keyVersion = view.hueVersion;
            keyNight = view.night;
            const a = trailAlpha(view.night);
            styles = buildColors(view.hues, FLOW.bins, lerp(0.78, 0.5, view.night)).map((c) => rgba(c, a));
          }
          for (let b = 0; b < FLOW.bins; b += 1) {
            ctx.strokeStyle = styles[b];
            ctx.beginPath();
            for (let i = b; i < active; i += FLOW.bins) {
              ctx.moveTo(flow.px[i], flow.py[i]);
              ctx.lineTo(flow.x[i], flow.y[i]);
            }
            ctx.stroke();
          }
        },
      };
    },
  });
}
