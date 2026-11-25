// Pure rules for the Night sky style: a stable star field turning about a pole above
// the screen at the real rate of 15 degrees an hour, and the shooting star schedule.
// The field is generated from a seeded random, so the same stars show every time; the
// shooting star takes an injected random. The clock is the caller's.

import { lerp } from "./gen-color.js";

export const SKY = Object.freeze({
  width: 400,
  height: 240,
  poleX: 280,
  poleY: -130, // above the screen, so the stars sweep across it
  stars: 1400, // in a ring around the pole; about one in seven is on screen at a time
  nightStars: 650,
  seed: 2024,
  degPerHour: 15,
  idleFps: 5, // the stars barely move: a few redraws a second
  shotFps: 60, // only while a shooting star is crossing
  shotS: 1,
  dayDelayS: [90, 270], // a shooting star every few minutes
  nightDelayS: [600, 1200],
  shotAlpha: [0.9, 0.4], // day, night
});

// A small seeded generator (mulberry32).
export function seededRandom(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// The turn of the sky, in radians, at a clock time in ms. It increases the angle, which is clockwise on screen (y runs down).
export const rotationAt = (ms) => ((ms / 3600000) * SKY.degPerHour * Math.PI) / 180;

const FAR_CORNER = Math.max(
  Math.hypot(SKY.poleX, SKY.height - SKY.poleY),
  Math.hypot(SKY.width - SKY.poleX, SKY.height - SKY.poleY),
);

// Stars at uniform density over the ring between the nearest and farthest screen
// points from the pole. `tint` is 0 (white), 1 (warm) or 2 (cool blue).
export function makeStars(count = SKY.stars, seed = SKY.seed) {
  const random = seededRandom(seed);
  const near = -SKY.poleY;
  const stars = [];
  for (let i = 0; i < count; i += 1) {
    const r = Math.sqrt(near * near + random() * (FAR_CORNER * FAR_CORNER - near * near));
    const a = random() * Math.PI * 2;
    const size = random() ** 3 * 1.8 + 0.4;
    const bright = random() * 0.7 + 0.3;
    const roll = random();
    const tint = roll < 0.04 ? 1 : roll < 0.08 ? 2 : 0;
    stars.push({ r, a, size, bright, tint, tw: random() * 6 });
  }
  return stars;
}

export function starPosition(star, angle) {
  return { x: SKY.poleX + star.r * Math.cos(star.a + angle), y: SKY.poleY + star.r * Math.sin(star.a + angle) };
}

// How many of the stars to draw: fewer as the night mix (0 day, 1 night) rises.
export const starCount = (night) => Math.round(lerp(SKY.stars, SKY.nightStars, night));

// The shooting star: waits a random few minutes (longer at night), crosses in about a
// second, then waits again. `advance` is called with each frame's dt.
export function createShooter({ random }) {
  const delay = (night) => {
    const [a, b] = [lerp(SKY.dayDelayS[0], SKY.nightDelayS[0], night), lerp(SKY.dayDelayS[1], SKY.nightDelayS[1], night)];
    return a + random() * (b - a);
  };
  let wait = 15 + random() * 45;
  let shot = null;
  let elapsed = 0;
  let nightNow = 0;
  return {
    advance(dt, night) {
      nightNow = night;
      if (shot) {
        elapsed += dt;
        if (elapsed >= SKY.shotS) {
          shot = null;
          wait = delay(night);
        }
        return;
      }
      wait -= dt;
      if (wait <= 0) {
        elapsed = 0;
        shot = { x: SKY.width * (0.2 + random() * 0.5), y: SKY.height * (0.1 + random() * 0.3) };
      }
    },
    current() {
      if (!shot) return null;
      const k = elapsed / SKY.shotS;
      const x = shot.x + k * 130;
      const y = shot.y + k * 55;
      return { x, y, tailX: x - 45, tailY: y - 19, alpha: lerp(SKY.shotAlpha[0], SKY.shotAlpha[1], nightNow) * (1 - k) };
    },
  };
}
