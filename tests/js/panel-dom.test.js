import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// A tiny stand-in for document.createElementNS: enough for panel-dom's icons.
function fakeNode() {
  const attrs = {};
  return {
    attrs,
    children: [],
    setAttribute(k, v) {
      attrs[k] = v;
    },
    appendChild(c) {
      this.children.push(c);
    },
  };
}
globalThis.document = { createElementNS: () => fakeNode() };

const { solidIcon } = await import("../../roles/room_picker/files/site/panel-dom.js");

test("solidIcon draws a filled shape with no stroke, as the mockup's transport icons are", () => {
  const svg = solidIcon("M7 4v16l13-8z", 44);
  assert.equal(svg.attrs.fill, "currentColor");
  assert.equal(svg.attrs.stroke, undefined);
  assert.equal(svg.attrs.width, "44");
  assert.equal(svg.children[0].attrs.d, "M7 4v16l13-8z");
});

test("the panel's play, pause, previous and next icons are drawn solid, never as outlines", () => {
  const src = readFileSync(new URL("../../roles/room_picker/files/site/panel-music.js", import.meta.url), "utf8");
  assert.doesNotMatch(src, /[^d]icon\(TRANSPORT\.(previous|next|play|pause)\b/);
  assert.match(src, /solidIcon\(TRANSPORT\.play\b/);
  assert.match(src, /solidIcon\(TRANSPORT\.previous\b/);
  assert.match(src, /solidIcon\(TRANSPORT\.next\b/);
});
