import { test } from "node:test";
import assert from "node:assert/strict";
import { finished, health, snapshot } from "./status";

test("tray status", () => {
  assert.equal(health(true, true), "running");
  assert.equal(health(true, false), "running");
  assert.equal(health(false, true), "starting");
  assert.equal(health(false, false), "stopped");
});

test("a notification only when queued work runs out", () => {
  const busy = snapshot({ jobs: { convert: { queued: 3, running: 1, failed: 0 }, index: { queued: 2, running: 0, failed: 1 } } });
  assert.deepEqual(busy, { active: 6, failed: 1 });
  const idle = snapshot({ jobs: { convert: { queued: 0, running: 0, failed: 2 }, index: { queued: 0, running: 0, failed: 1 } } });
  assert.equal(finished(null, idle), null, "nothing at start-up");
  assert.equal(finished(idle, idle), null, "nothing when idle");
  assert.equal(finished(idle, busy), null, "nothing when work starts");
  assert.equal(finished(busy, busy), null);
  const n = finished(busy, idle);
  assert.equal(n?.title, "Background work finished");
  assert.match(n!.body, /^2 files couldn't be processed/);
  assert.match(finished(busy, { active: 0, failed: 1 })!.body, /Every queued file/);
  assert.deepEqual(snapshot({ jobs: {} }), { active: 0, failed: 0 });
});
