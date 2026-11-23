// rooms.js is bound to the DOM, so node cannot import it, and `node --check`
// does not resolve imports. A name it imports from a module that no longer
// exports it only fails in the browser, where the whole page then stops working
// (this happened when the pairing confirm helpers were removed). This test reads
// every static import in the page's modules and checks the names exist.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SITE = fileURLToPath(new URL("../../roles/room_picker/files/site/", import.meta.url));
const IMPORT_RE = /import\s*\{([^}]*)\}\s*from\s*"(\.\/[^"]+)";/g;

for (const file of ["rooms.js", "pair-controller.js", "devices-controller.js", "volume-slider.js", "nav-model.js", "panel.js", "panel-music.js", "panel-garage.js"]) {
  test(`${file} imports only names the modules it imports actually export`, async () => {
    const source = readFileSync(path.join(SITE, file), "utf8");
    let checked = 0;
    for (const match of source.matchAll(IMPORT_RE)) {
      const names = match[1]
        .split(",")
        .map((n) => n.replace(/\/\/.*$/gm, "").trim().split(/\s+as\s+/)[0])
        .filter(Boolean);
      const mod = await import(path.join(SITE, match[2]));
      for (const name of names) {
        assert.ok(name in mod, `${file} imports ${name} from ${match[2]}, which does not export it`);
        checked += 1;
      }
    }
    assert.ok(checked > 0, "found no imports to check");
  });
}

test("rooms.js wires the Garage card to garage-model.js and the guarded endpoints", () => {
  const source = readFileSync(path.join(SITE, "rooms.js"), "utf8");
  assert.match(source, /from "\.\/garage-model\.js"/);
  assert.match(source, /garageTabVisible/);
  for (const name of ["intentOutcome", "commandOutcome", "afterCommandPoll", "recentLine", "parseGarageText"]) {
    assert.match(source, new RegExp(`\\b${name}\\b`), `${name} is not used`);
  }
  // Both POSTs go through apiRequest, which adds X-Snaphouse: 1.
  assert.match(source, /postGarage\("\/api\/homebody\/garage\/intent"/);
  assert.match(source, /postGarage\("\/api\/homebody\/garage\/command"/);
  assert.match(source, /const \{ url, init \} = apiRequest\(path, body\);\s*\n\s*const response = await fetch\(url, init\);\s*\n\s*return \{ status/);
});

test("the Garage command is never retried by the page", () => {
  const source = readFileSync(path.join(SITE, "rooms.js"), "utf8");
  const sends = source.match(/postGarage\("\/api\/homebody\/garage\/command"/g) || [];
  assert.equal(sends.length, 1, "one call site for the command");
});

test("a refused garage settings write puts the remote switch back to the server's value at once", () => {
  const source = readFileSync(path.join(SITE, "rooms.js"), "utf8");
  const body = source.match(/async function writeGarage\([\s\S]*?\n\}\n/)[0];
  const refused = body.slice(body.indexOf("if (!response.ok)"), body.indexOf("const doc = parseGarageText"));
  const reset = refused.indexOf("setGarageSwitch(garageWritten.remote === true)");
  assert.ok(reset >= 0, "the refused branch resets the switch");
  assert.ok(reset < refused.indexOf("throw"), "and does so before it throws");
});
