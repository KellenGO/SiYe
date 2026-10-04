import { test } from "node:test";
import assert from "node:assert/strict";
import { sectionReading, transcriptDuration } from "../src/lib/researchPresentation.js";

test("transcript duration renders media times and rejects unknown/invalid values", () => {
  assert.equal(transcriptDuration(522.4), "08:42");
  assert.equal(transcriptDuration(87), "01:27");
  assert.equal(transcriptDuration(0), "00:00");
  for (const value of [undefined, NaN, Infinity, -1]) assert.equal(transcriptDuration(value), "");
});

test("zero chunks are unavailable, fully read comments are a sample, and content limits stay explicit", () => {
  assert.equal(sectionReading({ chunks: 0, read_chunks: 0, reading_complete: false }, true), "research.sectionUnavailable");
  assert.equal(sectionReading({ chunks: 2, read_chunks: 1, reading_complete: false }, true), "research.sectionRead");
  assert.equal(sectionReading({ chunks: 2, read_chunks: 2, reading_complete: true }, true), "research.sampleRead");
  assert.equal(sectionReading({ chunks: 2, read_chunks: 2 }, true), "research.sampleRead");
  assert.equal(sectionReading({ chunks: 2, read_chunks: 2, content_limited: true }, true), "research.sectionRead");
  assert.equal(sectionReading({ chunks: 1, read_chunks: 1, reading_complete: true }, false), "research.sectionRead");
});
