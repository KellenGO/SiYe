/**
 * Round 12 搜索体验纯逻辑测试 —— 直接 import 编译后的生产模块
 * （webui/src/lib/searchExperience.ts），不复制任何生产逻辑。
 * 运行：npm run test:search（tsc -p tsconfig.test.json && node --test .test-dist/tests/）
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  HISTORY_STORAGE_KEY,
  PLATFORM_PREF_STORAGE_KEY,
  MAX_HISTORY_ITEMS,
  PLATFORM_SLUGS,
  addHistoryItem,
  applySearchTransition,
  aggregateSortResults,
  createInitialExperienceState,
  deduplicateCrossPlatformResults,
  expandGroupedResultsForPlatform,
  engagementScore,
  groupByPlatform,
  interleaveByPlatform,
  interactionStrengthScore,
  isSearchBlocked,
  keywordRelevanceScore,
  makeDedupKey,
  mergeSinglePlatformRetry,
  normalizeHistoryKeyword,
  normalizedPlatformSet,
  parseHistory,
  parsePlatformPref,
  parsePublishedTime,
  readHistory,
  readPlatformPref,
  recomputeOverall,
  removeHistoryItem,
  resolveActiveTab,
  safeErrorSummary,
  sortResults,
  toFiniteNumber,
  writeHistory,
  writePlatformPref,
  type ExperienceEvent,
  type ExperienceState,
  type SearchHistoryItem,
  type SearchSortMode,
  type StorageLike,
} from "../src/lib/searchExperience.js";
import type { PlatformSlug, SearchJobResponse, UnifiedSearchResult } from "../src/types/search.js";

// ── Test helpers（仅测试构造数据用，不包含生产逻辑） ──────────────────

class MemoryStorage implements StorageLike {
  private map = new Map<string, string>();
  getItem(key: string): string | null {
    return this.map.has(key) ? this.map.get(key)! : null;
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
  removeItem(key: string): void {
    this.map.delete(key);
  }
}

function makeResult(
  platform: PlatformSlug,
  contentId: string,
  overrides: Partial<UnifiedSearchResult> = {}
): UnifiedSearchResult {
  return {
    platform,
    content_id: contentId,
    content_type: "note",
    title: `title-${contentId}`,
    author: null,
    url: `https://${platform}.com/${contentId}`,
    published_at: null,
    cover_url: null,
    metrics: {},
    rank: 0,
    ...overrides,
  };
}

function makeJob(
  jobId: string,
  overall: SearchJobResponse["overall"],
  keyword: string,
  platforms: Record<PlatformSlug, { status: string; count: number }>,
  results: UnifiedSearchResult[],
  overrides: Partial<SearchJobResponse> = {}
): SearchJobResponse {
  return {
    job_id: jobId,
    overall,
    keyword,
    created_at: "2026-08-01T00:00:00.000Z",
    completed_at: "2026-08-01T00:01:00.000Z",
    platforms: Object.fromEntries(
      Object.entries(platforms).map(([p, info]) => [
        p,
        { status: info.status, result_count: info.count, error_summary: null },
      ])
    ) as SearchJobResponse["platforms"],
    results,
    ...overrides,
  };
}

const STATUS_OK: Record<PlatformSlug, { status: string; count: number }> = {
  xhs: { status: "succeeded", count: 1 },
  douyin: { status: "succeeded", count: 1 },
  bilibili: { status: "succeeded", count: 0 },
  zhihu: { status: "succeeded", count: 0 },
};

function h(keyword: string, platforms: PlatformSlug[], searchedAt: string): SearchHistoryItem {
  return { keyword, platforms, searchedAt };
}

// ── 1. 非法 localStorage JSON 安全回退 ─────────────────────────────────

test("readHistory: 非法 JSON 安全回退为空列表", () => {
  const storage = new MemoryStorage();
  storage.setItem(HISTORY_STORAGE_KEY, "{not valid json!!");
  assert.deepEqual(readHistory(storage), []);
});

test("readHistory: 非数组 JSON 安全回退为空列表", () => {
  const storage = new MemoryStorage();
  storage.setItem(HISTORY_STORAGE_KEY, JSON.stringify({ keyword: "x" }));
  assert.deepEqual(readHistory(storage), []);
});

test("writeHistory: 浏览器拒绝存储时静默失败不抛异常", () => {
  const storage: StorageLike = {
    getItem: () => null,
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
    removeItem: () => {},
  };
  assert.equal(writeHistory(storage, [{ keyword: "k", platforms: ["xhs"], searchedAt: "t" }]), false);
});

// ── 2. 非法平台 slug 被丢弃 ────────────────────────────────────────────

test("parseHistory: 非法平台 slug 被过滤丢弃，过滤后为空才丢弃整条", () => {
  const raw = [
    { keyword: "a", platforms: ["xhs", "evil-slug", 123], searchedAt: "2026-07-01T00:00:00.000Z" },
    { keyword: "b", platforms: ["douyin"], searchedAt: "2026-07-02T00:00:00.000Z" },
    { keyword: "c", platforms: ["evil-only"], searchedAt: "2026-07-03T00:00:00.000Z" },
  ];
  const history = parseHistory(raw);
  assert.equal(history.length, 2);
  assert.deepEqual(history[0].platforms, ["xhs"]); // 非法 slug 被过滤，合法部分保留
  assert.deepEqual(history.map((h) => h.keyword), ["a", "b"]); // c 过滤后为空 → 整条丢弃
});

test("parseHistory: 空平台列表 / 缺失字段的记录被丢弃", () => {
  const raw = [
    { keyword: "  ", platforms: ["xhs"], searchedAt: "2026-07-01T00:00:00.000Z" },
    { keyword: "a", platforms: [], searchedAt: "2026-07-01T00:00:00.000Z" },
    { keyword: "b", platforms: ["xhs"], searchedAt: 123 },
    { keyword: "c", platforms: ["xhs"], searchedAt: "2026-07-01T00:00:00.000Z" },
  ];
  const history = parseHistory(raw);
  assert.equal(history.length, 1);
  assert.equal(history[0].keyword, "c");
});

// ── 3. 历史最多 10 条 ──────────────────────────────────────────────────

test("parseHistory: searchedAt 非法日期被丢弃，整条记录无效", () => {
  const valid = parseHistory([
    { keyword: "a", platforms: ["xhs"], searchedAt: "not-a-date" },
    { keyword: "b", platforms: ["xhs"], searchedAt: "2026-08-01T00:00:00.000Z" },
    { keyword: "c", platforms: ["xhs"], searchedAt: "" },
    { keyword: "d", platforms: ["xhs"], searchedAt: 12345 },
  ]);
  assert.deepEqual(valid.map((i) => i.keyword), ["b"]);
});

test("addHistoryItem: 最多保留 10 条", () => {
  let history: SearchHistoryItem[] = [];
  for (let i = 0; i < 13; i++) {
    history = addHistoryItem(history, `kw${i}`, ["xhs"], `t${i}`);
  }
  assert.equal(history.length, MAX_HISTORY_ITEMS);
  assert.equal(history[0].keyword, "kw12"); // 最新在最前
  assert.equal(history[history.length - 1].keyword, "kw3");
});

// ── 4. 重复历史移动到最前 ─────────────────────────────────────────────

test("addHistoryItem: 相同关键词（大小写/空白不同）+ 相同平台组合 → 去重移前并更新时间", () => {
  const base: SearchHistoryItem[] = [
    { keyword: "Alpha", platforms: ["xhs"], searchedAt: "t1" },
    { keyword: "beta", platforms: ["douyin"], searchedAt: "t2" },
  ];
  const next = addHistoryItem(base, "  ALPHA ", ["xhs"], "t3");
  assert.equal(next.length, 2);
  assert.equal(next[0].keyword, "ALPHA"); // trim 后保存
  assert.equal(next[0].searchedAt, "t3");
  assert.equal(next[1].keyword, "beta");
});

test("addHistoryItem: 平台组合不同（顺序/成员不同）不算重复", () => {
  const base: SearchHistoryItem[] = [{ keyword: "k", platforms: ["xhs"], searchedAt: "t1" }];
  const diffOrder = addHistoryItem(base, "k", ["douyin", "xhs"], "t2");
  assert.equal(diffOrder.length, 2); // 成员不同 → 新条目
  const sameSet = addHistoryItem(base, "k", ["xhs", "douyin"], "t3");
  assert.equal(sameSet.length, 2); // 排序后相同 → 去重移前
  assert.equal(sameSet[0].searchedAt, "t3");
});

test("normalizedPlatformSet: 过滤非法并排序", () => {
  assert.deepEqual(normalizedPlatformSet(["zhihu", "xhs", "xhs", "bogus"]), ["xhs", "zhihu"]);
  assert.deepEqual(normalizedPlatformSet([1, "xhs"]), ["xhs"]);
});

// ── 5. 删除单条与清空 ─────────────────────────────────────────────────

test("removeHistoryItem: 删除指定索引", () => {
  const base: SearchHistoryItem[] = [
    { keyword: "a", platforms: ["xhs"], searchedAt: "t1" },
    { keyword: "b", platforms: ["xhs"], searchedAt: "t2" },
    { keyword: "c", platforms: ["xhs"], searchedAt: "t3" },
  ];
  const next = removeHistoryItem(base, 1);
  assert.deepEqual(next.map((h) => h.keyword), ["a", "c"]);
  assert.equal(removeHistoryItem(base, 99).length, 3); // 越界安全
  assert.equal(removeHistoryItem(base, -1).length, 3);
});

test("清空: 写回空数组后 readHistory 返回空", () => {
  const storage = new MemoryStorage();
  writeHistory(storage, [{ keyword: "a", platforms: ["xhs"], searchedAt: "t" }]);
  writeHistory(storage, []);
  assert.deepEqual(readHistory(storage), []);
});

// ── 6. 平台偏好恢复及至少保留一个平台 ─────────────────────────────────

test("parsePlatformPref: 非法 slug 被丢弃，空结果回退全选", () => {
  assert.deepEqual(parsePlatformPref(["xhs", "bogus", "douyin"]), ["xhs", "douyin"]);
  assert.deepEqual(parsePlatformPref(["bogus", 42]), [...PLATFORM_SLUGS]);
  assert.deepEqual(parsePlatformPref("not-array"), [...PLATFORM_SLUGS]);
  assert.deepEqual(parsePlatformPref(null), [...PLATFORM_SLUGS]);
});

test("readPlatformPref: 刷新后恢复，损坏时默认全选", () => {
  const storage = new MemoryStorage();
  writePlatformPref(storage, ["zhihu", "xhs"]);
  assert.deepEqual(readPlatformPref(storage), ["zhihu", "xhs"]);
  storage.setItem(PLATFORM_PREF_STORAGE_KEY, "###broken###");
  assert.deepEqual(readPlatformPref(storage), [...PLATFORM_SLUGS]);
});

test("readPlatformPref: 存储不可用时默认全选", () => {
  const storage: StorageLike = {
    getItem: () => {
      throw new Error("SecurityError");
    },
    setItem: () => {},
    removeItem: () => {},
  };
  assert.deepEqual(readPlatformPref(storage), [...PLATFORM_SLUGS]);
});

// ── 7. 综合排序 V1 ────────────────────────────────────────────────────

test("aggregateSortResults: 关键词完整命中优先于无关标题", () => {
  const results = [
    makeResult("xhs", "unrelated", { title: "旅行日记", rank: 0 }),
    makeResult("douyin", "matched", { title: "露营装备推荐", rank: 5 }),
  ];
  const sorted = aggregateSortResults(results, "露营", { nowMs: Date.parse("2026-08-01T00:00:00Z") });
  assert.deepEqual(sorted.map((r) => r.content_id), ["matched", "unrelated"]);
});

test("keywordRelevanceScore: 标题优先于 snippet，snippet 优先于作者", () => {
  const keyword = "露营装备";
  const titleHit = makeResult("xhs", "title-hit", { title: "露营装备" });
  const snippetHit = makeResult("xhs", "snippet-hit", {
    title: "周末记录",
    snippet: "这篇内容介绍露营装备选择。",
  });
  const authorHit = makeResult("xhs", "author-hit", {
    title: "周末记录",
    author: "露营装备测评员",
  });
  assert.ok(keywordRelevanceScore(titleHit, keyword) > keywordRelevanceScore(snippetHit, keyword));
  assert.ok(keywordRelevanceScore(snippetHit, keyword) > keywordRelevanceScore(authorHit, keyword));
});

test("keywordRelevanceScore: 中文完整短语和双字 token 有效，单字弱命中不计分", () => {
  const keyword = "露营装备";
  const exact = makeResult("xhs", "exact", { title: "露营装备真实体验" });
  const tokenHit = makeResult("xhs", "token", { title: "露营帐篷推荐" });
  const singleChar = makeResult("xhs", "single", { title: "营地风景分享" });
  assert.ok(keywordRelevanceScore(exact, keyword) > keywordRelevanceScore(tokenHit, keyword));
  assert.ok(keywordRelevanceScore(tokenHit, keyword) > keywordRelevanceScore(singleChar, keyword));
  assert.equal(keywordRelevanceScore(singleChar, keyword), 0);
});

test("aggregateSortResults: 互动只使用平台内相对排名，不跨平台比较绝对数量", () => {
  const results = [
    makeResult("xhs", "x-high", { rank: 0, metrics: { like_count: 100 } }),
    makeResult("xhs", "x-low", { rank: 1, metrics: { like_count: 10 } }),
    makeResult("douyin", "d-high", { rank: 0, metrics: { like_count: 100000 } }),
    makeResult("douyin", "d-low", { rank: 1, metrics: { like_count: 10000 } }),
  ];
  const sorted = aggregateSortResults(results, "", { nowMs: Date.parse("2026-08-01T00:00:00Z") });
  assert.deepEqual(sorted.slice(0, 2).map((r) => r.content_id), ["x-high", "d-high"]);
});

test("aggregateSortResults: 原始 rank 和新鲜度参与综合分", () => {
  const results = [
    makeResult("xhs", "old-best-rank", {
      rank: 0,
      published_at: "2025-01-01T00:00:00Z",
    }),
    makeResult("xhs", "new-lower-rank", {
      rank: 1,
      published_at: "2026-07-20T00:00:00Z",
    }),
  ];
  const sorted = aggregateSortResults(results, "", { nowMs: Date.parse("2026-08-01T00:00:00Z") });
  assert.deepEqual(sorted.map((r) => r.content_id), ["old-best-rank", "new-lower-rank"]);
});

test("aggregateSortResults: 连续结果会施加平台多样性惩罚", () => {
  const results = [
    makeResult("xhs", "x1", { rank: 0 }),
    makeResult("xhs", "x2", { rank: 1 }),
    makeResult("xhs", "x3", { rank: 2 }),
    makeResult("bilibili", "b1", { rank: 0 }),
    makeResult("bilibili", "b2", { rank: 1 }),
    makeResult("bilibili", "b3", { rank: 2 }),
  ];
  const sorted = aggregateSortResults(results, "", {
    nowMs: Date.parse("2026-08-01T00:00:00Z"),
    platformOrder: ["xhs", "bilibili"],
  });
  assert.deepEqual(sorted.slice(0, 4).map((r) => r.platform), ["xhs", "bilibili", "xhs", "bilibili"]);
});

// ── 8. 最新排序：非法/缺失时间放最后，稳定 ────────────────────────────

test("sortResults latest: 时间从新到旧，无/非法时间放最后并保持相对顺序", () => {
  const results = [
    makeResult("xhs", "a", { published_at: null }),
    makeResult("xhs", "b", { published_at: "2026-01-01T00:00:00Z" }),
    makeResult("xhs", "c", { published_at: "bad-date" }),
    makeResult("xhs", "d", { published_at: "2026-06-01T00:00:00Z" }),
    makeResult("xhs", "e", { published_at: "2026-03-01T00:00:00Z" }),
  ];
  const sorted = sortResults(results, "latest");
  assert.deepEqual(sorted.map((r) => r.content_id), ["d", "e", "b", "a", "c"]);
});

test("sortResults latest: 时间相同时保持原始顺序（稳定）", () => {
  const results = [
    makeResult("xhs", "a", { published_at: "2026-05-01T00:00:00Z" }),
    makeResult("xhs", "b", { published_at: "2026-05-01T00:00:00Z" }),
    makeResult("xhs", "c", { published_at: "2026-05-01T00:00:00Z" }),
  ];
  assert.deepEqual(sortResults(results, "latest").map((r) => r.content_id), ["a", "b", "c"]);
});

test("parsePublishedTime: 非法输入 → null", () => {
  assert.equal(parsePublishedTime(null), null);
  assert.equal(parsePublishedTime(""), null);
  assert.equal(parsePublishedTime("not-a-date"), null);
  assert.equal(typeof parsePublishedTime("2026-08-01T00:00:00Z"), "number");
});

// ── 9. 互动排序：公式 + 缺失为 0 + 同分稳定 ───────────────────────────

test("engagementScore: 按固定公式计算，缺失/NaN/Infinity/非数字为 0", () => {
  assert.equal(
    engagementScore({ like_count: 10, collect_count: 2, comment_count: 3, share_count: 1, coin_count: 4, danmaku_count: 5, view_count: 100 }),
    10 + 2 * 2 + 3 * 2 + 1 * 3 + 4 * 2 + 5 + 100 * 0.01
  );
  assert.equal(engagementScore({}), 0);
  assert.equal(engagementScore({ like_count: NaN }), 0);
  assert.equal(engagementScore({ like_count: Infinity }), 0);
  assert.equal(engagementScore({ like_count: "12" as unknown as number }), 0);
  assert.equal(engagementScore({ like_count: 5, collect_count: 2, share_count: 1 }), 5 + 4 + 3);
});

test("toFiniteNumber: 非数字值按 0", () => {
  assert.equal(toFiniteNumber(undefined), 0);
  assert.equal(toFiniteNumber(null), 0);
  assert.equal(toFiniteNumber(NaN), 0);
  assert.equal(toFiniteNumber(Infinity), 0);
  assert.equal(toFiniteNumber("3"), 0);
  assert.equal(toFiniteNumber(3.5), 3.5);
});

test("sortResults engagement: 分数从高到低，同分保持原始顺序", () => {
  const results = [
    makeResult("xhs", "a", { metrics: { like_count: 100 } }),
    makeResult("xhs", "b", { metrics: { like_count: 5, comment_count: 10 } }), // 5+20=25
    makeResult("xhs", "c", { metrics: {} }), // 0
    makeResult("xhs", "d", { metrics: { like_count: 5, comment_count: 10 } }), // 25（同 b）
    makeResult("xhs", "e", { metrics: { share_count: 30 } }), // 90
  ];
  const sorted = sortResults(results, "engagement");
  assert.deepEqual(sorted.map((r) => r.content_id), ["a", "e", "b", "d", "c"]);
});

test("sortResults engagement: 使用平台内相对互动排名，不跨平台比较绝对数量", () => {
  const results = [
    makeResult("xhs", "x-low", { metrics: { like_count: 10 } }),
    makeResult("douyin", "d-low", { metrics: { like_count: 100000 } }),
    makeResult("xhs", "x-high", { metrics: { like_count: 100 } }),
    makeResult("douyin", "d-high", { metrics: { like_count: 1000000 } }),
  ];
  const sorted = sortResults(results, "engagement");
  assert.deepEqual(sorted.map((r) => r.content_id), ["d-high", "x-high", "d-low", "x-low"]);
});

test("sortResults engagement: 评论和深度互动可打破平台第一名并列", () => {
  const results = [
    makeResult("xhs", "xhs-top", {
      metrics: { like_count: 398, comment_count: 67, collect_count: 319 },
    }),
    makeResult("bilibili", "bili-top", {
      metrics: {
        view_count: 269000,
        comment_count: 551,
        like_count: 9900,
        coin_count: 502,
      },
    }),
  ];
  assert.ok(interactionStrengthScore(results[1].metrics) > interactionStrengthScore(results[0].metrics));
  assert.deepEqual(sortResults(results, "engagement").map((r) => r.content_id), ["bili-top", "xhs-top"]);
});

test("sortResults engagement: 播放量低权重，不能单独压过深度互动", () => {
  const results = [
    makeResult("xhs", "deep", { metrics: { like_count: 1000, comment_count: 100 } }),
    makeResult("bilibili", "views-only", { metrics: { view_count: 100000000 } }),
  ];
  assert.deepEqual(sortResults(results, "engagement").map((r) => r.content_id), ["deep", "views-only"]);
});

test("interactionStrengthScore: 极端数值有限且评论权重高于点赞", () => {
  const comment = interactionStrengthScore({ comment_count: 100 });
  const likes = interactionStrengthScore({ like_count: 1000 });
  assert.ok(comment > likes);
  assert.ok(Number.isFinite(interactionStrengthScore({ view_count: Number.MAX_VALUE })));
});

// ── 10. 平台轮询交错 ──────────────────────────────────────────────────

test("interleaveByPlatform: 按平台顺序轮流取，保持各平台内部顺序", () => {
  const grouped = new Map<PlatformSlug, UnifiedSearchResult[]>([
    ["xhs", [makeResult("xhs", "x1"), makeResult("xhs", "x2"), makeResult("xhs", "x3")]],
    ["bilibili", [makeResult("bilibili", "b1")]],
  ]);
  const merged = interleaveByPlatform(grouped, ["xhs", "bilibili"]);
  assert.deepEqual(merged.map((r) => r.content_id), ["x1", "b1", "x2", "x3"]);
});

test("interleaveByPlatform: 重复结果被去重", () => {
  const grouped = new Map<PlatformSlug, UnifiedSearchResult[]>([
    ["xhs", [makeResult("xhs", "dup"), makeResult("xhs", "x2")]],
    ["douyin", [makeResult("xhs", "dup"), makeResult("douyin", "d1")]], // 跨平台同 content_id 也去重
  ]);
  const merged = interleaveByPlatform(grouped, ["xhs", "douyin"]);
  assert.equal(merged.length, 3);
  // douyin 队列跳过重复的 dup 后同轮取 d1（与后端 interleave_results 一致）
  assert.deepEqual(merged.map((r) => r.content_id), ["dup", "d1", "x2"]);
});

test("deduplicateCrossPlatformResults: 标题规范化后跨平台去重并保留完整代表", () => {
  const results = [
    makeResult("xhs", "x1", {
      title: "iPhone 18 使用一个月真实体验！",
      author: "作者",
      rank: 0,
    }),
    makeResult("douyin", "d1", {
      title: "iphone18使用一个月真实体验",
      snippet: "详细记录使用一个月后的体验。",
      author: "作者",
      rank: 1,
    }),
  ];
  const deduped = deduplicateCrossPlatformResults(results);
  assert.deepEqual(deduped.map((r) => r.content_id), ["d1"]);
});

test("deduplicateCrossPlatformResults: 同作者标题附加修饰被识别为同一内容", () => {
  const results = [
    makeResult("xhs", "x1", {
      author: "秋芝2046",
      title: "全网最全！60分钟全面掌握Claude Code~",
    }),
    makeResult("bilibili", "b1", {
      author: "秋芝2046",
      title: "全网最全！60分钟全面掌握Claude Code~【附完整文档】",
      snippet: "附完整文档和教程。",
    }),
  ];
  assert.equal(deduplicateCrossPlatformResults(results).length, 1);
});

test("deduplicateCrossPlatformResults: 保留 grouped_sources 且代表结果排在第一", () => {
  const results = [
    makeResult("xhs", "x1", { title: "Claude Code 完整教程", author: "作者", rank: 0 }),
    makeResult("bilibili", "b1", {
      title: "Claude Code 完整教程",
      author: "作者",
      snippet: "完整简介",
      rank: 1,
    }),
  ];
  const grouped = deduplicateCrossPlatformResults(results);
  assert.equal(grouped.length, 1);
  assert.deepEqual(
    grouped[0].grouped_sources?.map((source) => source.platform),
    ["bilibili", "xhs"],
  );
  assert.equal(grouped[0].grouped_sources?.[0].url, "https://bilibili.com/b1");
});

test("expandGroupedResultsForPlatform: 综合组在单平台 Tab 展开为原始版本", () => {
  const grouped = deduplicateCrossPlatformResults([
    makeResult("xhs", "x1", { title: "同一内容", author: "作者" }),
    makeResult("bilibili", "b1", { title: "同一内容", author: "作者", rank: 1 }),
    makeResult("douyin", "d1", { title: "同一内容", author: "作者", rank: 2 }),
  ]);
  const bili = expandGroupedResultsForPlatform(grouped, "bilibili");
  assert.deepEqual(bili.map((result) => result.content_id), ["b1"]);
  assert.equal(bili[0].grouped_sources, null);
});

test("deduplicateCrossPlatformResults: A~B、A~C 通过连通组聚类，即使 B~C 不直接匹配也只留一个", () => {
  const results = [
    makeResult("xhs", "a", { author: "秋芝", title: "Claude Code 全面教程" }),
    makeResult("bilibili", "b", { author: "秋芝2046", title: "Claude Code 全面教程安装与配置" }),
    makeResult("douyin", "c", { author: "秋芝_2046", title: "Claude Code 全面教程实战与部署" }),
  ];
  assert.equal(deduplicateCrossPlatformResults(results).length, 1);
});

test("deduplicateCrossPlatformResults: 同作者三个不同视频全部保留", () => {
  const results = [
    makeResult("xhs", "a", { author: "秋芝", title: "Claude Code 入门教程" }),
    makeResult("bilibili", "b", { author: "秋芝2046", title: "Claude Code 调试技巧" }),
    makeResult("douyin", "c", { author: "秋芝_2046", title: "Claude Code 插件开发" }),
  ];
  assert.equal(deduplicateCrossPlatformResults(results).length, 3);
});

test("deduplicateCrossPlatformResults: 危险链式相似只合并安全的一侧", () => {
  const results = [
    makeResult("xhs", "a", { author: "秋芝", title: "Claude Code 入门教程" }),
    makeResult("bilibili", "b", { author: "秋芝", title: "Claude Code 入门教程与配置" }),
    makeResult("douyin", "c", { author: "秋芝", title: "Claude Code 入门与配置" }),
  ];
  // A~B、B~C，但 A/C 的核心标题不同，不应把三条传递并成一组。
  assert.equal(deduplicateCrossPlatformResults(results).length, 2);
});

test("deduplicateCrossPlatformResults: 同作者主题相近但标题核心不同不误删", () => {
  const results = [
    makeResult("xhs", "x1", {
      author: "秋芝2046",
      title: "Claude Code 入门教程与安装",
    }),
    makeResult("bilibili", "b1", {
      author: "秋芝2046",
      title: "Claude Code 进阶实战与部署",
    }),
  ];
  assert.equal(deduplicateCrossPlatformResults(results).length, 2);
});

test("deduplicateCrossPlatformResults: 主题相近但无辅助信号时保留", () => {
  const results = [
    makeResult("xhs", "x1", { title: "iPhone 18 使用一个月真实体验分享" }),
    makeResult("bilibili", "b1", { title: "iPhone 18 使用一个月续航测试" }),
  ];
  assert.equal(deduplicateCrossPlatformResults(results).length, 2);
});

test("deduplicateCrossPlatformResults: 不同平台相同 content_id 不能单独触发去重", () => {
  const results = [
    makeResult("xhs", "same-id", { title: "露营装备清单" }),
    makeResult("douyin", "same-id", { title: "城市通勤装备推荐" }),
  ];
  assert.equal(deduplicateCrossPlatformResults(results).length, 2);
});

test("makeDedupKey 唯一性", () => {
  assert.notEqual(makeDedupKey("xhs", "1"), makeDedupKey("douyin", "1"));
  assert.equal(makeDedupKey("xhs", "1"), makeDedupKey("xhs", "1"));
});

// ── 11. 单平台成功只替换目标平台 ──────────────────────────────────────

test("mergeSinglePlatformRetry: 成功只替换目标平台，其他平台保留，综合顺序重排", () => {
  const prev = interleaveByPlatform(
    new Map([
      ["xhs", [makeResult("xhs", "x1"), makeResult("xhs", "x2")]],
      ["bilibili", [makeResult("bilibili", "b1")]],
      ["zhihu", [makeResult("zhihu", "z1")]],
    ]),
    ["xhs", "bilibili", "zhihu"]
  );
  const newXhs = [makeResult("xhs", "nx1"), makeResult("xhs", "nx2"), makeResult("xhs", "nx3")];
  const merged = mergeSinglePlatformRetry(prev, "xhs", newXhs, ["xhs", "bilibili", "zhihu"]);
  const contentIds = merged.map((r) => r.content_id);
  assert.deepEqual(contentIds, ["nx1", "b1", "z1", "nx2", "nx3"]); // 交错重排
  assert.equal(merged.filter((r) => r.platform === "xhs").length, 3); // 旧 xhs 结果被完全替换
  assert.equal(merged.filter((r) => r.platform === "bilibili").length, 1);
  assert.equal(merged.filter((r) => r.platform === "zhihu").length, 1);
  assert.ok(merged.every((r) => r.content_id !== "x1")); // 旧目标结果不复存在
});

// ── 12. empty 清空目标平台结果 ────────────────────────────────────────

test("mergeSinglePlatformRetry: empty 结果清空目标平台旧结果", () => {
  const prev = interleaveByPlatform(
    new Map([
      ["xhs", [makeResult("xhs", "x1"), makeResult("xhs", "x2")]],
      ["douyin", [makeResult("douyin", "d1")]],
    ]),
    ["xhs", "douyin"]
  );
  const merged = mergeSinglePlatformRetry(prev, "xhs", [], ["xhs", "douyin"]);
  assert.deepEqual(merged.map((r) => r.platform), ["douyin"]);
  assert.equal(merged.filter((r) => r.platform === "xhs").length, 0);
});

test("groupByPlatform 保持平台内部顺序", () => {
  const results = [
    makeResult("xhs", "x2"),
    makeResult("douyin", "d1"),
    makeResult("xhs", "x1"),
  ];
  const grouped = groupByPlatform(results);
  assert.deepEqual(grouped.get("xhs")!.map((r) => r.content_id), ["x2", "x1"]);
  assert.deepEqual(grouped.get("douyin")!.map((r) => r.content_id), ["d1"]);
});

// ── 生产 reducer：搜索体验状态机（Round 12.1/12.2） ────────────────────
// 直接 import 生产 applySearchTransition，覆盖终态提交/身份保护/历史写入/取消提示。

function initState(
  history: SearchHistoryItem[] = [],
  platformPref: PlatformSlug[] = []
): ExperienceState {
  return createInitialExperienceState(history, platformPref);
}

function step(state: ExperienceState, ...events: ExperienceEvent[]): ExperienceState {
  return events.reduce((s, e) => applySearchTransition(s, e), state);
}

/** 发起并接受一个全量任务（POST 返回 job_id 后）。 */
function startFull(
  state: ExperienceState,
  keyword: string,
  jobId: string,
  platforms: PlatformSlug[] = ["xhs", "douyin"]
): ExperienceState {
  return step(
    state,
    { type: "search_start", keyword, platforms },
    {
      type: "search_accepted",
      jobId,
      keyword,
      platforms,
      nowIso: "2026-08-03T00:00:00.000Z",
    }
  );
}

/** 发起并接受一个单平台重试任务。 */
function startRetry(state: ExperienceState, platform: PlatformSlug, jobId: string): ExperienceState {
  return step(state, { type: "retry_start", platform }, { type: "retry_accepted", jobId });
}

/** 页面加载恢复一个后端任务（显式登记身份）。 */
function recoverJob(state: ExperienceState, jobId: string): ExperienceState {
  return step(state, { type: "job_recovered", jobId });
}

test("① 全量开始保留快照：search_start 不丢旧结果，仅标记 refreshing", () => {
  const prevJob = makeJob("job-A", "completed", "旧词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(startFull(initState(), "旧词", "job-A"), { type: "job_terminal", job: prevJob });
  assert.equal(state.display.jobResponse?.keyword, "旧词");

  const next = step(state, { type: "search_start", keyword: "新词", platforms: ["xhs", "douyin"] });
  assert.equal(next.display.jobResponse?.keyword, "旧词"); // 快照保留
  assert.equal(next.display.refreshing, true); // 有旧结果 → 显示"正在更新"
  assert.equal(next.display.cancelledNotice, false);
  assert.equal(next.display.awaitingJobAcceptance, true); // POST 已发出、未被接受
});

test("①b 首次搜索无旧结果：search_start 不标记 refreshing", () => {
  const next = step(initState(), { type: "search_start", keyword: "词", platforms: ["xhs"] });
  assert.equal(next.display.jobResponse, null);
  assert.equal(next.display.refreshing, false);
});

test("② POST 被接受后才写入历史与身份：search_accepted 增加历史并移前", () => {
  const state = startFull(
    initState([h("旧词", ["xhs"], "2026-07-01T00:00:00.000Z")]),
    "新词",
    "job-B"
  );
  assert.equal(state.history.length, 2);
  assert.equal(state.history[0].keyword, "新词"); // 最新在前
  assert.equal(state.display.activeJobId, "job-B");
  assert.equal(state.display.awaitingJobAcceptance, false);
});

test("②b POST 失败不加历史：search_rejected 后历史原样、退出等待", () => {
  const original = [h("旧词", ["xhs"], "2026-07-01T00:00:00.000Z")];
  const state = step(
    initState(original),
    { type: "search_start", keyword: "新词", platforms: ["xhs"] },
    { type: "search_rejected" }
  );
  assert.deepEqual(state.history, original); // 不加历史
  assert.equal(state.display.refreshing, false); // 退出刷新标记
  assert.equal(state.display.awaitingJobAcceptance, false); // 退出等待
  assert.equal(state.display.jobResponse, null); // 快照保留（无旧结果仍为空）
});

test("③ 全量终态替换快照并退出 refreshing", () => {
  const prevJob = makeJob("job-A", "completed", "旧词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(startFull(initState(), "旧词", "job-A"), { type: "job_terminal", job: prevJob });
  const newJob = makeJob("job-B", "completed", "新词", STATUS_OK, [
    makeResult("xhs", "x2"),
    makeResult("douyin", "d1"),
  ]);
  const next = step(
    startFull(state, "新词", "job-B"),
    { type: "job_terminal", job: newJob }
  );
  assert.equal(next.display.jobResponse?.job_id, "job-B");
  assert.equal(next.display.jobResponse?.results.length, 2);
  assert.equal(next.display.refreshing, false);
  assert.ok(next.display.appliedJobIds.has("job-B"));
  assert.equal(next.display.activeJobId, "job-B"); // 提交后保留当前任务身份
});

test("④ 重试成功只替换目标平台：其他平台保留，综合顺序重排", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [
    makeResult("xhs", "x1"),
    makeResult("douyin", "d1"),
  ]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job: prevJob });
  const retryJob = makeJob("job-R", "completed", "词", { ...STATUS_OK, xhs: { status: "succeeded", count: 1 } }, [
    makeResult("xhs", "n1"),
  ]);
  const next = step(startRetry(state, "xhs", "job-R"), { type: "job_terminal", job: retryJob });
  const xhsIds = next.display.jobResponse!.results.filter((r) => r.platform === "xhs");
  const douyinIds = next.display.jobResponse!.results.filter((r) => r.platform === "douyin");
  assert.deepEqual(xhsIds.map((r) => r.content_id), ["n1"]); // 目标平台整体替换
  assert.deepEqual(douyinIds.map((r) => r.content_id), ["d1"]); // 其他平台保留
  assert.equal(next.display.retryingPlatform, null);
  assert.deepEqual(next.display.retryErrors, {});
});

test("⑤ 重试 empty：清空目标平台旧结果", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [
    makeResult("xhs", "x1"),
    makeResult("douyin", "d1"),
  ]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job: prevJob });
  const retryJob = makeJob("job-R", "completed", "词", { ...STATUS_OK, xhs: { status: "empty", count: 0 } }, []);
  const next = step(startRetry(state, "xhs", "job-R"), { type: "job_terminal", job: retryJob });
  assert.equal(next.display.jobResponse!.results.filter((r) => r.platform === "xhs").length, 0);
  assert.deepEqual(next.display.retryErrors, {});
});

// ── 单平台重搜（⟳）：替换当前批次里该平台的位置，不开新批次 ──────────────
// 场景：这一轮没搜小红书，用户点 ⟳ 只重搜它 —— 其它平台不动、小红的旧内容
// 被替换，且**不产生第 2 批**（"换一批"才是往后叠加批次的那个动作）。

test("⑫ 单平台重搜：只替换该平台在当前批次里的位置，其它平台与批次结构不动", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [
    makeResult("xhs", "x1"),
    makeResult("douyin", "d1"),
  ]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job: prevJob });
  const batchesBefore = state.display.jobResponse?.exploration?.previous_batches.length ?? 0;
  // 重搜小红书：响应是它自己的新结果 + 其它平台的既有状态
  const fetchJob = makeJob("job-F", "completed", "词", { ...STATUS_OK, xhs: { status: "succeeded", count: 2 } }, [
    makeResult("xhs", "n1"),
    makeResult("xhs", "n2"),
  ]);
  const next = step(startRetry(state, "xhs", "job-F"), { type: "job_terminal", job: fetchJob });

  const xhs = next.display.jobResponse!.results.filter((r) => r.platform === "xhs");
  assert.deepEqual(xhs.map((r) => r.content_id), ["n1", "n2"]); // 替换（不是追加）
  assert.deepEqual(
    next.display.jobResponse!.results.filter((r) => r.platform === "douyin").map((r) => r.content_id),
    ["d1"] // 其它平台原样保留，还能和重搜结果一起看
  );
  assert.equal(next.display.jobResponse?.keyword, "词");
  assert.equal(next.display.retryingPlatform, null);
  assert.deepEqual(next.display.retryErrors, {});
  // 重搜不是换批：不该多出批次
  assert.equal(next.display.jobResponse?.exploration?.previous_batches.length ?? 0, batchesBefore);
  assert.equal(next.display.jobResponse?.exploration?.round, prevJob.exploration?.round);
});

test("⑬ 单平台重搜平台原本没有结果时：等于把它补进来", () => {
  const prevJob = makeJob("job-A", "completed", "词", { ...STATUS_OK, zhihu: { status: "empty", count: 0 } }, [
    makeResult("xhs", "x1"),
  ]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job: prevJob });
  const fetchJob = makeJob("job-F", "completed", "词", { ...STATUS_OK, zhihu: { status: "succeeded", count: 1 } }, [
    makeResult("zhihu", "z1"),
  ]);
  const next = step(startRetry(state, "zhihu", "job-F"), { type: "job_terminal", job: fetchJob });
  assert.deepEqual(
    next.display.jobResponse!.results.map((r) => `${r.platform}:${r.content_id}`).sort(),
    ["xhs:x1", "zhihu:z1"]
  );
});

test("⑭ 单平台重搜不写历史、不改平台偏好", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job: prevJob });
  const historyBefore = state.history.map((item) => `${item.keyword}|${item.platforms.join(",")}`);
  const prefBefore = [...state.platformPref];
  const next = step(
    state,
    { type: "retry_start", platform: "zhihu" },
    { type: "retry_accepted", jobId: "job-F" },
    { type: "job_terminal", job: makeJob("job-F", "completed", "词", STATUS_OK, [makeResult("zhihu", "z1")]) }
  );
  assert.deepEqual(next.history.map((item) => `${item.keyword}|${item.platforms.join(",")}`), historyBefore);
  assert.deepEqual(next.platformPref, prefBefore);
});

test("⑥ 重试失败：保留旧结果 + 记录安全错误摘要", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [
    makeResult("xhs", "x1"),
    makeResult("douyin", "d1"),
  ]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job: prevJob });
  const failedPlatforms: SearchJobResponse["platforms"] = {
    xhs: { status: "failed", result_count: 0, error_summary: "平台被限流" },
    douyin: { status: "succeeded", result_count: 1, error_summary: null },
    bilibili: { status: "succeeded", result_count: 0, error_summary: null },
    zhihu: { status: "succeeded", result_count: 0, error_summary: null },
  };
  const retryJob = makeJob("job-R", "partial", "词", STATUS_OK, [], { platforms: failedPlatforms });
  const next = step(startRetry(state, "xhs", "job-R"), { type: "job_terminal", job: retryJob });
  // 旧结果原样保留（失败不调用 merge）
  assert.equal(next.display.jobResponse!.results.filter((r) => r.platform === "xhs").length, 1);
  assert.equal(next.display.jobResponse!.results.filter((r) => r.platform === "xhs")[0].content_id, "x1");
  assert.equal(next.display.retryErrors.xhs, "平台被限流");
  assert.equal(next.display.retryingPlatform, null);
});

test("⑥b 新完整搜索清空旧重试错误：search_start 立即清空，全量终态不恢复", () => {
  // 1. 旧任务提交 → 重试失败产生错误
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [
    makeResult("xhs", "x1"),
    makeResult("douyin", "d1"),
  ]);
  const withError = step(
    startFull(initState(), "词", "job-A"),
    { type: "job_terminal", job: prevJob }
  );
  const failedPlatforms: SearchJobResponse["platforms"] = {
    xhs: { status: "failed", result_count: 0, error_summary: "平台被限流" },
    douyin: { status: "succeeded", result_count: 1, error_summary: null },
    bilibili: { status: "succeeded", result_count: 0, error_summary: null },
    zhihu: { status: "succeeded", result_count: 0, error_summary: null },
  };
  const withError2 = step(
    startRetry(withError, "xhs", "job-R1"),
    { type: "job_terminal", job: makeJob("job-R1", "partial", "词", STATUS_OK, [], { platforms: failedPlatforms }) }
  );
  assert.equal(withError2.display.retryErrors.xhs, "平台被限流"); // 重试失败产生错误

  // 2. 发起新完整搜索 → search_start 立即清空
  const started = step(withError2, { type: "search_start", keyword: "新词", platforms: ["xhs"] });
  assert.deepEqual(started.display.retryErrors, {});

  // 3. 新全量终态 → 不会恢复旧错误
  const accepted = step(started, { type: "search_accepted", jobId: "job-B", keyword: "新词", platforms: ["xhs"], nowIso: "2026-08-03T00:00:00.000Z" });
  const done = step(accepted, { type: "job_terminal", job: makeJob("job-B", "completed", "新词", STATUS_OK, [makeResult("xhs", "n1")]) });
  assert.deepEqual(done.display.retryErrors, {});
});

test("⑥c retry_start 只清除目标平台自己的旧错误", () => {
  const withErrors = {
    ...initState(),
    display: {
      ...createInitialExperienceState().display,
      retryErrors: { xhs: "xhs 错误", douyin: "douyin 错误" } as Partial<Record<PlatformSlug, string>>,
    },
  };
  const next = step(withErrors, { type: "retry_start", platform: "xhs" });
  assert.deepEqual(next.display.retryErrors, { douyin: "douyin 错误" }); // 只清 xhs
});

test("⑦ 重试创建被拒（POST 失败）：记录失败摘要并退出重试态", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(
    startFull(initState(), "词", "job-A"),
    { type: "job_terminal", job: prevJob },
    { type: "retry_start", platform: "xhs" },
    { type: "search_rejected", errorSummary: "已有任务正在运行，请等待完成后再试。" }
  );
  assert.equal(state.display.retryingPlatform, null);
  assert.equal(state.display.retryErrors.xhs, "已有任务正在运行，请等待完成后再试。");
  assert.equal(state.display.jobResponse!.results.length, 1); // 快照保留
  assert.equal(state.display.awaitingJobAcceptance, false);
});

test("⑧ 取消终态：保留旧结果 + cancelledNotice 只在真实终态出现", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job: prevJob });
  // 点击取消本身不改任何状态（不提前显示提示）
  const clicked = startFull(state, "新词", "job-C", ["xhs"]);
  const cancelledJob = makeJob("job-C", "cancelled", "新词", STATUS_OK, []);
  const next = step(clicked, { type: "job_terminal", job: cancelledJob });
  assert.equal(next.display.cancelledNotice, true);
  assert.equal(next.display.jobResponse?.keyword, "词"); // 旧结果保留
  assert.equal(next.display.refreshing, false);
  // 新搜索清除提示
  const afterNewSearch = step(next, { type: "search_start", keyword: "更新", platforms: ["xhs"] });
  assert.equal(afterNewSearch.display.cancelledNotice, false);
});

test("⑧b 非 cancelled 终态绝不产生取消提示；取消失败（任务完成）不显示", () => {
  const state = step(
    startFull(initState(), "词", "job-D"),
    { type: "job_terminal", job: makeJob("job-D", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]) }
  );
  assert.equal(state.display.cancelledNotice, false);
});

test("⑨ 同一 job_id 终态重复到达只应用一次（幂等，原引用返回）", () => {
  const job = makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(startFull(initState(), "词", "job-A"), { type: "job_terminal", job });
  const again = applySearchTransition(state, { type: "job_terminal", job });
  assert.equal(again, state); // 原引用：React 不会重渲染
  assert.ok(again.display.appliedJobIds.has("job-A"));
});

test("⑩ 身份保护完整序列：A 从未提交 → 接受 B → A 迟到被拒 → B 提交 → A 再迟到仍被拒", () => {
  const jobA = makeJob("job-A", "completed", "旧词", STATUS_OK, [makeResult("xhs", "x1")]);
  const jobB = makeJob("job-B", "completed", "新词", STATUS_OK, [makeResult("xhs", "x2")]);

  // 1-2. A 从未提交过终态；开始并接受 B
  const stateB = startFull(initState(), "新词", "job-B");

  // 3-4. A 的终态迟到 → 拒绝（activeJobId=job-B ≠ job-A），展示不受影响
  const lateA1 = applySearchTransition(stateB, { type: "job_terminal", job: jobA });
  assert.equal(lateA1.display.jobResponse, null); // 未提交任何结果

  // 5. B 的终态被接受
  const doneB = applySearchTransition(lateA1, { type: "job_terminal", job: jobB });
  assert.equal(doneB.display.jobResponse?.job_id, "job-B");

  // 6. B 终态后 A 再迟到仍被拒绝
  const lateA2 = applySearchTransition(doneB, { type: "job_terminal", job: jobA });
  assert.equal(lateA2.display.jobResponse?.job_id, "job-B"); // 未被 A 覆盖
});

test("⑩b 等待接受期间（awaiting）任何终态被拒绝：无身份终态", () => {
  const state = step(initState(), { type: "search_start", keyword: "新词", platforms: ["xhs"] });
  assert.equal(state.display.awaitingJobAcceptance, true);
  const rejected = applySearchTransition(state, {
    type: "job_terminal",
    job: makeJob("job-X", "completed", "新词", STATUS_OK, [makeResult("xhs", "x1")]),
  });
  assert.equal(rejected.display.jobResponse, null); // 未接受前的终态不提交
});

test("⑪ 页面恢复任务 R 可正常提交；恢复后新任务不受旧 R 干扰", () => {
  const jobR = makeJob("job-R", "completed", "恢复词", STATUS_OK, [makeResult("xhs", "r1")]);
  // 恢复运行中的任务：先登记身份，再等终态
  const recovered = recoverJob(initState(), "job-R");
  assert.equal(recovered.display.activeJobId, "job-R");
  const doneR = step(recovered, { type: "job_terminal", job: jobR });
  assert.equal(doneR.display.jobResponse?.job_id, "job-R");

  // 恢复后用户发起新任务：B 终态接受，旧 R 迟到被拒
  const jobB = makeJob("job-B", "completed", "新词", STATUS_OK, [makeResult("xhs", "b1")]);
  const stateB = step(
    startFull(doneR, "新词", "job-B"),
    { type: "job_terminal", job: jobB }
  );
  assert.equal(stateB.display.jobResponse?.job_id, "job-B");
  const lateR = applySearchTransition(stateB, { type: "job_terminal", job: jobR });
  assert.equal(lateR.display.jobResponse?.job_id, "job-B"); // 旧 R 不能覆盖
});

test("⑫ reset 后旧任务终态不能恢复已清空页面", () => {
  const jobA = makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(
    startFull(initState(), "词", "job-A"),
    { type: "job_terminal", job: jobA },
    { type: "reset" }
  );
  assert.equal(state.display.jobResponse, null);
  assert.equal(state.display.activeJobId, null);
  assert.equal(state.display.awaitingJobAcceptance, false);
  // reset 后迟到的旧任务终态 → 无身份，拒绝
  const late = applySearchTransition(state, { type: "job_terminal", job: jobA });
  assert.equal(late.display.jobResponse, null); // 页面保持已清空
  assert.deepEqual(late.display.retryErrors, {});
});

test("⑬ reset 清除任务与展示；历史与平台偏好保留", () => {
  const history = [h("旧词", ["xhs"], "2026-07-01T00:00:00.000Z")];
  const pref: PlatformSlug[] = ["xhs", "douyin"];
  const state = step(
    initState(history, pref),
    { type: "job_recovered", jobId: "job-A" },
    { type: "job_terminal", job: makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]) },
    { type: "reset" }
  );
  assert.equal(state.display.jobResponse, null);
  assert.equal(state.display.refreshing, false);
  assert.equal(state.display.cancelledNotice, false);
  assert.equal(state.display.activeJobId, null);
  assert.deepEqual(state.history, history); // 历史保留
  assert.deepEqual(state.platformPref, pref); // 平台偏好保留
});

test("⑭ 历史删除/清空走 reducer；platform_pref_set 过滤非法且空集保持已落地偏好", () => {
  const history = [h("a", ["xhs"], "2026-07-01T00:00:00.000Z"), h("b", ["douyin"], "2026-07-02T00:00:00.000Z")];
  const state = initState(history, ["xhs"]);
  const removed = step(state, { type: "history_remove", index: 0 });
  assert.deepEqual(removed.history.map((i) => i.keyword), ["b"]);
  const cleared = step(state, { type: "history_clear" });
  assert.deepEqual(cleared.history, []);
  // 测试防御性过滤：事件类型声明为合法 slug，测试用断言注入非法值。
  const prefSet = step(state, { type: "platform_pref_set", platforms: ["xhs", "bogus", "xhs", "zhihu"] as PlatformSlug[] });
  assert.deepEqual(prefSet.platformPref, ["xhs", "zhihu"]); // 过滤非法 + 去重
  // 新规则：首次进入（readPlatformPref 无存储值）才允许空集；偏好一旦落地
  // （这里初始为 ["xhs"]），reducer 的 platform_pref_set 仍维持"至少一个平台"
  // —— 传入空集时保留已落地的原偏好，避免把已有偏好清空。UI 层始终允许手动
  // 取消到零，只是提交时统一提示"先选平台"（见 SearchBar）。
  const prefEmpty = step(state, { type: "platform_pref_set", platforms: [] });
  assert.deepEqual(prefEmpty.platformPref, ["xhs"]); // 空集保持已落地偏好（至少一个平台）
});

test("⑭b readPlatformPref 无存储值：首次进入允许零勾选（返回空集）", () => {
  // 与"损坏时默认全选"不同：key 完全不存在才是首次进入，返回空集而不是全选。
  const storage = new MemoryStorage();
  assert.deepEqual(readPlatformPref(storage), []);
});

test("⑭c readPlatformPref 损坏值仍回退全选（不被首次进入的空集规则影响）", () => {
  const storage = new MemoryStorage();
  storage.setItem(PLATFORM_PREF_STORAGE_KEY, "###broken###");
  assert.deepEqual(readPlatformPref(storage), [...PLATFORM_SLUGS]);
});

// ── Round 13：取消请求 / 取消失败 / 真实取消终态 ────────────────────────
// 生产 reducer 的 cancel_requested / cancel_rejected 事件 + cancelRequested /
// cancelError 状态（固定安全文案、不伪造终态、不清快照、身份保护不变）。

test("Ⓐ cancel_requested 不提前显示已取消：标记请求中，快照与 cancelledNotice 不动", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(
    startFull(initState(), "词", "job-A"),
    { type: "job_terminal", job: prevJob }
  );
  const clicked = applySearchTransition(state, { type: "cancel_requested" });
  assert.equal(clicked.display.cancelRequested, true);
  assert.equal(clicked.display.cancelledNotice, false); // 不提前亮"已取消"
  assert.equal(clicked.display.jobResponse, state.display.jobResponse); // 快照原引用不动
  assert.equal(clicked.display.activeJobId, "job-A"); // 任务身份不变
});

test("Ⓑ cancel_rejected 保留快照并记录安全错误：不伪造终态、任务身份不变", () => {
  const prevJob = makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(
    startFull(initState(), "词", "job-A"),
    { type: "job_terminal", job: prevJob }
  );
  const failed = applySearchTransition(state, {
    type: "cancel_rejected",
    safeMessage: "取消失败，当前搜索仍在继续，可稍后重试",
  });
  assert.equal(failed.display.cancelError, "取消失败，当前搜索仍在继续，可稍后重试");
  assert.equal(failed.display.cancelRequested, false);
  assert.equal(failed.display.cancelledNotice, false); // 不伪造已取消终态
  assert.equal(failed.display.jobResponse, state.display.jobResponse); // 旧结果保留
  assert.equal(failed.display.activeJobId, "job-A"); // 身份不变，后续终态仍可提交
});

test("Ⓒ 取消失败后任务正常 completed：正常提交结果并清除取消失败提示", () => {
  const state = step(
    startFull(initState(), "词", "job-A"),
    { type: "cancel_rejected", safeMessage: "取消失败，当前搜索仍在继续，可稍后重试" }
  );
  const done = applySearchTransition(state, {
    type: "job_terminal",
    job: makeJob("job-A", "completed", "词", STATUS_OK, [makeResult("xhs", "x1")]),
  });
  assert.equal(done.display.cancelError, null); // 正常终态清除取消失败提示
  assert.equal(done.display.cancelRequested, false);
  assert.equal(done.display.cancelledNotice, false);
  assert.equal(done.display.jobResponse?.overall, "completed"); // 结果正常提交
});

test("Ⓓ 取消失败后允许再次取消：cancel_requested 清空旧错误并重新标记", () => {
  const state = step(
    startFull(initState(), "词", "job-A"),
    { type: "cancel_rejected", safeMessage: "取消失败，当前搜索仍在继续，可稍后重试" }
  );
  const retry = applySearchTransition(state, { type: "cancel_requested" });
  assert.equal(retry.display.cancelError, null); // 重试前清空旧错误
  assert.equal(retry.display.cancelRequested, true);
  assert.equal(retry.display.jobResponse, state.display.jobResponse); // 快照保留
});

test("Ⓔ 后端 cancelling 期间保持 busy：isSearchBlocked 覆盖取消中状态", () => {
  assert.equal(
    isSearchBlocked({ isCreating: false, isRunning: false, isCancelling: true, retryingPlatform: null }),
    true // 取消清理期间禁止搜索框/历史/平台/重试
  );
  assert.equal(
    isSearchBlocked({ isCreating: false, isRunning: false, isCancelling: false, retryingPlatform: null }),
    false // 取消结束后解锁
  );
});

test("Ⓕ 真实 cancelled 终态才显示 cancelledNotice：同时清除取消请求标记", () => {
  const prevJob = makeJob("job-A", "completed", "旧词", STATUS_OK, [makeResult("xhs", "x1")]);
  const state = step(startFull(initState(), "旧词", "job-A"), { type: "job_terminal", job: prevJob });
  // 新任务开始并被接受（真实流程：POST 返回新 job_id）
  const inFlight = startFull(state, "新词", "job-C", ["xhs", "douyin"]);
  const clicked = applySearchTransition(inFlight, { type: "cancel_requested" });
  assert.equal(clicked.display.cancelledNotice, false); // 请求中不亮
  const cancelledJob = makeJob("job-C", "cancelled", "新词", STATUS_OK, []);
  const next = applySearchTransition(clicked, { type: "job_terminal", job: cancelledJob });
  assert.equal(next.display.cancelledNotice, true); // 只有真实终态才亮
  assert.equal(next.display.cancelRequested, false);
  assert.equal(next.display.cancelError, null);
  assert.equal(next.display.jobResponse?.keyword, "旧词"); // 旧快照保留
});

test("Ⓖ reset / 新搜索清除取消失败提示", () => {
  const state = step(
    startFull(initState(), "词", "job-A"),
    { type: "cancel_rejected", safeMessage: "取消失败，当前搜索仍在继续，可稍后重试" }
  );
  const reset = applySearchTransition(state, { type: "reset" });
  assert.equal(reset.display.cancelError, null);
  assert.equal(reset.display.cancelRequested, false);
  const newSearch = applySearchTransition(reset, { type: "search_start", keyword: "新词", platforms: ["xhs"] });
  assert.equal(newSearch.display.cancelError, null);
  assert.equal(newSearch.display.cancelRequested, false);
});

test("Ⓗ 旧任务的取消终态不能覆盖当前新任务（身份 guard 依然有效）", () => {
  const stateB = startFull(initState(), "新词", "job-B", ["xhs"]);
  // 旧任务 job-A 的取消终态迟到 → job_id 与 activeJobId 不匹配，拒绝
  const lateCancel = applySearchTransition(stateB, {
    type: "job_terminal",
    job: makeJob("job-A", "cancelled", "旧词", STATUS_OK, []),
  });
  assert.equal(lateCancel.display.cancelledNotice, false); // 不被旧取消污染
  assert.equal(lateCancel.display.jobResponse, null); // 新任务结果未被覆盖
  assert.equal(lateCancel.display.activeJobId, "job-B"); // 当前身份不变
  // 当前任务正常终态仍可提交
  const doneB = applySearchTransition(lateCancel, {
    type: "job_terminal",
    job: makeJob("job-B", "completed", "新词", STATUS_OK, [makeResult("xhs", "b1")]),
  });
  assert.equal(doneB.display.jobResponse?.overall, "completed");
  assert.equal(doneB.display.cancelledNotice, false);
});

// ── 平台标签（activeTab）回退（生产纯函数） ────────────────────────────

test("resolveActiveTab: 激活标签不可见时回退到 all", () => {
  assert.equal(resolveActiveTab("bilibili", ["all", "xhs", "douyin"], "all"), "all");
  assert.equal(resolveActiveTab("xhs", ["all", "xhs", "douyin"], "all"), "xhs");
  assert.equal(resolveActiveTab("all", ["all", "xhs"], "all"), "all");
  assert.equal(resolveActiveTab(null, ["all", "xhs"], "all"), "all");
  assert.equal(resolveActiveTab(undefined, ["all"], "all"), "all");
});

// ── 17. busy 状态拒绝历史回放和重试 ───────────────────────────────────

test("isSearchBlocked: 搜索/取消/重试进行中禁止新任务", () => {
  assert.equal(isSearchBlocked({ isCreating: true, isRunning: false, isCancelling: false, retryingPlatform: null }), true);
  assert.equal(isSearchBlocked({ isCreating: false, isRunning: true, isCancelling: false, retryingPlatform: null }), true);
  assert.equal(isSearchBlocked({ isCreating: false, isRunning: false, isCancelling: true, retryingPlatform: null }), true);
  assert.equal(isSearchBlocked({ isCreating: false, isRunning: false, isCancelling: false, retryingPlatform: "xhs" }), true);
  assert.equal(isSearchBlocked({ isCreating: false, isRunning: false, isCancelling: false, retryingPlatform: null }), false);
});

// ── 补充：安全错误摘要 ────────────────────────────────────────────────

test("safeErrorSummary: 409/404 固定文案，detail 拼接，不含响应体", () => {
  assert.equal(
    safeErrorSummary({ response: { status: 409, data: { detail: "A search job is already running." } } }),
    "已有任务正在运行，请等待完成后再试。"
  );
  assert.equal(
    safeErrorSummary({ response: { status: 404, data: {} } }),
    "任务已失效，请重新搜索。"
  );
  assert.equal(
    safeErrorSummary({ response: { status: 422, data: { detail: [{ msg: "platform 无效" }, { msg: "keyword 为空" }] } } }),
    "platform 无效; keyword 为空"
  );
  assert.equal(safeErrorSummary({ message: "Network Error" }), "Network Error");
  assert.equal(safeErrorSummary({}), "请求失败，请重试。");
});

// ── 补充：历史规范化 ──────────────────────────────────────────────────

test("normalizeHistoryKeyword: trim + 小写", () => {
  assert.equal(normalizeHistoryKeyword("  Hello World  "), "hello world");
});

// ── 补充：recomputeOverall ─────────────────────────────────────────────

test("recomputeOverall: 全成功 → completed，混合 → partial，全失败 → failed", () => {
  assert.equal(recomputeOverall(["succeeded", "empty"]), "completed");
  assert.equal(recomputeOverall(["succeeded", "failed"]), "partial");
  assert.equal(recomputeOverall(["failed", "timed_out"]), "failed");
  assert.equal(recomputeOverall([]), "failed");
});

// ── 补充：排序模式联合类型（编译期验证） ──────────────────────────────

test("SearchSortMode 支持三种模式", () => {
  const modes: SearchSortMode[] = ["default", "latest", "engagement"];
  assert.equal(modes.length, 3);
});
