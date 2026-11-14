// docs/cross-repo-features.md is the contract with the homebody repo. The garage
// section must list the document key, the endpoints, the error codes and the
// origin header, and carry the rule that the origin is decided by the Cloudflare
// headers (the tunnel connector sits on the LAN, not on loopback).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const doc = readFileSync(fileURLToPath(new URL("../../docs/cross-repo-features.md", import.meta.url)), "utf8");

test("the contract lists the garage key and both endpoints", () => {
  assert.match(doc, /`garage`/);
  assert.match(doc, /POST \/api\/homebody\/garage\/intent/);
  assert.match(doc, /POST \/api\/homebody\/garage\/command/);
  for (const key of ["garage_left_open_minutes", "garage_away_open_grace_minutes", "garage_alerts", "garage_remote"]) {
    assert.ok(doc.includes(key), `${key} is missing`);
  }
});

test("the contract lists the error codes", () => {
  for (const code of ["400", "403", "409", "423", "429", "503", "504"]) {
    assert.match(doc, new RegExp(`\\|\\s*${code}\\s*\\|`), `error ${code} is missing from the table`);
  }
});

test("the contract names the origin header and the R-T8 rule", () => {
  assert.match(doc, /X-Snaphouse-Origin/);
  assert.match(doc, /Cf-Ray/);
  assert.match(doc, /Cf-Access-Jwt-Assertion/);
  assert.match(doc, /all four/i);
  assert.doesNotMatch(doc, /tunnel daemon's own connections are\s+`tunnel`/);
  assert.match(doc, /tunnel connector (?:sits|runs) on the LAN/);
});

test("the contract names the two fixture copies that must change together", () => {
  assert.match(doc, /garage_status_sample\.json/);
  assert.match(doc, /homebody-garage\.json/);
});

test("the contract has no em dash", () => {
  assert.ok(!doc.includes("\u2014"));
});
