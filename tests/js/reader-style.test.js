import { test } from "node:test";
import assert from "node:assert/strict";
import { createFactsStyle, createQuotesStyle } from "../../roles/room_picker/files/site/reader-style.js";
import { FADE_MS, holdMs } from "../../roles/room_picker/files/site/reader-model.js";

const FACTS = [
  { text: "Fact one is odd.", source: "https://example.org/1" },
  { text: "Fact two is odder than the first.", source: "https://example.org/2" },
  { text: "Fact three.", source: "https://example.org/3" },
];
const QUOTES = [
  { text: "Quote A", author: "Ann", source: "Book, 1950" },
  { text: "Quote B", author: "Ann", source: "Book, 1951" },
  { text: "Quote C", author: "Bob", source: "Book, 1952" },
  { text: "Quote D", author: "Cy", source: "Book, 1953" },
];

function env(files) {
  let t = 0;
  const timers = [];
  const fetched = [];
  let id = 1;
  const doc = {
    createElement() {
      const classes = new Set();
      const props = {};
      const node = {
        children: [], textContent: "", className: "", hidden: false,
        style: { setProperty(k, v) { props[k] = v; }, props },
        classList: {
          add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
          toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
        },
        append(...c) { node.children.push(...c); },
      };
      return node;
    },
  };
  return {
    doc, win: {}, timers, fetched,
    now: () => t,
    setTimeout: (fn, ms) => { const n = id++; timers.push({ n, fn, at: t + ms }); return n; },
    clearTimeout: (n) => { const i = timers.findIndex((x) => x.n === n); if (i >= 0) timers.splice(i, 1); },
    random: () => 0.37,
    fetch: async (url) => {
      fetched.push(url);
      const body = files[url];
      return { ok: body !== undefined, json: async () => body };
    },
    advance(ms) {
      const end = t + ms;
      for (;;) {
        timers.sort((a, b) => a.at - b.at);
        if (!timers[0] || timers[0].at > end) break;
        const next = timers.shift();
        t = next.at;
        next.fn();
      }
      t = end;
    },
  };
}
const settle = async () => { for (let i = 0; i < 10; i += 1) await new Promise((r) => setImmediate(r)); };
const parts = (s) => {
  const box = s.node.children.find((c) => c.className === "saver-reader-box");
  return { box, label: box.children.find((c) => c.className === "saver-reader-label"), text: box.children.find((c) => c.className === "saver-reader-text"), by: box.children.find((c) => c.className === "saver-reader-by") };
};

test("nothing is fetched until the style first starts, then once", async () => {
  const e = env({ "facts.json": FACTS });
  const s = createFactsStyle(e);
  await settle();
  assert.deepEqual(e.fetched, []);
  s.start();
  await settle();
  s.stop();
  s.start();
  await settle();
  assert.deepEqual(e.fetched, ["facts.json"]);
});

test("a fact shows with the Weird fact label and no author line", async () => {
  const e = env({ "facts.json": FACTS });
  const s = createFactsStyle(e);
  s.start();
  await settle();
  const p = parts(s);
  assert.equal(p.label.textContent, "Weird fact");
  assert.equal(p.by, undefined);
  assert.ok(FACTS.some((f) => f.text === p.text.textContent));
  assert.equal(p.box.classList.contains("is-in"), true);
});

test("a quote shows its author below and has no label", async () => {
  const e = env({ "quotes.json": QUOTES });
  const s = createQuotesStyle(e);
  s.start();
  await settle();
  const p = parts(s);
  assert.equal(p.label, undefined);
  const q = QUOTES.find((x) => x.text === p.text.textContent);
  assert.ok(q);
  assert.equal(p.by.textContent, q.author);
  assert.ok(Number(p.box.style.props["--rd-size"].replace("px", "")) >= 28);
});

test("an entry is held, fades out, then the next appears in a new place", async () => {
  const e = env({ "facts.json": FACTS });
  const s = createFactsStyle(e);
  s.start();
  await settle();
  const p = parts(s);
  const first = p.text.textContent;
  const hold = holdMs("facts", first);
  e.advance(hold - 1);
  assert.equal(p.box.classList.contains("is-in"), true);
  e.advance(1);
  assert.equal(p.box.classList.contains("is-in"), false);
  assert.equal(p.text.textContent, first);
  e.advance(FADE_MS);
  assert.notEqual(p.text.textContent, first);
  assert.equal(p.box.classList.contains("is-in"), true);
  assert.ok(p.box.style.props["--rd-x"] !== undefined && p.box.style.props["--rd-y"] !== undefined);
});

test("stop clears the timer and a second start keeps a single one", async () => {
  const e = env({ "facts.json": FACTS });
  const s = createFactsStyle(e);
  s.start();
  s.start();
  await settle();
  assert.equal(e.timers.length, 1);
  s.stop();
  assert.equal(e.timers.length, 0);
  e.advance(10 * 60000);
  assert.equal(e.timers.length, 0);
});

test("stopped before the file arrives, nothing starts", async () => {
  const e = env({ "facts.json": FACTS });
  const s = createFactsStyle(e);
  s.start();
  s.stop();
  await settle();
  assert.equal(e.timers.length, 0);
  assert.equal(parts(s).text.textContent, "");
});

test("a missing file shows nothing and retries later while running", async () => {
  const files = {};
  const e = env(files);
  const s = createFactsStyle(e);
  s.start();
  await settle();
  assert.equal(e.timers.length, 1);
  files["facts.json"] = FACTS;
  e.advance(30000);
  await settle();
  assert.equal(parts(s).text.textContent !== "", true);
});

test("night turns the label off through the night class; hues set the tint", async () => {
  const e = env({ "facts.json": FACTS });
  const s = createFactsStyle(e);
  s.setNight(true);
  assert.equal(s.node.classList.contains("is-night"), true);
  s.setNight(false);
  assert.equal(s.node.classList.contains("is-night"), false);
  s.setHues({ h1: 140, h2: 200, chroma: 0.5 });
  assert.equal(s.node.style.props["--rd-h"], 140);
  s.setHues(null);
});

test("every quote of a full round keeps authors apart", async () => {
  const e = env({ "quotes.json": QUOTES });
  const s = createQuotesStyle(e);
  s.start();
  await settle();
  const p = parts(s);
  const seen = [p.by.textContent];
  for (let i = 0; i < 12; i += 1) {
    e.advance(holdMs("quotes", p.text.textContent) + FADE_MS);
    seen.push(p.by.textContent);
  }
  for (let i = 1; i < seen.length; i += 1) assert.notEqual(seen[i], seen[i - 1]);
});
