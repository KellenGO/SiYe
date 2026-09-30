import { test } from "node:test";
import assert from "node:assert/strict";
import { searchPollInterval, searchWaitParams } from "../src/lib/searchPolling.js";
import type { OverallStatus } from "../src/types/search.js";

test("active search and finalization use low latency local polling", () => {
  assert.equal(searchPollInterval(), 250);
  for (const overall of ["running", "cancelling", "completed"] as OverallStatus[]) {
    assert.equal(searchPollInterval({ overall, completed_at: null }), 250);
  }
});

test("revision backend immediately renews a held request; terminal stops", () => {
  assert.deepEqual(searchWaitParams(3), { after_revision: 3, wait_seconds: 15 });
  for (const revision of [undefined, -1, NaN, 1.5]) {
    assert.equal(searchWaitParams(revision), undefined);
  }
  assert.equal(searchPollInterval({ revision: 0, overall: "running", completed_at: null }), 10);
  assert.equal(searchPollInterval({ revision: 1, overall: "completed", completed_at: "done",
    hydration_status: "running" }), 10);
  assert.equal(searchPollInterval({ revision: 2, overall: "completed", completed_at: "done" }), false);
});

test("terminal jobs slow down for hydration and stop when finished", () => {
  for (const overall of ["completed", "partial", "failed", "cancelled"] as OverallStatus[]) {
    const job = { overall, completed_at: "2026-09-30T00:00:00Z" };
    assert.equal(searchPollInterval({ ...job, hydration_status: "running" }), 800);
    assert.equal(searchPollInterval({ ...job, hydration_status: "completed" }), false);
    assert.equal(searchPollInterval(job), false);
  }
});
