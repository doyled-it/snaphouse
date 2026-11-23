import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);
const rooms = readFileSync(new URL("rooms.js", site), "utf8");
const html = readFileSync(new URL("index.html", site), "utf8");

test("rooms.js loads the panel layout only by dynamic import, guarded by panel=1", () => {
  assert.doesNotMatch(rooms, /^import[^;]*\.\/panel\.js/m);
  const at = rooms.indexOf('import("./panel.js")');
  assert.ok(at > -1, "dynamic import of panel.js");
  const guard = rooms.lastIndexOf('get("panel") === "1"', at);
  assert.ok(guard > -1 && at - guard < 300, "import sits under the panel=1 guard");
});

test("the shipped page carries no panel attribute, stylesheet or node", () => {
  assert.doesNotMatch(html, /data-panel/);
  assert.doesNotMatch(html, /panel\.css/);
  assert.doesNotMatch(html, /panel\.js/);
  assert.doesNotMatch(html, /id="p-|class="p-/);
});
