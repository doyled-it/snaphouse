import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);
const css = readFileSync(new URL("panel.css", site), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

// [{ selector, body }] for top-level rules, flattening @media and @keyframes bodies.
function rules(text) {
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(text))) out.push({ selector: m[1].trim(), body: m[2] });
  return out;
}
const all = rules(css).filter((r) => !/^\d+%|^from|^to$/.test(r.selector));

test("every panel rule is scoped under html[data-panel]", () => {
  for (const { selector } of all) {
    for (const part of selector.split(",")) {
      assert.match(part.trim(), /^html\[data-panel\]/, `unscoped selector: ${part.trim()}`);
    }
  }
});

test("no panel text is under 20px, except the rail labels (17px) and tile lines (19px), the two numbers the design fixes", () => {
  for (const { selector, body } of all) {
    for (const m of body.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
      const px = Number(m[1]);
      if (px >= 20) continue;
      const allowed = (/\.p-rail-label/.test(selector) && px === 17) || (/\.p-tile-line/.test(selector) && px === 19);
      assert.ok(allowed, `${selector} uses ${px}px`);
    }
  }
});

test("the rail is 96px wide and the strip 48px tall", () => {
  const rail = all.find((r) => /\.p-rail$/.test(r.selector));
  assert.match(rail.body, /width:\s*96px/);
  const strip = all.find((r) => /\.p-strip$/.test(r.selector));
  assert.match(strip.body, /height:\s*48px/);
});

test("style.css keeps the existing landscape block untouched", () => {
  const style = readFileSync(new URL("style.css", site), "utf8");
  assert.match(style, /@media \(min-width: 720px\) and \(max-height: 540px\)/);
});

function rule(sel) {
  const found = all.find((r) => r.selector.split(",").some((p) => p.trim() === `html[data-panel] ${sel}`));
  assert.ok(found, `missing rule ${sel}`);
  return found.body;
}

test("Now playing sizes follow the design", () => {
  assert.match(rule(".p-now-art"), /width:\s*300px/);
  assert.match(rule(".p-now-art"), /height:\s*300px/);
  assert.match(rule(".p-now-title"), /font-size:\s*34px/);
  assert.match(rule(".p-now-title"), /-webkit-line-clamp:\s*2/);
  assert.match(rule(".p-now-artist"), /font-size:\s*24px/);
  assert.match(rule(".p-now-btn"), /height:\s*92px/);
  assert.match(rule(".p-now-rooms"), /height:\s*64px/);
  assert.match(rule(".p-now-paused"), /font-size:\s*20px/);
  assert.match(rule(".p-now[data-paused=\"1\"] .p-now-art"), /opacity:/);
});

test("ambient Now Playing sizes follow the design", () => {
  assert.match(rule(".p-amb-art"), /width:\s*340px/);
  assert.match(rule(".p-amb-title"), /font-size:\s*40px/);
  assert.match(rule(".p-amb-artist"), /font-size:\s*28px/);
});

test("settings rows are 72px tall and their buttons at least 72px square", () => {
  assert.match(rule(".p-srow"), /height:\s*72px/);
  const btn = rule(".p-sbtn");
  assert.match(btn, /width:\s*72px/);
  assert.match(btn, /height:\s*72px/);
  assert.match(rule(".p-schip"), /height:\s*72px/);
});

test("the settings pages and the screensaver preview are styled under html[data-panel]", () => {
  for (const sel of [".p-sset", ".p-hbar", ".p-tval", ".p-schips", ".p-schip.soon", ".p-saver-preview", ".p-sset-status"]) {
    assert.ok(typeof rule(sel) === "string", `missing rule for ${sel}`);
  }
});
