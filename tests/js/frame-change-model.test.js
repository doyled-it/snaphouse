import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CHANGE_TIMEOUT_MS,
  DONE_MS,
  advance,
  inFlight,
  needsPng,
  pollMs,
  progressOf,
  settled,
  startChange,
  viewOf,
} from "../../roles/room_picker/files/site/frame-change-model.js";

const T0 = 1_000_000;
const lookup = (id) => (id === "a.jpg" ? { title: "Water Lilies", thumb: "/art-thumbs/a.jpg" } : null);
const ctx = { frameLabel: "Nightstand", lookup };
const pending = (extra = {}) => ({
  seq: 7, id: "a.jpg", since: "s", state: "changing", reason: null, ...extra,
});
const mirror = (extra = {}) => ({ updated_at: "t1", screen: "art", ...extra });

function sent(action = "next", seq = 7) {
  return startChange({ frameId: "nightstand", action, seq, targetId: null, nowMs: T0 });
}

test("a tap starts in the sent state with the frame name", () => {
  const view = viewOf(sent(), ctx);
  assert.equal(view.phase, "sent");
  assert.equal(view.text, "Sending to Nightstand...");
  assert.equal(view.busy, true);
  assert.equal(view.thumb, null);
  assert.equal(inFlight(sent()), true);
});

test("Show on the frame goes straight to changing with the known picture", () => {
  const change = startChange({ frameId: "nightstand", action: "show", seq: 7, targetId: "a.jpg", nowMs: T0 });
  const view = viewOf(change, ctx);
  assert.equal(view.phase, "changing");
  assert.equal(view.text, "Changing to Water Lilies");
  assert.equal(view.thumb, "/art-thumbs/a.jpg");
  assert.equal(view.progress, true);
});

test("a pending with the command's seq shows the target picture", () => {
  const change = advance(sent(), mirror({ pending: pending() }), T0 + 4000);
  const view = viewOf(change, ctx);
  assert.equal(view.phase, "changing");
  assert.equal(view.text, "Changing to Water Lilies");
  assert.equal(view.thumb, "/art-thumbs/a.jpg");
});

test("an id missing from the art index says the next picture, with no thumbnail", () => {
  const change = advance(sent(), mirror({ pending: pending({ id: "gone.jpg" }) }), T0 + 4000);
  const view = viewOf(change, ctx);
  assert.equal(view.text, "Changing to the next picture");
  assert.equal(view.thumb, null);
});

test("waiting names what the frame shows, per reason, and ends the flow", () => {
  const expect = {
    music: "Up next: Water Lilies. The frame shows music right now.",
    game: "Up next: Water Lilies. The frame shows the game right now.",
    pin: "Up next: Water Lilies. The frame shows the pinned screen right now.",
    settling: "Up next: Water Lilies. The frame shows music right now.",
  };
  for (const [reason, text] of Object.entries(expect)) {
    const change = advance(sent(), mirror({ pending: pending({ state: "waiting", reason }) }), T0 + 3000);
    const view = viewOf(change, ctx);
    assert.equal(view.phase, "waiting");
    assert.equal(view.text, text);
    assert.equal(view.ends, true);
    assert.equal(inFlight(change), false);
  }
});

test("failed says so and ends the flow", () => {
  const change = advance(sent(), mirror({ pending: pending({ state: "failed" }) }), T0 + 3000);
  const view = viewOf(change, ctx);
  assert.equal(view.text, "The frame could not show it. Try again.");
  assert.equal(inFlight(change), false);
});

test("applied_seq reaching the seq is done: Now showing for 3 s, then settled", () => {
  const changing = advance(sent(), mirror({ pending: pending() }), T0 + 4000);
  const done = advance(changing, mirror({ applied_seq: 7 }), T0 + 35_000);
  const view = viewOf(done, ctx);
  assert.equal(view.phase, "done");
  assert.equal(view.text, "Now showing Water Lilies");
  assert.equal(inFlight(done), false);
  assert.equal(settled(done, T0 + 35_000 + DONE_MS - 1), false);
  assert.equal(settled(done, T0 + 35_000 + DONE_MS), true);
});

test("done works when the pending was never seen between two reads", () => {
  const done = advance(sent(), mirror({ applied_seq: 9 }), T0 + 20_000);
  assert.equal(viewOf(done, ctx).phase, "done");
});

test("a pending from an earlier command is ignored", () => {
  const change = advance(sent("next", 8), mirror({ pending: pending({ seq: 7 }) }), T0 + 4000);
  assert.equal(viewOf(change, ctx).phase, "sent");
});

test("no confirmation in 90 s times out with the plain message, then settles", () => {
  const early = advance(sent(), mirror(), T0 + CHANGE_TIMEOUT_MS - 1);
  assert.equal(viewOf(early, ctx).phase, "sent");
  const late = advance(sent(), mirror(), T0 + CHANGE_TIMEOUT_MS);
  const view = viewOf(late, ctx);
  assert.equal(view.phase, "timeout");
  assert.equal(view.text, "The frame did not confirm the change. It may be busy or offline.");
  assert.equal(inFlight(late), false);
  assert.equal(settled(late, T0 + CHANGE_TIMEOUT_MS + 60_000), true);
});

test("a frame that predates pending and applied_seq takes the 90 s path", () => {
  let change = sent();
  for (let t = 2000; t < CHANGE_TIMEOUT_MS; t += 2000) {
    change = advance(change, { updated_at: "t0", screen: "art" }, T0 + t);
    assert.equal(viewOf(change, ctx).phase, "sent");
  }
  change = advance(change, { updated_at: "t0", screen: "art" }, T0 + CHANGE_TIMEOUT_MS);
  assert.equal(viewOf(change, ctx).phase, "timeout");
});

test("a missing mirror read leaves the state alone until the timeout", () => {
  const change = advance(sent(), null, T0 + 5000);
  assert.equal(viewOf(change, ctx).phase, "sent");
});

test("a second tap restarts the flow and the first seq's confirmation does not end it", () => {
  const first = sent("next", 7);
  const second = startChange({ frameId: "nightstand", action: "next", seq: 8, targetId: null, nowMs: T0 + 10_000 });
  const after = advance(second, mirror({ applied_seq: 7 }), T0 + 12_000);
  assert.equal(viewOf(after, ctx).phase, "sent");
  assert.equal(inFlight(after), true);
  const done = advance(after, mirror({ applied_seq: 8 }), T0 + 14_000);
  assert.equal(viewOf(done, ctx).phase, "done");
  assert.notEqual(first.seq, second.seq);
});

test("a reply without a seq can only time out", () => {
  const change = startChange({ frameId: "nightstand", action: "next", seq: null, targetId: null, nowMs: T0 });
  const same = advance(change, mirror({ applied_seq: 99, pending: pending() }), T0 + 4000);
  assert.equal(viewOf(same, ctx).phase, "sent");
  assert.equal(viewOf(advance(same, mirror(), T0 + CHANGE_TIMEOUT_MS), ctx).phase, "timeout");
});

test("waiting and failed messages settle after they have been shown", () => {
  const change = advance(sent(), mirror({ pending: pending({ state: "failed" }) }), T0 + 3000);
  assert.equal(settled(change, T0 + 3500), false);
  assert.equal(settled(change, T0 + 3000 + 8000), true);
});

test("polling is every 2 s only while a change is in flight", () => {
  assert.equal(pollMs(sent(), 30_000), 2000);
  assert.equal(pollMs(null, 30_000), 30_000);
  const done = advance(sent(), mirror({ applied_seq: 7 }), T0 + 5000);
  assert.equal(pollMs(done, 30_000), 30_000);
});

test("the image is fetched only when updated_at changes", () => {
  assert.equal(needsPng("t1", mirror({ updated_at: "t1" })), false);
  assert.equal(needsPng("t1", mirror({ updated_at: "t2" })), true);
  assert.equal(needsPng(null, mirror()), true);
  assert.equal(needsPng("t1", null), false);
});

test("the copy has no em dash", () => {
  const texts = ["sent", "timeout"].map((phase) =>
    viewOf({ ...sent(), phase }, ctx).text
  );
  for (const text of texts) assert.ok(!text.includes("—"));
});

test("progress is a gentle fill over the 30 s push, from when the frame took the command", () => {
  const sentOnly = sent();
  assert.equal(progressOf(sentOnly, T0 + 5000), 0);
  const changing = advance(sent(), mirror({ pending: pending() }), T0 + 4000);
  assert.equal(progressOf(changing, T0 + 4000), 0);
  const half = progressOf(changing, T0 + 4000 + 17_500);
  assert.ok(half > 0.4 && half < 0.6, String(half));
  assert.equal(progressOf(changing, T0 + 4000 + 120_000), 0.95);
  const show = startChange({ frameId: "nightstand", action: "show", seq: 7, targetId: "a.jpg", nowMs: T0 });
  assert.ok(progressOf(show, T0 + 10_000) > 0);
});
