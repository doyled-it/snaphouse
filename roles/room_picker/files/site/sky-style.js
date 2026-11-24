// The Night sky screensaver style: stars turning about a pole above the screen at the
// real rate (15 degrees an hour, from the wall clock), a shooting star every few
// minutes. Day is deep blue; night is black with fewer, dimmer, warmer stars and rarer
// shooting stars. The stars barely move, so it redraws a few times a second, and only
// speeds up while a shooting star is crossing. Rules in sky-model.js.

import { createCanvasStyle } from "./canvas-style.js";
import { lerp } from "./gen-color.js";
import { SKY, makeStars, starPosition, starCount, rotationAt, createShooter } from "./sky-model.js";

const SKY_TOP = [5, 11, 30];
const SKY_BOTTOM = [19, 34, 74];
const DAY_STAR = [[232, 238, 255], [255, 225, 190], [190, 210, 255]];
const NIGHT_STAR = [216, 201, 176];

const mix = (a, b, t) => a.map((v, i) => Math.round(lerp(v, b[i], t)));
const rgb = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;

export function createSkyStyle(env) {
  let shooting = false;
  return createCanvasStyle(env, {
    width: SKY.width,
    height: SKY.height,
    fps: () => (shooting ? SKY.shotFps : SKY.idleFps),
    make({ ctx, width, height, random }) {
      const stars = makeStars();
      const shooter = createShooter({ random });
      return {
        frame(dt, view) {
          const night = view.night;
          shooter.advance(dt, night);
          const shot = shooter.current();
          shooting = shot !== null;
          const ms = env.now();
          const angle = rotationAt(ms);

          const g = ctx.createLinearGradient(0, 0, 0, height);
          g.addColorStop(0, rgb(mix(SKY_TOP, [0, 0, 0], night)));
          g.addColorStop(1, rgb(mix(SKY_BOTTOM, [0, 0, 0], night)));
          ctx.globalAlpha = 1;
          ctx.fillStyle = g;
          ctx.fillRect(0, 0, width, height);

          const dim = lerp(1, 0.55, night);
          const seconds = ms / 1000;
          const n = starCount(night);
          for (let i = 0; i < n; i += 1) {
            const s = stars[i];
            const p = starPosition(s, angle);
            if (p.x < -2 || p.x > width + 2 || p.y < -2 || p.y > height + 2) continue;
            const twinkle = 0.8 + 0.2 * Math.sin(seconds * 1.3 + s.tw);
            ctx.globalAlpha = s.bright * twinkle * dim;
            ctx.fillStyle = rgb(mix(DAY_STAR[s.tint], NIGHT_STAR, night));
            ctx.beginPath();
            ctx.arc(p.x, p.y, s.size, 0, 7);
            ctx.fill();
          }

          if (shot) {
            const tail = ctx.createLinearGradient(shot.tailX, shot.tailY, shot.x, shot.y);
            tail.addColorStop(0, "rgba(255,255,255,0)");
            tail.addColorStop(1, `rgba(255,255,255,${shot.alpha.toFixed(3)})`);
            ctx.globalAlpha = 1;
            ctx.strokeStyle = tail;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(shot.tailX, shot.tailY);
            ctx.lineTo(shot.x, shot.y);
            ctx.stroke();
          }
          ctx.globalAlpha = 1;
        },
      };
    },
  });
}
