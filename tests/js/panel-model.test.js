import { test } from "node:test";
import assert from "node:assert/strict";
import { isPanel } from "../../roles/room_picker/files/site/panel-model.js";

test("isPanel is true only for panel=1", () => {
  assert.equal(isPanel("?panel=1&idle=60"), true);
  assert.equal(isPanel(""), false);
  assert.equal(isPanel("?panel=0"), false);
  assert.equal(isPanel("?panel=true"), false);
  assert.equal(isPanel(undefined), false);
});
