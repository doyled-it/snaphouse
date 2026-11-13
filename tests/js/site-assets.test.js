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
