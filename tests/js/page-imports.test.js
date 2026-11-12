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

for (const file of ["rooms.js", "pair-controller.js"]) {
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
