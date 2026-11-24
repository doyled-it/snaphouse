// Pure rules for the Harmonograph style: a two-pendulum figure (each axis is the sum
// of two decaying sines) drawn over about a minute, held, faded, then replaced by a
// new random figure. `createHarmonograph` is the phase clock: each advance(dt) says
// what segment of the figure to draw now. Random is injected.

export const HARMONO = Object.freeze({
  width: 400,
  height: 240,
  drawS: 60,
  holdS: 20,
  fadeS: 6,
  // pendulum amplitudes as a fraction of the width (x) or height (y)
  ax1: 0.2125,
  ax2: 0.1125,
  ay1: 0.2292,
  ay2: 0.125,
  dayAlpha: 0.6,
  nightAlpha: 0.3,
  maxFps: 15,
});

const END_ENVELOPE = 0.06;

export function figureEnd(fig) {
  return Math.log(1 / END_ENVELOPE) / fig.d;
}

export function newFigure(random) {
  const r = [2, 3, 3, 4, 5][Math.floor(random() * 5)];
  const q = [1, 2, 2, 3][Math.floor(random() * 4)];
  const jitter = () => (random() - 0.5) * 0.02;
  const fig = {
    f1: r + jitter(),
    f2: q + jitter(),
    f3: q + jitter(),
    f4: r + jitter(),
    p1: random() * 6,
    p2: random() * 6,
    p3: random() * 6,
    p4: random() * 6,
    d: 0.0045 + random() * 0.003,
  };
  fig.endU = figureEnd(fig);
  return fig;
}

export function figurePoint(fig, u, w, h) {
  const e = Math.exp(-fig.d * u);
  const s = u * 0.1;
  return [
    w / 2 + e * (HARMONO.ax1 * w * Math.sin(fig.f1 * s + fig.p1) + HARMONO.ax2 * w * Math.sin(fig.f2 * s + fig.p2)),
    h / 2 + e * (HARMONO.ay1 * h * Math.sin(fig.f3 * s + fig.p3) + HARMONO.ay2 * h * Math.sin(fig.f4 * s + fig.p4)),
  ];
}

export function createHarmonograph({ random, cfg = HARMONO }) {
  let fig = null;
  let phase = "draw";
  let t = 0; // seconds into the phase
  let u = 0;

  function fresh() {
    fig = newFigure(random);
    phase = "draw";
    t = 0;
    u = 0;
  }

  return {
    figure: () => fig,
    u: () => u,
    // Move the clock on by dt seconds. u0..u1 is the stretch of the figure to draw
    // now (it can be set in any phase: the last bit of the draw lands in the frame that
    // enters the hold). `fresh` is true on the call that starts a figure.
    advance(dt) {
      let isFresh = false;
      if (fig === null) {
        fresh();
        isFresh = true;
      }
      let u0 = u;
      let left = Math.max(0, dt);
      while (left > 0) {
        if (phase === "draw") {
          const need = cfg.drawS - t;
          if (left < need) {
            t += left;
            u = (fig.endU * t) / cfg.drawS;
            left = 0;
          } else {
            left -= need;
            u = fig.endU;
            phase = "hold";
            t = 0;
          }
        } else if (phase === "hold") {
          const need = cfg.holdS - t;
          if (left < need) {
            t += left;
            left = 0;
          } else {
            left -= need;
            phase = "fade";
            t = 0;
          }
        } else {
          const need = cfg.fadeS - t;
          if (left < need) {
            t += left;
            left = 0;
          } else {
            left -= need;
            fresh();
            isFresh = true;
            u0 = 0;
          }
        }
      }
      return { phase, u0, u1: u, fresh: isFresh, fadeS: cfg.fadeS };
    },
  };
}
