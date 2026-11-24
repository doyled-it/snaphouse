import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const dir = new URL("../../roles/room_picker/files/site/", import.meta.url);
const raw = (name) => readFileSync(new URL(name, dir), "utf8");
const facts = JSON.parse(raw("facts.json"));
const quotes = JSON.parse(raw("quotes.json"));

const filled = (v) => typeof v === "string" && v.trim() !== "" && v === v.trim();
const isHttps = (v) => {
  try {
    return new URL(v).protocol === "https:";
  } catch {
    return false;
  }
};
// An em dash, or an en dash used as a dash (spaced, or between letters).
const DASH = /—|―|\s–\s|[A-Za-z]–[A-Za-z]/;

test("facts.json has at least 150 entries with text and an https source", () => {
  assert.ok(Array.isArray(facts));
  assert.ok(facts.length >= 150, `only ${facts.length}`);
  for (const f of facts) {
    assert.deepEqual(Object.keys(f).sort(), ["source", "text"], JSON.stringify(f));
    assert.ok(filled(f.text), JSON.stringify(f));
    assert.ok(f.text.length <= 160, `${f.text.length}: ${f.text}`);
    assert.ok(isHttps(f.source), `source: ${f.source}`);
  }
});

test("quotes.json has at least 120 entries with text, author, source and a source URL", () => {
  assert.ok(Array.isArray(quotes));
  assert.ok(quotes.length >= 120, `only ${quotes.length}`);
  for (const q of quotes) {
    assert.deepEqual(Object.keys(q).sort(), ["author", "source", "sourceUrl", "text"], JSON.stringify(q));
    assert.ok(filled(q.text), JSON.stringify(q));
    assert.ok(q.text.length <= 220, `${q.text.length}: ${q.text}`);
    assert.ok(filled(q.author), JSON.stringify(q));
    assert.ok(filled(q.source), JSON.stringify(q));
    assert.ok(isHttps(q.sourceUrl), `sourceUrl: ${q.sourceUrl}`);
  }
});

test("no duplicate facts or quotes", () => {
  const key = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  assert.equal(new Set(facts.map((f) => key(f.text))).size, facts.length);
  assert.equal(new Set(quotes.map((q) => key(q.text))).size, quotes.length);
  assert.equal(new Set(facts.map((f) => f.source)).size, facts.length, "one fact per source page");
});

test("quotes spread over at least 12 authors and none is above 12 percent", () => {
  const counts = new Map();
  for (const q of quotes) counts.set(q.author, (counts.get(q.author) || 0) + 1);
  assert.ok(counts.size >= 12, `only ${counts.size} authors`);
  for (const [author, n] of counts) assert.ok(n / quotes.length <= 0.12, `${author} has ${n}`);
});

test("no em dash, and no en dash used as a dash, in any entry", () => {
  for (const entry of [...facts, ...quotes]) {
    for (const value of Object.values(entry)) assert.doesNotMatch(value, DASH, value);
  }
  assert.doesNotMatch(raw("facts.json") + raw("quotes.json"), /—/);
});
