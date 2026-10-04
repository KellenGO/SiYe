import { test } from "node:test";
import assert from "node:assert/strict";
import { transcriptDuration } from "../src/lib/researchPresentation.js";

test("transcript duration renders media times and rejects unknown/invalid values", () => {
  assert.equal(transcriptDuration(522.4), "08:42");
  assert.equal(transcriptDuration(87), "01:27");
  assert.equal(transcriptDuration(0), "00:00");
  for (const value of [undefined, NaN, Infinity, -1]) assert.equal(transcriptDuration(value), "");
});
