import { test } from "node:test";
import assert from "node:assert/strict";
import {
  extractHues,
  DEFAULT_HUES,
  chromaScale,
  applyCoverColor,
  loadCoverHues,
  swapImage,
} from "../../roles/room_picker/files/site/cover-color.js";

function solid(r, g, b, a, count) {
  const out = new Uint8ClampedArray(count * 4);
  for (let i = 0; i < count; i += 1) {
    out[i * 4] = r;
    out[i * 4 + 1] = g;
    out[i * 4 + 2] = b;
    out[i * 4 + 3] = a;
  }
  return out;
}

function mix(...parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8ClampedArray(total);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

function hueDist(a, b) {
  const d = Math.abs(a - b) % 360;
  return Math.min(d, 360 - d);
}

test("a red cover gives a red h1 (about 29 degrees) and chroma above 0.5", () => {
  const { h1, chroma } = extractHues(solid(220, 30, 30, 255, 1024));
  assert.ok(hueDist(h1, 29) < 8, `h1 was ${h1}`);
  assert.ok(chroma > 0.5, `chroma was ${chroma}`);
});

test("a two-color cover gives h1 for the larger area and a distinct h2", () => {
  const { h1, h2 } = extractHues(mix(solid(220, 30, 30, 255, 700), solid(30, 60, 220, 255, 300)));
  assert.ok(hueDist(h1, 29) < 10, `h1 was ${h1}`);
  assert.ok(hueDist(h1, h2) >= 40, `h2 was ${h2}`);
});

test("a single-hue cover gets h2 = h1 + 30", () => {
  const { h1, h2 } = extractHues(solid(220, 30, 30, 255, 1024));
  assert.ok(hueDist((h1 + 30) % 360, h2) < 1e-9);
});

test("near-black and near-white pixels are ignored", () => {
  const { h1 } = extractHues(mix(solid(255, 255, 255, 255, 512), solid(0, 160, 150, 255, 512)));
  assert.ok(hueDist(h1, 190) < 15, `h1 was ${h1}`);
  const dark = extractHues(mix(solid(0, 0, 0, 255, 900), solid(0, 160, 150, 255, 124)));
  assert.ok(hueDist(dark.h1, 190) < 15);
});

test("greyscale gives DEFAULT_HUES with chroma 0", () => {
  const got = extractHues(mix(solid(120, 120, 120, 255, 500), solid(60, 60, 60, 255, 500)));
  assert.deepEqual(got, DEFAULT_HUES);
  assert.equal(got.chroma, 0);
});

test("fully transparent, empty and 1-pixel inputs give DEFAULT_HUES, no NaN", () => {
  const inputs = [solid(220, 30, 30, 0, 100), new Uint8ClampedArray(0), solid(220, 30, 30, 255, 1)];
  for (const input of inputs) {
    const got = extractHues(input);
    assert.deepEqual(got, DEFAULT_HUES);
    for (const v of Object.values(got)) assert.ok(Number.isFinite(v));
  }
});

test("chromaScale(0) is 0.15 and chromaScale(1) is 1", () => {
  assert.equal(chromaScale(0), 0.15);
  assert.equal(chromaScale(1), 1);
});

test("loadCoverHues resolves DEFAULT_HUES when the image errors or the canvas throws", async () => {
  const badImage = () => {
    const img = {};
    Object.defineProperty(img, "src", {
      set() {
        setTimeout(() => img.onerror && img.onerror(new Error("x")), 0);
      },
    });
    return img;
  };
  const okImage = () => {
    const img = { width: 8, height: 8 };
    Object.defineProperty(img, "src", {
      set() {
        setTimeout(() => img.onload && img.onload(), 0);
      },
    });
    return img;
  };
  const throwingCanvas = () => ({
    width: 0,
    height: 0,
    getContext: () => ({
      drawImage() {},
      getImageData() {
        throw new Error("tainted");
      },
    }),
  });
  assert.deepEqual(
    await loadCoverHues("/cover/a", { makeImage: badImage, makeCanvas: throwingCanvas }),
    DEFAULT_HUES,
  );
  assert.deepEqual(
    await loadCoverHues("/cover/a", { makeImage: okImage, makeCanvas: throwingCanvas }),
    DEFAULT_HUES,
  );
});

test("loadCoverHues extracts from the canvas pixels on success", async () => {
  const okImage = () => {
    const img = { width: 8, height: 8 };
    Object.defineProperty(img, "src", {
      set() {
        setTimeout(() => img.onload && img.onload(), 0);
      },
    });
    return img;
  };
  const canvas = () => ({
    width: 0,
    height: 0,
    getContext: () => ({
      drawImage() {},
      getImageData: () => ({ data: solid(220, 30, 30, 255, 1024) }),
    }),
  });
  const got = await loadCoverHues("/cover/a", { makeImage: okImage, makeCanvas: canvas });
  assert.ok(hueDist(got.h1, 29) < 8);
});

test("applyCoverColor sets the three properties on a stub root", () => {
  const set = {};
  const root = { style: { setProperty: (k, v) => (set[k] = v) } };
  applyCoverColor(root, { h1: 29.5, h2: 59.5, chroma: 1 });
  assert.equal(set["--h"], "29.5");
  assert.equal(set["--h2"], "59.5");
  assert.equal(set["--k"], "1");
  let calls = 0;
  const root2 = { style: { setProperty: () => (calls += 1) } };
  applyCoverColor(root2, DEFAULT_HUES);
  applyCoverColor(root2, DEFAULT_HUES);
  assert.equal(calls, 3);
});

test("swapImage sets src after decode, and still sets it when decode fails", async () => {
  const mk = (fail) => () => ({
    set src(v) {
      this._src = v;
    },
    decode: () => (fail ? Promise.reject(new Error("x")) : Promise.resolve()),
  });
  for (const fail of [false, true]) {
    const img = {};
    await swapImage(img, "/cover/b", { makeImage: mk(fail) });
    assert.equal(img.src, "/cover/b");
  }
});
