// The page loads nothing off the LAN, ships one dark theme, and vendors its
// font. These read the site files as text.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SITE = fileURLToPath(new URL("../../roles/room_picker/files/site/", import.meta.url));
const css = readFileSync(path.join(SITE, "style.css"), "utf8");
const html = readFileSync(path.join(SITE, "index.html"), "utf8");

test("style.css and index.html make no external requests", () => {
  assert.doesNotMatch(css, /url\(\s*["']?https?:/);
  assert.doesNotMatch(css, /@import/);
  assert.doesNotMatch(html, /<link[^>]*href="https?:/);
  assert.doesNotMatch(html, /<script[^>]*src="https?:/);
});

test("every url() font in style.css exists on disk", () => {
  const urls = [...css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g)].map((m) => m[1]);
  const fonts = urls.filter((u) => u.endsWith(".woff2"));
  assert.ok(fonts.length >= 2, "expected the latin and latin-ext faces");
  for (const u of fonts) assert.ok(existsSync(path.join(SITE, u)), `${u} is missing`);
});

test("style.css is dark only", () => {
  assert.doesNotMatch(css, /prefers-color-scheme/);
  assert.match(css, /color-scheme:\s*dark\s*;/);
});

test("index.html has one dark theme-color meta", () => {
  const metas = html.match(/<meta[^>]*name="theme-color"[^>]*>/g) ?? [];
  assert.equal(metas.length, 1);
  assert.doesNotMatch(metas[0], /media=/);
  assert.match(metas[0], /content="#1[0-9a-fA-F]{5}"/);
});

test("fonts directory carries OFL.txt, SOURCE.md and no Hanken files", () => {
  const files = readdirSync(path.join(SITE, "fonts"));
  assert.ok(files.includes("OFL.txt"));
  assert.ok(files.includes("SOURCE.md"));
  assert.ok(files.includes("instrument-sans-latin.woff2"));
  assert.ok(files.includes("instrument-sans-latin-ext.woff2"));
  assert.equal(files.filter((f) => /hanken/i.test(f)).length, 0);
  assert.match(readFileSync(path.join(SITE, "fonts", "OFL.txt"), "utf8"), /Instrument Sans/);
});

// --- Dusk styling (Task 5) ---------------------------------------------------

function blockAfter(source, header) {
  const start = source.indexOf(header);
  assert.ok(start >= 0, `${header} not found`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}" && --depth === 0) return source.slice(open + 1, i);
  }
  throw new Error(`${header} is unbalanced`);
}

test("every transition and animation is covered by reduced motion", () => {
  const block = blockAfter(css, "@media (prefers-reduced-motion: reduce)");
  assert.match(block, /\*,\s*\*::before,\s*\*::after/);
  assert.match(block, /transition:\s*none\s*!important/);
  assert.match(block, /animation:\s*none\s*!important/);
  assert.match(block, /:root\s*\{[^}]*transition:\s*none/);
});

test("kiosk block drops backdrop-filter", () => {
  const block = blockAfter(css, "@media (min-width: 720px) and (max-height: 540px)");
  assert.match(block, /backdrop-filter:\s*none/);
  assert.match(block, /-webkit-backdrop-filter:\s*none/);
});

test("no transition lasts 300ms or more except the hue drift and the switch spring", () => {
  const found = [];
  for (const m of css.matchAll(/(?:^|\n)\s*(transition|animation)(?:-duration)?:\s*([^;]+);/g)) {
    for (const d of m[2].matchAll(/(?<![\w.-])(\d*\.?\d+)(ms|s)\b/g)) {
      const ms = d[2] === "s" ? parseFloat(d[1]) * 1000 : parseFloat(d[1]);
      found.push({ ms, text: m[0].trim() });
    }
  }
  assert.ok(found.length > 5);
  for (const { ms, text } of found) {
    if (ms < 300) continue;
    const allowed = /--h\b/.test(text) || /tile-flash/.test(text) || /--spring/.test(text) || /\.45s/.test(text);
    assert.ok(allowed, `too long: ${text}`);
  }
});

test("no light-theme tokens remain", () => {
  assert.doesNotMatch(css, /#e9e7e0|#f4f2ec/i);
  assert.doesNotMatch(css, /--paper|--mat\b/);
});

test("press feedback is shared and hover is gated", () => {
  assert.match(css, /:active\s*\{[^}]*transform:\s*scale\(0?\.96\)/);
  assert.match(css, /touch-action:\s*manipulation/);
  assert.match(css, /@media \(hover: hover\)/);
  assert.match(css, /:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\)/);
});

test("the switch uses the spring behind a supports guard", () => {
  assert.match(css, /@supports \(transition-timing-function: linear\(0, 1\)\)/);
  assert.match(css, /transition:\s*transform \.45s var\(--spring\)|transition:\s*transform 0\.45s var\(--spring\)/);
});

test("panels carry the shared surface values", () => {
  const panel = blockAfter(css, "\n.panel");
  assert.match(panel, /border-radius:\s*24px/);
  assert.match(panel, /padding:\s*16px/);
  assert.match(panel, /backdrop-filter:\s*blur\(24px\) saturate\(1\.3\)/);
  assert.match(html, /id="now-playing" class="section panel"/);
  assert.match(html, /id="rooms-section" class="section panel"/);
});

test("section labels are sentence case, not tracked caps", () => {
  const rule = css.match(/\n\.section-h\s*\{([^}]*)\}/)[1];
  assert.doesNotMatch(rule, /text-transform/);
  assert.match(rule, /letter-spacing:\s*0\.01em/);
});

test("the top bar bleeds across the body gutters", () => {
  const rule = css.match(/\n\.topbar\s*\{([^}]*)\}/)[1];
  assert.match(rule, /margin:\s*0 calc\(-1 \* var\(--gutter-r\)\) 8px calc\(-1 \* var\(--gutter-l\)\)/);
  assert.match(rule, /padding:[^;]*var\(--gutter-r\)[^;]*var\(--gutter-l\)/);
});
