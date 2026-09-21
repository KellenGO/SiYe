import { test } from "node:test";
import assert from "node:assert/strict";
import {
  collectionMembership,
  decideMigration,
  describeImport,
  legacyBookmarkCount,
  parseBackupFile,
  splitKey,
  toAddPayload,
  toCollections,
  toLibraryItems,
  toLibraryItem,
  type LibraryItem,
} from "../src/lib/libraryApi.js";
import { MAX_BACKUP_BYTES } from "../src/lib/bookmarks.js";
import { parseGroupKey } from "../src/lib/resultTools.js";
import type { UnifiedSearchResult } from "../src/types/search.js";

/** 本仓库 tsconfig 下 assert 只暴露部分方法，这里统一用 JSON 比较与正则 test。 */
function jsonEqual(actual: unknown, expected: unknown): void {
  assert.equal(JSON.stringify(actual), JSON.stringify(expected));
}

function expectItem(value: unknown): LibraryItem {
  const item = toLibraryItem(value);
  assert.ok(item !== null);
  if (item === null) throw new Error("expected a parsed item");
  return item;
}

/** 本仓库 tsconfig 下 assert 只暴露部分方法：用 try/catch 断言抛错原因。 */
function expectThrow(fn: () => unknown, pattern: RegExp): void {
  let message = "";
  let threw = false;
  try {
    fn();
  } catch (error) {
    threw = true;
    message = error instanceof Error ? error.message : String(error);
  }
  assert.ok(threw);
  assert.ok(pattern.test(message));
}

function result(id: string, overrides: Partial<UnifiedSearchResult> = {}): UnifiedSearchResult {
  return {
    platform: "xhs", content_id: id, content_type: "note", title: `标题 ${id}`,
    author: "作者", url: `https://www.xiaohongshu.com/explore/${id}`,
    published_at: "2026-09-05T12:00:00Z", snippet: "摘要", metrics: { like_count: 3 },
    cover_url: null, rank: 0, ...overrides,
  };
}

/** 后端 /api/library/items 返回的条目形状。 */
function apiItem(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 7, key: "xhs|n1", result: result("n1"), saved_at: "2026-09-01T00:00:00Z",
    fetched_at: "2026-09-02T00:00:00Z", note: "我的备注",
    in_default: true, watch_later: false,
    collections: [{ id: 2, name: "AI 学习" }, { id: 3, name: "待看" }],
    ...overrides,
  };
}

// ── 转换 ────────────────────────────────────────────────────────────────

test("toLibraryItem：把后端 snake_case 转成前端 camelCase，并保留收藏夹归属", () => {
  const item = expectItem(apiItem());
  assert.equal(item.id, 7);
  assert.equal(item.key, "xhs|n1");
  assert.equal(item.savedAt, "2026-09-01T00:00:00Z");
  assert.equal(item.fetchedAt, "2026-09-02T00:00:00Z");
  assert.equal(item.note, "我的备注");
  assert.equal(item.inDefault, true);
  assert.equal(item.watchLater, false);
  jsonEqual(item.collections.map((tag) => tag.name), ["AI 学习", "待看"]);
  assert.equal(item.result.title, "标题 n1");
});

test("toLibraryItem：同一条内容可属于多个收藏夹（多对多不被压平）", () => {
  assert.equal(expectItem(apiItem()).collections.length, 2);
});

test("toLibraryItem：格式非法返回 null，不抛异常（单条坏数据不整页崩）", () => {
  assert.equal(toLibraryItem(null), null);
  assert.equal(toLibraryItem({ id: 1 }), null);
  assert.equal(toLibraryItem({ id: 1, result: { platform: "nope", content_id: "x" } }), null);
  assert.equal(toLibraryItem({ id: 1, result: { ...result("n1"), url: "javascript:alert(1)" } }), null);
});

test("toLibraryItem：缺 saved_at / fetched_at / collections 时给出安全默认值", () => {
  const item = expectItem({ id: 1, result: result("n1") });
  assert.equal(item.key, "xhs|n1");
  assert.equal(item.fetchedAt, null);
  assert.equal(item.note, "");
  assert.equal(item.inDefault, true);
  assert.equal(item.watchLater, false);
  jsonEqual(item.collections, []);
  assert.ok(Number.isFinite(Date.parse(item.savedAt)));
});

test("toLibraryItems：过滤坏数据并接受空输入", () => {
  const items = toLibraryItems({ items: [apiItem(), { id: 9 }, apiItem({ key: "douyin|b" })] });
  assert.equal(items.length, 2);
  jsonEqual(items.map((item) => item.key), ["xhs|n1", "douyin|b"]);
  jsonEqual(toLibraryItems(null), []);
  jsonEqual(toLibraryItems({}), []);
});

test("toCollections：解析收藏夹并丢弃无 id / 无名称的行", () => {
  const collections = toCollections({
    collections: [
      { id: 1, name: "AI 学习", item_count: 3 },
      { id: 0, name: "坏行" },
      { id: 2, name: "" },
      { id: 3, name: "待看" },
    ],
  });
  jsonEqual(collections, [
    { id: 1, name: "AI 学习", item_count: 3 },
    { id: 3, name: "待看", item_count: 0 },
  ]);
});

// ── 迁移判定 ────────────────────────────────────────────────────────────

test("legacyBookmarkCount：解析旧 localStorage 备份条数，坏数据算 0", () => {
  assert.equal(legacyBookmarkCount(null), 0);
  assert.equal(legacyBookmarkCount("不是 JSON"), 0);
  assert.equal(legacyBookmarkCount(JSON.stringify({ version: 1 })), 0);
  assert.equal(legacyBookmarkCount(JSON.stringify({ version: 1, items: [{}, {}] })), 2);
});

test("decideMigration：有旧数据且未迁移才提示；迁移过就不再打扰", () => {
  const legacy = JSON.stringify({ version: 1, items: [{}] });
  jsonEqual(decideMigration({ legacyRaw: legacy, alreadyMigrated: false }), { shouldOffer: true, count: 1 });
  jsonEqual(decideMigration({ legacyRaw: legacy, alreadyMigrated: true }), { shouldOffer: false, count: 0 });
  jsonEqual(decideMigration({ legacyRaw: null, alreadyMigrated: false }), { shouldOffer: false, count: 0 });
});

// ── 备份解析 ────────────────────────────────────────────────────────────

test("parseBackupFile：接受 v1、v2 与当前 v3 备份结构", () => {
  jsonEqual(parseBackupFile(JSON.stringify({ version: 1, items: [] })), { version: 1, items: [] });
  jsonEqual(parseBackupFile(JSON.stringify({ version: 2, items: [], collections: [] })), { version: 2, items: [], collections: [] });
  jsonEqual(parseBackupFile(JSON.stringify({ version: 3, items: [], collections: [] })), { version: 3, items: [], collections: [] });
});

test("parseBackupFile：拒绝非 JSON、缺 items 与超大文件", () => {
  expectThrow(() => parseBackupFile("不是 JSON"), /JSON/);
  expectThrow(() => parseBackupFile(JSON.stringify({ version: 1 })), /items/);
  const huge = JSON.stringify({ items: ["x".repeat(MAX_BACKUP_BYTES)] });
  expectThrow(() => parseBackupFile(huge), /10 MB/);
});

// ── 收藏载荷 ────────────────────────────────────────────────────────────

test("toAddPayload：分组卡片按来源展开，并按 platform|content_id 去重", () => {
  const note = result("n1");
  const video: UnifiedSearchResult = { ...result("b1"), platform: "bilibili", url: "https://www.bilibili.com/video/BV1" };
  const grouped: UnifiedSearchResult = { ...note, grouped_sources: [note, video] };

  const entries = toAddPayload([grouped, note], { xhs: "2026-09-03T00:00:00Z" });

  jsonEqual(entries.map((entry) => entry.result.platform), ["xhs", "bilibili"]);
  assert.equal(entries[0].fetched_at, "2026-09-03T00:00:00Z");
  assert.equal(entries[1].fetched_at, null);
});

test("toAddPayload：非法链接被跳过而不是抛错", () => {
  const bad = { ...result("bad"), url: "javascript:alert(1)" } as UnifiedSearchResult;
  jsonEqual(toAddPayload([bad]), []);
});

// ── 提示文案 ────────────────────────────────────────────────────────────

test("describeImport：分别覆盖新增、更新、跳过与空结果", () => {
  const text = describeImport({ added: 3, updated: 1, skipped: 2 });
  assert.ok(/新增 3 条/.test(text));
  assert.ok(/更新 1 条/.test(text));
  assert.ok(/跳过 2 条/.test(text));
  assert.ok(/无效或收藏数量已达上限/.test(describeImport({ added: 0, updated: 0, skipped: 5 })));
  assert.ok(/没有可导入的内容/.test(describeImport({ added: 0, updated: 0, skipped: 0 })));
});

// ── 收藏后编辑归属：一批条目的勾选三态 ────────────────────────────────

test("toLibraryItem：saved 是独立状态，后端没给时按收藏夹归属兜底推断", () => {
  assert.equal(expectItem(apiItem({ saved: true, in_default: false, collections: [] })).saved, true);
  assert.equal(expectItem(apiItem({ saved: false, watch_later: true })).saved, false);
  assert.equal(expectItem(apiItem()).saved, true);
  assert.equal(expectItem(apiItem({ in_default: false, collections: [] })).saved, false);
});

test("collectionMembership：单条条目按自身归属给出勾选状态", () => {
  const state = collectionMembership([expectItem(apiItem())]);
  assert.equal(state.inDefault, true);
  assert.equal(state.watchLater, false);
  assert.equal(state.custom[2], true);
  assert.equal(state.custom[3], true);
  assert.equal(state.custom[99], undefined);
});

test("collectionMembership：一批条目只有部分属于某收藏夹时是半选（null）", () => {
  const items = [
    expectItem(apiItem({ key: "xhs|n1", in_default: true, collections: [{ id: 2, name: "AI 学习" }] })),
    expectItem(apiItem({ key: "douyin|d1", in_default: true, watch_later: true, collections: [] })),
  ];
  const state = collectionMembership(items);
  assert.equal(state.inDefault, true);
  assert.equal(state.watchLater, null);
  assert.equal(state.custom[2], null);
});

test("collectionMembership：空批次不算选中，避免凭空勾上收藏夹", () => {
  const state = collectionMembership([]);
  assert.equal(state.inDefault, false);
  assert.equal(state.watchLater, false);
  jsonEqual(state.custom, {});
});

// ── groupKey 还原（批量加入/移出收藏夹的关键路径） ─────────────────────

test("parseGroupKey：把结果列表的分组勾选还原成收藏库的单条 key", () => {
  jsonEqual(parseGroupKey('["xhs|n1"]'), ["xhs|n1"]);
  jsonEqual(parseGroupKey('["xhs|n1","bilibili|b1"]'), ["xhs|n1", "bilibili|b1"]);
  jsonEqual(parseGroupKey('["bilibili|BV1|extra"]'), ["bilibili|BV1|extra"]);
});

test("parseGroupKey：垃圾输入返回空数组（调用方据此提示而不是发坏请求）", () => {
  jsonEqual(parseGroupKey("不是 JSON"), []);
  jsonEqual(parseGroupKey(JSON.stringify({ not: "array" })), []);
  jsonEqual(parseGroupKey(JSON.stringify([1, null, "xhs|ok"])), ["xhs|ok"]);
  jsonEqual(parseGroupKey('["no-separator"]'), []);
});

// ── key 解析 ────────────────────────────────────────────────────────────

test("splitKey：只在第一个分隔符切开，content_id 里可以有竖线", () => {
  jsonEqual(splitKey("xhs|n1"), { platform: "xhs", content_id: "n1" });
  jsonEqual(splitKey("bilibili|BV1|extra"), { platform: "bilibili", content_id: "BV1|extra" });
  assert.equal(splitKey("n1"), null);
  assert.equal(splitKey("|n1"), null);
  assert.equal(splitKey("xhs|"), null);
});
