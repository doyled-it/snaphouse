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

test("no panel text is under 20px, except the rail labels at 17px", () => {
  for (const { selector, body } of all) {
    for (const m of body.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
      const px = Number(m[1]);
      if (px >= 20) continue;
      assert.ok(/\.p-rail-label/.test(selector) && px === 17, `${selector} uses ${px}px`);
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
