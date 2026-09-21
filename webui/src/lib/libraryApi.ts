/**
 * 收藏库 API 客户端（后端 /api/library）。
 *
 * 收藏不再放在浏览器 localStorage，而是存本机 SQLite：清缓存、换浏览器都不会丢，
 * 并且支持收藏夹（一条内容可同时属于多个收藏夹）。
 *
 * 这个文件分两层，便于测试：
 * - 纯函数：字段转换、迁移判定、备份解析（tests/libraryApi.test.ts 覆盖）；
 * - IO 函数：axios 调用后端（薄封装，不做业务判断）。
 */

import axios from "axios";

import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { MAX_BACKUP_BYTES, MAX_NOTE_LENGTH, publicResult } from "./bookmarks.js";
import { resultKey, resultSources } from "./resultTools.js";

export const LIBRARY_API_BASE = "/api/library";
/** 旧版本存浏览器 localStorage 的 key。 */
export const LEGACY_BOOKMARKS_KEY = "aggregate_search_bookmarks_v1";
/** 迁移完成标记：写入后不再重复提示迁移（旧数据本身保留不动）。 */
export const MIGRATION_MARKER_KEY = "aggregate_search_library_migrated_v1";
/** 「以后再说」只在本次会话内隐藏迁移提示（sessionStorage），下次打开仍会提示。 */
export const MIGRATION_DISMISS_KEY = "aggregate_search_library_migration_dismissed";

export interface LibraryTag {
  id: number;
  name: string;
}

export interface LibraryItem {
  id: number;
  key: string;
  result: UnifiedSearchResult;
  savedAt: string;
  fetchedAt: string | null;
  note: string;
  collections: LibraryTag[];
  inDefault: boolean;
  watchLater: boolean;
  /**
   * 是否「已收藏」：决定这条内容出现在不在「全部」里。
   * 与 inDefault（是否放在默认收藏夹）是两件独立的事——一条内容可以
   * 既不在默认收藏夹也不在任何自建夹，只要收藏过就仍在「全部」里。
   */
  saved: boolean;
}

export interface LibraryCollection extends LibraryTag {
  item_count: number;
}

export interface LibraryStats {
  total: number;
  /** 已收藏条数（「全部」视图的口径，不含只挂在稍后再看的内容）。 */
  saved_count: number;
  unclassified: number;
  default_count: number;
  watch_later_count: number;
  collections: number;
  db_path: string;
  migration?: { id?: string; source_count?: number; local_items?: number; remote_items?: number; warnings?: string[] } | null;
}

export interface ImportStats {
  added: number;
  updated: number;
  skipped: number;
  total?: number;
  total_requested?: number;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

// ── 纯函数：转换与判定 ──────────────────────────────────────────────────

/** 后端 item -> 前端 LibraryItem；格式不合法时返回 null（跳过而不是整页崩）。 */
export function toLibraryItem(raw: unknown): LibraryItem | null {
  if (!isRecord(raw)) return null;
  let result: UnifiedSearchResult;
  try {
    result = publicResult(raw.result);
  } catch {
    return null;
  }
  const tags = Array.isArray(raw.collections)
    ? raw.collections
        .filter(isRecord)
        .map((tag) => ({ id: Number(tag.id) || 0, name: String(tag.name ?? "") }))
        .filter((tag) => tag.name.length > 0)
    : [];
  return {
    id: Number(raw.id) || 0,
    key: typeof raw.key === "string" && raw.key ? raw.key : resultKey(result),
    result,
    savedAt: typeof raw.saved_at === "string" ? raw.saved_at : new Date().toISOString(),
    fetchedAt: typeof raw.fetched_at === "string" ? raw.fetched_at : null,
    note: typeof raw.note === "string" ? raw.note.slice(0, MAX_NOTE_LENGTH) : "",
    collections: tags,
    inDefault: typeof raw.in_default === "boolean" ? raw.in_default : tags.length === 0,
    watchLater: raw.watch_later === true,
    // 后端没给就按旧规则推断：在默认收藏夹或有自建归属的算已收藏。
    saved: typeof raw.saved === "boolean"
      ? raw.saved
      : (typeof raw.in_default === "boolean" ? raw.in_default : tags.length === 0) || tags.length > 0,
  };
}

export function toLibraryItems(raw: unknown): LibraryItem[] {
  const list = isRecord(raw) && Array.isArray(raw.items) ? raw.items : [];
  return list.map(toLibraryItem).filter((item): item is LibraryItem => item !== null);
}

export function toCollections(raw: unknown): LibraryCollection[] {
  const list = isRecord(raw) && Array.isArray(raw.collections) ? raw.collections : [];
  return list
    .filter(isRecord)
    .map((row) => ({
      id: Number(row.id) || 0,
      name: String(row.name ?? ""),
      item_count: Number(row.item_count) || 0,
    }))
    .filter((row) => row.id > 0 && row.name.length > 0);
}

/** 旧 localStorage 备份里有多少条收藏（无法解析时返回 0，不阻塞页面）。 */
export function legacyBookmarkCount(raw: string | null): number {
  if (!raw) return 0;
  try {
    const data: unknown = JSON.parse(raw);
    if (!isRecord(data) || !Array.isArray(data.items)) return 0;
    return data.items.length;
  } catch {
    return 0;
  }
}

export interface MigrationDecision {
  shouldOffer: boolean;
  count: number;
}

/**
 * 是否需要提示迁移旧收藏。
 * 只在「有旧数据 + 未迁移过」时提示；迁移成功后写标记，旧数据保留不删。
 */
export function decideMigration(args: {
  legacyRaw: string | null;
  alreadyMigrated: boolean;
}): MigrationDecision {
  if (args.alreadyMigrated) return { shouldOffer: false, count: 0 };
  const count = legacyBookmarkCount(args.legacyRaw);
  return { shouldOffer: count > 0, count };
}

/** 备份文件体积/格式校验（10 MB），返回给后端导入的 payload。 */
export function parseBackupFile(raw: string): unknown {
  if (new TextEncoder().encode(raw).length > MAX_BACKUP_BYTES) {
    throw new Error("收藏备份不能超过 10 MB");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("备份文件不是有效的 JSON");
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.items)) {
    throw new Error("备份文件格式不正确：缺少 items 数组");
  }
  return parsed;
}

/** 把一批搜索结果展开成待收藏的条目（分组卡片会按来源展开）。 */
export function toAddPayload(
  results: readonly UnifiedSearchResult[],
  fetchedAt: Partial<Record<PlatformSlug, string | null>> = {},
): { result: UnifiedSearchResult; fetched_at: string | null }[] {
  const seen = new Set<string>();
  const entries: { result: UnifiedSearchResult; fetched_at: string | null }[] = [];
  for (const source of results.flatMap(resultSources)) {
    let result: UnifiedSearchResult;
    try {
      result = publicResult(source);
    } catch {
      continue;
    }
    const key = resultKey(result);
    if (seen.has(key)) continue;
    seen.add(key);
    entries.push({ result, fetched_at: fetchedAt[result.platform] ?? null });
  }
  return entries;
}

/** 勾选框三态：全部命中 = true，部分命中 = null（半选），都没命中 = false。 */
export type MembershipState = boolean | null;

export interface CollectionMembership {
  inDefault: MembershipState;
  watchLater: MembershipState;
  /** 自建收藏夹 id -> 勾选三态。 */
  custom: Record<number, MembershipState>;
}

function triState(hit: number, total: number): MembershipState {
  if (total === 0) return false;
  if (hit === total) return true;
  return hit > 0 ? null : false;
}

/**
 * 一批收藏条目的归属勾选状态。
 *
 * 聚合卡片一次会收藏多个平台版本，勾选框要按"这批里有多少条已属于该收藏夹"
 * 显示成选中 / 半选 / 未选，而不是只看第一条。
 */
export function collectionMembership(items: readonly LibraryItem[]): CollectionMembership {
  const total = items.length;
  const custom: Record<number, MembershipState> = {};
  const ids = new Set<number>();
  for (const item of items) {
    for (const tag of item.collections) ids.add(tag.id);
  }
  for (const id of ids) {
    const hit = items.filter((item) => item.collections.some((tag) => tag.id === id)).length;
    custom[id] = triState(hit, total);
  }
  return {
    inDefault: triState(items.filter((item) => item.inDefault).length, total),
    watchLater: triState(items.filter((item) => item.watchLater).length, total),
    custom,
  };
}

export function describeImport(stats: ImportStats): string {
  if (stats.added === 0 && stats.updated === 0) {
    return stats.skipped > 0 ? `未导入 ${stats.skipped} 条：内容无效或收藏数量已达上限` : "备份里没有可导入的内容";
  }
  const parts = [`新增 ${stats.added} 条`];
  if (stats.updated > 0) parts.push(`更新 ${stats.updated} 条`);
  if (stats.skipped > 0) parts.push(`跳过 ${stats.skipped} 条`);
  return `已导入：${parts.join("，")}`;
}

// ── IO：薄封装 ──────────────────────────────────────────────────────────

export async function fetchStats(): Promise<LibraryStats> {
  const { data } = await axios.get<LibraryStats>(`${LIBRARY_API_BASE}/stats`);
  return data;
}

export async function fetchItems(): Promise<LibraryItem[]> {
  const { data } = await axios.get(`${LIBRARY_API_BASE}/items`);
  return toLibraryItems(data);
}

export async function fetchCollections(): Promise<LibraryCollection[]> {
  const { data } = await axios.get(`${LIBRARY_API_BASE}/collections`);
  return toCollections(data);
}

export async function addItems(
  entries: { result: UnifiedSearchResult; fetched_at: string | null; in_default?: boolean; watch_later?: boolean }[],
  collectionIds: number[] = [],
): Promise<ImportStats> {
  if (!entries.length) return { added: 0, updated: 0, skipped: 0 };
  const { data } = await axios.post<ImportStats>(`${LIBRARY_API_BASE}/items/batch`, {
    entries,
    collection_ids: collectionIds,
  });
  return data;
}

export type SystemCollectionKey = "default" | "watch_later";

export async function addItemsToSystemCollection(
  collection: SystemCollectionKey,
  entries: { result: UnifiedSearchResult; fetched_at: string | null }[],
): Promise<ImportStats> {
  if (!entries.length) return { added: 0, updated: 0, skipped: 0 };
  const { data } = await axios.put<ImportStats>(
    `${LIBRARY_API_BASE}/system-collections/${collection}/items`, { entries },
  );
  return data;
}

export async function removeItemsFromSystemCollection(
  collection: SystemCollectionKey,
  keys: string[],
): Promise<void> {
  const parsed = keys.map(splitKey).filter((key): key is { platform: string; content_id: string } => key !== null);
  if (!parsed.length) return;
  await axios.delete(`${LIBRARY_API_BASE}/system-collections/${collection}/items`, { data: { keys: parsed } });
}

export async function removeItems(keys: string[]): Promise<void> {
  if (!keys.length) return;
  const parsed = keys.map(splitKey).filter((key): key is { platform: string; content_id: string } => key !== null);
  if (!parsed.length) return;
  await axios.delete(`${LIBRARY_API_BASE}/items`, { data: { keys: parsed } });
}

/** 取消收藏：清掉收藏夹归属并移出「全部」；稍后再看保留（后端自行清理孤儿）。 */
export async function unsaveItems(keys: string[]): Promise<void> {
  const parsed = keys.map(splitKey).filter((entry): entry is { platform: string; content_id: string } => entry !== null);
  if (!parsed.length) return;
  await axios.post(`${LIBRARY_API_BASE}/items/unsave`, { keys: parsed });
}

export async function updateNote(key: string, note: string): Promise<void> {
  const parsed = splitKey(key);
  if (!parsed) throw new Error("收藏标识无效");
  await axios.patch(`${LIBRARY_API_BASE}/items/${encodeURIComponent(parsed.platform)}/${encodeURIComponent(parsed.content_id)}`, {
    note,
  });
}

export async function createCollection(name: string): Promise<LibraryCollection> {
  const { data } = await axios.post(`${LIBRARY_API_BASE}/collections`, { name });
  return { id: Number(data.id) || 0, name: String(data.name ?? name), item_count: Number(data.item_count) || 0 };
}

export async function renameCollection(id: number, name: string): Promise<void> {
  await axios.patch(`${LIBRARY_API_BASE}/collections/${id}`, { name });
}

export async function deleteCollection(id: number): Promise<void> {
  await axios.delete(`${LIBRARY_API_BASE}/collections/${id}`);
}

export async function addItemsToCollection(keys: string[], collectionId: number): Promise<void> {
  const parsed = keys.map(splitKey).filter((key): key is { platform: string; content_id: string } => key !== null);
  if (!parsed.length) return;
  await axios.post(`${LIBRARY_API_BASE}/collections/${collectionId}/items`, { keys: parsed });
}

export async function removeItemsFromCollection(keys: string[], collectionId: number): Promise<void> {
  const parsed = keys.map(splitKey).filter((key): key is { platform: string; content_id: string } => key !== null);
  if (!parsed.length) return;
  await axios.delete(`${LIBRARY_API_BASE}/collections/${collectionId}/items`, { data: { keys: parsed } });
}

export async function importPayload(payload: unknown): Promise<ImportStats> {
  const { data } = await axios.post<ImportStats>(`${LIBRARY_API_BASE}/import`, { payload });
  return data;
}

export async function exportPayload(): Promise<unknown> {
  const { data } = await axios.get(`${LIBRARY_API_BASE}/export`);
  return data;
}

/** "platform|content_id" -> 结构化 key。content_id 里允许出现 "|"，因此只切第一个。 */
export function splitKey(key: string): { platform: string; content_id: string } | null {
  const index = key.indexOf("|");
  if (index <= 0 || index === key.length - 1) return null;
  return { platform: key.slice(0, index), content_id: key.slice(index + 1) };
}
