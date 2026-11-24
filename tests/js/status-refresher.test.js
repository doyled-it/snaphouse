import test from "node:test";
import assert from "node:assert/strict";
import { createStatusRefresher } from "../../roles/room_picker/files/site/status-refresher.js";

function make() {
  const sent = [];
  const clock = { t: 0 };
  let id = 100;
  const r = createStatusRefresher({
    send: () => {
      sent.push("GetStatus");
      return ++id;
    },
    now: () => clock.t,
  });
  return { r, sent, clock };
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

test("owns recognises the id of the request in flight, and only that", () => {
  const { r } = make();
  assert.equal(r.owns(101), false);
  r.want();
  assert.equal(r.owns(101), true);
  assert.equal(r.owns(102), false);
  r.answered();
  assert.equal(r.owns(101), false);
});

test("a request in flight under 5 s still coalesces", () => {
  const { r, sent, clock } = make();
  r.want();
  clock.t = 4999;
  r.want();
  assert.equal(sent.length, 1);
});

test("a request in flight past 5 s no longer blocks the next want", () => {
  const { r, sent, clock } = make();
  r.want();
  clock.t = 5001;
  r.want();
  assert.equal(sent.length, 2);
  assert.equal(r.owns(102), true);
  assert.equal(r.owns(101), false);
});

test("a lost request does not leave a trailing want behind after recovery", () => {
  const { r, sent, clock } = make();
  r.want();
  r.want();
  clock.t = 6000;
  r.want();
  assert.equal(sent.length, 2);
  r.answered();
  assert.equal(sent.length, 2);
});

test("rooms.js counts an error reply to the status request as answered", () => {
  const body = rooms.slice(rooms.indexOf("function onMessage("), rooms.indexOf("function clearStatusMessage"));
  assert.match(body, /statusRefresher\.owns\(one\.id\)[\s\S]*statusRefresher\.answered\(\)/);
});

const rooms = readFileSync(
  new URL("../../roles/room_picker/files/site/rooms.js", import.meta.url),
  "utf8",
);

test("rooms.js refreshes the status when the socket answers a control request", () => {
  const body = rooms.slice(rooms.indexOf("function onMessage("), rooms.indexOf("function clearStatusMessage"));
  assert.match(body, /statusRefresher\.answered\(\)/, "a status reply frees the slot");
  assert.match(body, /one\.id !== undefined[\s\S]*statusRefresher\.want\(\)/, "a control reply asks for a status");
});
