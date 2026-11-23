import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);
const more = readFileSync(new URL("panel-more.js", site), "utf8");
const css = readFileSync(new URL("panel.css", site), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function rulesFor(re) {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((m) => ({ selector: m[1].trim(), body: m[2] }))
    .filter((r) => re.test(r.selector));
}

test("the panel Frame screen draws the change view from the host", () => {
  assert.match(more, /host\.frameChange\(\)/);
  assert.match(more, /p-change/);
  assert.match(more, /p-change-text/);
  assert.match(more, /p-change-thumb/);
});

test("the change line has a polite live region", () => {
  assert.match(more, /aria-live/);
});

test("the change rules are all under html[data-panel], text is at least 20px", () => {
  const rules = rulesFor(/\.p-change/);
  assert.ok(rules.length >= 3);
  for (const { selector, body } of rules) {
    for (const part of selector.split(",")) {
      assert.match(part.trim(), /^html\[data-panel\]/, part);
    }
    for (const m of body.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
      assert.ok(Number(m[1]) >= 20, `${selector} uses ${m[1]}px`);
    }
  }
  const text = rulesFor(/\.p-change-text/);
  assert.ok(text.some((r) => /font-size:\s*(2\d|[3-9]\d)px/.test(r.body)));
});

test("Back and Next stay at least 72px tall with the change line up", () => {
  const big = rulesFor(/\.p-big$/);
  assert.ok(big.some((r) => /min-height:\s*(7[2-9]|[89]\d|\d{3})px/.test(r.body)));
});

test("a busy button reads as busy without changing its size", () => {
  const busy = rulesFor(/\.p-big\[aria-busy="true"\]/);
  assert.equal(busy.length, 1);
  assert.doesNotMatch(busy[0].body, /width|height|padding|font-size/);
});
