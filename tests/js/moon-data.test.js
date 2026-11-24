import { test } from "node:test";
import assert from "node:assert/strict";
import { statSync, readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);

test("the moon photo exists, is a JPEG under 150 KB, and is about 600 px", () => {
  const bytes = readFileSync(new URL("moon.jpg", site));
  assert.ok(statSync(new URL("moon.jpg", site)).size < 150 * 1024);
  assert.equal(bytes[0], 0xff);
  assert.equal(bytes[1], 0xd8);
  // the first SOF marker carries the size
  let i = 2;
  let size = null;
  while (i < bytes.length) {
    if (bytes[i] !== 0xff) break;
    const marker = bytes[i + 1];
    const len = bytes.readUInt16BE(i + 2);
    if (marker >= 0xc0 && marker <= 0xc2) {
      size = { h: bytes.readUInt16BE(i + 5), w: bytes.readUInt16BE(i + 7) };
      break;
    }
    i += 2 + len;
  }
  assert.ok(size, "found the frame header");
  assert.ok(size.w >= 500 && size.w <= 700 && size.w === size.h, `${size.w}x${size.h}`);
});

test("the credit note names the source, the credit and the license wording", () => {
  const note = readFileSync(new URL("MOON-CREDIT.md", site), "utf8");
  assert.match(note, /svs\.gsfc\.nasa\.gov\/4236/);
  assert.match(note, /Scientific Visualization Studio/);
  assert.match(note, /Ernie Wright/);
  assert.match(note, /public domain/i);
  assert.ok(!/[\u2013\u2014]/.test(note), "no dashes");
});
