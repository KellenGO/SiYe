import { test } from "node:test";
import assert from "node:assert/strict";
import { moveCollection } from "../src/lib/collectionOrder.js";

test("moveCollection：向上、向下与无效目标", () => {
  const original = [1, 2, 3, 4];
  assert.deepEqual(moveCollection(original, 3, 1, false), [3, 1, 2, 4]);
  assert.deepEqual(moveCollection(original, 1, 3, true), [2, 3, 1, 4]);
  assert.deepEqual(moveCollection(original, 2, 3, false), original);
  assert.deepEqual(moveCollection(original, 2, 99, true), original);
  assert.deepEqual(original, [1, 2, 3, 4]);
});
