import { test } from "node:test";
import assert from "node:assert/strict";
import {
  toHistoryView,
  toHistoryViews,
  type HistoryView,
} from "../src/lib/historyApi.js";
import type { UnifiedSearchResult } from "../src/types/search.js";

/** 本仓库 tsconfig 下 assert 只暴露部分方法，这里统一用 JSON 比较。 */
function jsonEqual(actual: unknown, expected: unknown): void {
  assert.equal(JSON.stringify(actual), JSON.stringify(expected));
}

function result(id: string, overrides: Partial<UnifiedSearchResult> = {}): UnifiedSearchResult {
  return {
    platform: "xhs", content_id: id, content_type: "note", title: `标题 ${id}`,
    author: "作者", url: `https://www.xiaohongshu.com/explore/${id}`,
    published_at: "2026-09-05T12:00:00Z", snippet: "摘要", metrics: { like_count: 3 },
    cover_url: null, rank: 0, ...overrides,
  };
}

/** 后端 /api/history/views 返回的条目形状。 */
function apiView(id: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 1, key: `xhs|${id}`, result: result(id),
    first_viewed_at: "2026-09-01T00:00:00Z", last_viewed_at: "2026-09-02T00:00:00Z", view_count: 2,
    ...overrides,
  };
}

test("toHistoryView：把后端 snake_case 转成前端 camelCase", () => {
  const view = toHistoryView(apiView("n1")) as HistoryView;
  assert.ok(view !== null);
  assert.equal(view.key, "xhs|n1");
  assert.equal(view.firstViewedAt, "2026-09-01T00:00:00Z");
  assert.equal(view.lastViewedAt, "2026-09-02T00:00:00Z");
  assert.equal(view.viewCount, 2);
  assert.equal(view.result.title, "标题 n1");
});

test("toHistoryView：格式非法返回 null，不抛异常（单条坏数据不整页崩）", () => {
  assert.equal(toHistoryView(null), null);
  assert.equal(toHistoryView({ id: 1 }), null);
  assert.equal(toHistoryView({ id: 1, result: { platform: "nope", content_id: "x" } }), null);
  assert.equal(toHistoryView({ id: 1, result: { ...result("n1"), url: "javascript:alert(1)" } }), null);
});

test("toHistoryView：缺时间/计数时给出安全默认值", () => {
  const view = toHistoryView({ id: 1, result: result("n1") }) as HistoryView;
  assert.ok(view !== null);
  assert.equal(view.viewCount, 0);
  assert.ok(Number.isFinite(Date.parse(view.firstViewedAt)));
  assert.ok(Number.isFinite(Date.parse(view.lastViewedAt)));
});

test("toHistoryViews：过滤坏数据并接受空输入", () => {
  const views = toHistoryViews({ items: [apiView("n1"), { id: 9 }, apiView("b")] });
  assert.equal(views.length, 2);
  jsonEqual(views.map((view) => view.key), ["xhs|n1", "xhs|b"]);
  jsonEqual(toHistoryViews(null), []);
  jsonEqual(toHistoryViews({}), []);
});
