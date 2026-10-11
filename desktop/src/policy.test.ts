import { test } from "node:test";
import assert from "node:assert/strict";
import { classify, handoffUrl, mayRequest } from "./policy";
import { loadConfig, origin } from "./config";

const APP = "https://localhost/";
const CC = "https://localhost:8443/";

test("only this computer's two addresses open in the app", () => {
  assert.equal(classify("https://localhost/cases/3?tab=search", APP, CC), "app");
  assert.equal(classify("https://localhost:443/api/status", APP, CC), "app");
  assert.equal(classify("https://localhost:8443/#components", APP, CC), "control-center");
  assert.equal(classify("http://localhost/", APP, CC), "external", "plain http is not the app");
  assert.equal(classify("https://localhost:9999/", APP, CC), "external");
  assert.equal(classify("https://example.com/", APP, CC), "external");
  assert.equal(classify("https://localhost.example.com/", APP, CC), "external");
  assert.equal(classify("file:///C:/Program%20Files/x/offline.html", APP, CC), "local");
  assert.equal(classify("javascript:alert(1)", APP, CC), "blocked");
  assert.equal(classify("not a url", APP, CC), "blocked");
});

test("the window never fetches from the internet", () => {
  assert.equal(mayRequest("https://localhost/assets/app.js", APP, CC), true);
  assert.equal(mayRequest("blob:https://localhost/1234", APP, CC), true);
  assert.equal(mayRequest("https://fonts.googleapis.com/css", APP, CC), false);
  assert.equal(mayRequest("https://api.anthropic.com/v1/messages", APP, CC), false);
  assert.equal(mayRequest("ftp://x/", APP, CC), false);
});

test("the installer's first-run file must be the Control Center with a setup code", () => {
  assert.equal(handoffUrl("https://localhost:8443/#setup=AB12-CD34-EF56\r\n", CC), "https://localhost:8443/#setup=AB12-CD34-EF56");
  assert.equal(handoffUrl("https://localhost/#setup=AB12-CD34-EF56", CC), null, "wrong port");
  assert.equal(handoffUrl("https://evil.example/#setup=AB12-CD34-EF56", CC), null);
  assert.equal(handoffUrl("https://localhost:8443/", CC), null);
  assert.equal(handoffUrl("", CC), null);
});

test("standard ports, overridable for development", () => {
  const c = loadConfig({}, "C:\\Program Files\\Case File Manager\\resources", "C:\\Temp");
  assert.equal(c.appUrl, "https://localhost/");
  assert.equal(c.controlCenterUrl, "https://localhost:8443/");
  assert.match(c.caPath, /local-ca\.crt$/);
  const d = loadConfig({ CASEFILES_APP_PORT: "9443", CASEFILES_CC_PORT: "x" }, "/r", "/t");
  assert.equal(d.appUrl, "https://localhost:9443/");
  assert.equal(d.controlCenterUrl, "https://localhost:8443/");
  assert.equal(origin(443), "https://localhost/");
});
