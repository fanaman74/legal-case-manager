import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { verifyServerCertificate } from "./trust";

const pem = (name: string) => readFileSync(path.join(__dirname, "..", "test-fixtures", `${name}.crt`), "utf8");
const ca = pem("ca");

test("trusts this computer's CA for localhost", () => {
  assert.deepEqual(verifyServerCertificate(pem("server"), ca, "localhost"), { ok: true });
  assert.deepEqual(verifyServerCertificate(pem("server"), ca, "127.0.0.1"), { ok: true });
  assert.deepEqual(verifyServerCertificate(pem("server"), ca, "[::1]"), { ok: true });
});

test("refuses anything else", () => {
  assert.equal(verifyServerCertificate(pem("rogue-server"), ca, "localhost").ok, false, "another CA");
  assert.equal(verifyServerCertificate(pem("server"), ca, "example.com").ok, false, "another host");
  assert.equal(verifyServerCertificate(pem("office-only"), ca, "localhost").ok, false, "doesn't name localhost");
  assert.equal(verifyServerCertificate(pem("server"), pem("server"), "localhost").ok, false, "a leaf isn't a CA");
  assert.equal(verifyServerCertificate("junk", ca, "localhost").ok, false);
  const later = new Date(Date.now() + 200 * 365 * 24 * 3600 * 1000);
  assert.match((verifyServerCertificate(pem("server"), ca, "localhost", later) as { reason: string }).reason, /expired/);
});
