import type { PlatformSlug } from "@/types/search";
import { PLATFORM_SLUGS, isPlatformSlug } from "../platformMeta.js";

export const HISTORY_STORAGE_KEY = "aggregate_search_history";
export const PLATFORM_PREF_STORAGE_KEY = "aggregate_search_platform_pref";
export const MAX_HISTORY_ITEMS = 10;

// ── Types ──────────────────────────────────────────────────────────────

export interface SearchHistoryItem {
  keyword: string;
  platforms: PlatformSlug[];
  searchedAt: string;
}


/** localStorage 的窄接口 —— 测试可用内存实现，不依赖 DOM。 */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

// ── Slug validation ────────────────────────────────────────────────────
// isPlatformSlug 见 lib/platformMeta.ts（上面已 re-export）。

// ── Search history ─────────────────────────────────────────────────────

/** 规范化关键词：trim + 不区分大小写（仅用于去重比较）。 */
export function normalizeHistoryKeyword(keyword: string): string {
  return keyword.trim().toLowerCase();
}

/** 规范化平台组合：过滤非法 slug、去重、排序（仅用于去重比较）。 */
export function normalizedPlatformSet(platforms: unknown): string[] {
  if (!Array.isArray(platforms)) return [];
  return [...new Set(platforms.filter(isPlatformSlug))].sort();
}

function isSameHistoryCombination(
  a: Pick<SearchHistoryItem, "keyword" | "platforms">,
  b: Pick<SearchHistoryItem, "keyword" | "platforms">
): boolean {
  if (normalizeHistoryKeyword(a.keyword) !== normalizeHistoryKeyword(b.keyword)) return false;
  const pa = JSON.stringify(normalizedPlatformSet(a.platforms));
  const pb = JSON.stringify(normalizedPlatformSet(b.platforms));
  return pa.length > 2 && pa === pb; // 过滤后非空才可能相同
}

/** 解析一条历史记录；字段非法返回 null（searchedAt 必须是可解析的日期）。 */
function parseHistoryItem(raw: unknown): SearchHistoryItem | null {
  if (typeof raw !== "object" || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  if (typeof obj.keyword !== "string" || obj.keyword.trim() === "") return null;
  const platforms = obj.platforms;
  if (!Array.isArray(platforms)) return null;
  const valid = platforms.filter(isPlatformSlug);
  if (valid.length === 0) return null;
  if (typeof obj.searchedAt !== "string") return null;
  if (Number.isNaN(Date.parse(obj.searchedAt))) return null; // 非法日期丢弃
  return {
    keyword: obj.keyword.trim(),
    platforms: valid as PlatformSlug[],
    searchedAt: obj.searchedAt,
  };
}

/** 解析任意来源的历史数据；非法输入安全回退为空列表。 */
export function parseHistory(raw: unknown): SearchHistoryItem[] {
  if (!Array.isArray(raw)) return [];
  const items: SearchHistoryItem[] = [];
  for (const entry of raw) {
    const item = parseHistoryItem(entry);
    if (item) items.push(item);
    if (items.length >= MAX_HISTORY_ITEMS) break;
  }
  return items;
}

/** 新增/更新一条历史：去重（规范化关键词+平台组合相同 → 更新 searchedAt 并移到最前），最多 10 条。 */
export function addHistoryItem(
  history: SearchHistoryItem[],
  keyword: string,
  platforms: PlatformSlug[],
  nowIso: string
): SearchHistoryItem[] {
  const trimmed = keyword.trim();
  if (!trimmed) return history;
  const item: SearchHistoryItem = { keyword: trimmed, platforms, searchedAt: nowIso };
  const rest = history.filter((h) => !isSameHistoryCombination(h, item));
  return [item, ...rest].slice(0, MAX_HISTORY_ITEMS);
}

export function removeHistoryItem(history: SearchHistoryItem[], index: number): SearchHistoryItem[] {
  if (index < 0 || index >= history.length) return history;
  return history.filter((_, i) => i !== index);
}

export function readHistory(storage: StorageLike): SearchHistoryItem[] {
  try {
    const raw = storage.getItem(HISTORY_STORAGE_KEY);
    if (raw === null) return [];
    return parseHistory(JSON.parse(raw));
  } catch {
    return []; // 损坏/拒绝 → 安全回退
  }
}

export function writeHistory(storage: StorageLike, history: SearchHistoryItem[]): boolean {
  try {
    storage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(history.slice(0, MAX_HISTORY_ITEMS)));
    return true;
  } catch {
    return false; // 浏览器拒绝存储 → 静默回退
  }
}

// ── Platform preference ────────────────────────────────────────────────

/** 解析平台偏好：只接受合法 slug；损坏值（非数组 / 全是非法值）→ 全选兜底。 */
export function parsePlatformPref(raw: unknown, all: PlatformSlug[] = PLATFORM_SLUGS): PlatformSlug[] {
  if (!Array.isArray(raw)) return [...all];
  // 显式空数组 = "用户当前一个都没勾"：保持零勾选，交给用户亲手勾。
  // 没有这一条时，首次进入写回的 [] 会被当成"解析不出平台"而回退成全选，
  // 于是页面往返一次就变成四个平台全勾（教程文案承诺的是默认不预选）。
  if (raw.length === 0) return [];
  const valid = [...new Set(raw.filter(isPlatformSlug))];
  return valid.length > 0 ? valid : [...all];
}

// 首次进入（没有任何存储值）返回空集：允许"零勾选"，由用户亲手选择平台；
// 首屏把空集写回存储后的"[]"同样按空集读，否则页面往返一次就变成全选。
// 只有"存储坏了"才回退全选（下同，见下方 catch）—— 那与"用户没勾"是两回事。
export function readPlatformPref(storage: StorageLike, all: PlatformSlug[] = PLATFORM_SLUGS): PlatformSlug[] {
  try {
    const raw = storage.getItem(PLATFORM_PREF_STORAGE_KEY);
    if (raw === null) return [];
    return parsePlatformPref(JSON.parse(raw), all);
  } catch {
    return [...all];
  }
}

export function writePlatformPref(storage: StorageLike, platforms: PlatformSlug[]): boolean {
  try {
    storage.setItem(PLATFORM_PREF_STORAGE_KEY, JSON.stringify(platforms));
    return true;
  } catch {
    return false;
  }
}
