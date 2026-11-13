import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyMachines,
  healthMessage,
  speakerBanner,
} from "../../roles/room_picker/files/site/health-model.js";

function ok(host, commit = "abc") {
  return { host, installed: true, status: { verify: "pass", commit, main_commit: commit } };
}

test("a machine the inventory marks not installed and silent is Not set up yet", () => {
  const out = classifyMachines([ok("house"), { host: "panel", installed: false, status: null }]);
  assert.deepEqual(out, { troubled: [], notSetUp: ["panel"] });
});

test("an installed machine that is silent is troubled, on every load", () => {
  const out = classifyMachines([{ host: "office", installed: true, status: null }]);
  assert.deepEqual(out, { troubled: ["office"], notSetUp: [] });
});

test("a missing installed field reads as installed", () => {
  const out = classifyMachines([{ host: "office", status: null }]);
  assert.deepEqual(out.troubled, ["office"]);
});

test("a failing verify or a commit behind main is troubled", () => {
  const out = classifyMachines([
    { host: "a", installed: true, status: { verify: "fail", commit: "x", main_commit: "x" } },
    { host: "b", installed: true, status: { verify: "pass", commit: "old", main_commit: "new" } },
    ok("c"),
  ]);
  assert.deepEqual(out, { troubled: ["a", "b"], notSetUp: [] });
});

test("a not-installed machine that does report is judged like any other", () => {
  const out = classifyMachines([
    { host: "panel", installed: false, status: { verify: "fail", commit: "x", main_commit: "x" } },
  ]);
  assert.deepEqual(out, { troubled: ["panel"], notSetUp: [] });
});

test("classifyMachines ignores a second argument (no page-session memory)", () => {
  assert.equal(classifyMachines.length, 1);
});

test("healthMessage is null when nothing is troubled", () => {
  assert.equal(healthMessage([]), null);
});

test("healthMessage is plain and never names a machine", () => {
  const msg = healthMessage(["office", "nightstand-frame"]);
  assert.match(msg, /A part of the house isn't responding/);
  assert.doesNotMatch(msg, /office|nightstand|house\b.*house|attention/i);
  assert.equal(msg, healthMessage(["house"]));
});

test("speakerBanner says it plainly only when the audio server is down", () => {
  assert.equal(speakerBanner("down"), "Can't reach the speakers right now. Retrying.");
  assert.equal(speakerBanner("up"), null);
  assert.equal(speakerBanner(undefined), null);
});

// Plain-language sweep: the words a person should never read on the page.
import { readFileSync } from "node:fs";
const SITE = new URL("../../roles/room_picker/files/site/", import.meta.url);

test("user-facing copy avoids jargon", () => {
  const banned = /snapserver|pairing service|Live updates|needs attention|"Offline"|>Rotation<|Show in library|Delete this piece|>Next piece</;
  for (const file of ["index.html", "rooms.js", "rooms-model.js", "pair-controller.js", "devices-controller.js"]) {
    const text = readFileSync(new URL(file, SITE), "utf8")
      .split("\n")
      .filter((line) => !/^\s*(\/\/|\*|<!--)/.test(line))
      .join("\n");
    assert.doesNotMatch(text, banned, file);
  }
});
