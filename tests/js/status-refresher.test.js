import test from "node:test";
import assert from "node:assert/strict";
import { createStatusRefresher } from "../../roles/room_picker/files/site/status-refresher.js";

function make() {
  const sent = [];
  const r = createStatusRefresher({ send: () => sent.push("GetStatus") });
  return { r, sent };
}

test("a first want sends one status request", () => {
  const { r, sent } = make();
  r.want();
  assert.equal(sent.length, 1);
});

test("wants while a request is in flight send nothing until it is answered, then one trailing request", () => {
  const { r, sent } = make();
  r.want();
  for (let i = 0; i < 10; i++) r.want();
  assert.equal(sent.length, 1);
  r.answered();
  assert.equal(sent.length, 2);
  r.answered();
  assert.equal(sent.length, 2);
});

test("an answer with no want waiting sends nothing and the next want sends again", () => {
  const { r, sent } = make();
  r.want();
  r.answered();
  assert.equal(sent.length, 1);
  r.want();
  assert.equal(sent.length, 2);
});

test("reset forgets an in-flight request lost with the socket", () => {
  const { r, sent } = make();
  r.want();
  r.want();
  r.reset();
  r.want();
  assert.equal(sent.length, 2);
});

test("a drag's last write is followed by a refresh after the earlier one answers", () => {
  const { r, sent } = make();
  r.want(); // reply to write 1
  r.want(); // reply to write 2 (last)
  r.answered(); // status for write 1 lands: stale value, trailing request goes
  assert.equal(sent.length, 2);
  r.answered(); // status after the last write
  assert.equal(sent.length, 2);
});

import { readFileSync } from "node:fs";

const rooms = readFileSync(
  new URL("../../roles/room_picker/files/site/rooms.js", import.meta.url),
  "utf8",
);

test("rooms.js refreshes the status when the socket answers a control request", () => {
  const body = rooms.slice(rooms.indexOf("function onMessage("), rooms.indexOf("function clearStatusMessage"));
  assert.match(body, /statusRefresher\.answered\(\)/, "a status reply frees the slot");
  assert.match(body, /one\.id !== undefined[\s\S]*statusRefresher\.want\(\)/, "a control reply asks for a status");
});
