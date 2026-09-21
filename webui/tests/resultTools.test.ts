import { test } from "node:test";
import assert from "node:assert/strict";

import { filterResultGroups } from "../src/lib/resultTools.js";
import type { UnifiedSearchResult } from "../src/types/search.js";

function result(platform: UnifiedSearchResult["platform"], contentId: string): UnifiedSearchResult {
  return {
    platform,
    content_id: contentId,
    content_type: "video",
    title: contentId,
    author: "作者",
    snippet: null,
    url: `https://example.test/${contentId}`,
    published_at: null,
    cover_url: null,
    metrics: {},
    rank: 1,
  };
}

test("result tabs keep duplicate remote identities out of every platform view", () => {
  const rows = [result("bilibili", "BV1"), result("bilibili", "BV1"), result("xhs", "note-1")];
  const filters = { days: 0 as const, contentType: "all" as const, query: "" };

  assert.deepEqual(
    filterResultGroups(rows, filters, Date.now()).map((item) => `${item.platform}|${item.content_id}`),
    ["bilibili|BV1", "xhs|note-1"]
  );
  assert.deepEqual(
    filterResultGroups(rows, filters, Date.now(), "xhs").map((item) => `${item.platform}|${item.content_id}`),
    ["xhs|note-1"]
  );
});
