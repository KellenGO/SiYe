import { test } from "node:test";
import assert from "node:assert/strict";
import { searchPollInterval } from "../src/lib/searchPolling.js";
import type { OverallStatus } from "../src/types/search.js";

test("active search and finalization use low latency local polling", () => {
  assert.equal(searchPollInterval(), 250);
  for (const overall of ["running", "cancelling", "completed"] as OverallStatus[]) {
    assert.equal(searchPollInterval({ overall, completed_at: null }), 250);
  }
});

test("terminal jobs slow down for hydration and stop when finished", () => {
  for (const overall of ["completed", "partial", "failed", "cancelled"] as OverallStatus[]) {
    const job = { overall, completed_at: "2026-09-30T00:00:00Z" };
    assert.equal(searchPollInterval({ ...job, hydration_status: "running" }), 800);
    assert.equal(searchPollInterval({ ...job, hydration_status: "completed" }), false);
    assert.equal(searchPollInterval(job), false);
  }
});
