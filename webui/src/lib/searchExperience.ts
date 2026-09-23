/**
 * 搜索体验纯逻辑模块（Round 12，无 React 依赖）。
 *
 * 职责：
 * - 最近搜索历史（localStorage，最多 10 条，去重移前）
 * - 平台选择偏好（localStorage，至少一个平台）
 * - 前端排序（综合 / 最新 / 互动最多，稳定）
 * - 跨平台轮询交错与单平台重试合并（与后端 interleave_results 算法一致）
 * - 安全错误摘要（不含响应体/Cookie/header/token）
 *
 * 所有 localStorage 读写均为安全回退：任何损坏/非法/拒绝都不影响搜索。
 */

import type {
  GroupedSource,
  PlatformSlug,
  SearchJobResponse,
  UnifiedSearchResult,
} from "@/types/search";
import { PLATFORM_SLUGS, isPlatformSlug } from "./platformMeta.js";

// 平台列表与 slug 守卫的唯一来源是 platformMeta（历史上这里各有一份拷贝）。
// 继续从这里导出，是为了不改动既有调用点与测试。
export { PLATFORM_SLUGS, isPlatformSlug };

export const HISTORY_STORAGE_KEY = "aggregate_search_history";
export const PLATFORM_PREF_STORAGE_KEY = "aggregate_search_platform_pref";
export const MAX_HISTORY_ITEMS = 10;

// ── Types ──────────────────────────────────────────────────────────────

export interface SearchHistoryItem {
  keyword: string;
  platforms: PlatformSlug[];
  searchedAt: string;
}

export type SearchSortMode = "default" | "latest" | "engagement";

/** localStorage 的窄接口 —— 测试可用内存实现，不依赖 DOM。 */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface BusyFlags {
  isCreating: boolean;
  isRunning: boolean;
  isCancelling: boolean;
  retryingPlatform: string | null;
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

/** 解析平台偏好：只接受合法 slug，至少保留一个平台；损坏 → 全选。 */
export function parsePlatformPref(raw: unknown, all: PlatformSlug[] = PLATFORM_SLUGS): PlatformSlug[] {
  if (!Array.isArray(raw)) return [...all];
  // 显式空数组 = "还没落地过偏好"：保持零勾选，交给用户亲手勾。
  // 没有这一条时，首次进入写回的 [] 会被当成"解析不出平台"而回退成全选，
  // 于是页面往返一次就变成四个平台全勾（教程文案承诺的是默认不预选）。
  if (raw.length === 0) return [];
  const valid = [...new Set(raw.filter(isPlatformSlug))];
  return valid.length > 0 ? valid : [...all];
}

// 首次进入（没有任何存储值）返回空集：允许"零勾选"，由用户亲手选择平台；
// 首屏把空集写回存储后的"[]"同样按空集读，否则页面往返一次就变成全选。
// 损坏 / 不可读的存储仍回退全选，避免把用户卡住（见下方 catch）。
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

// ── Sorting ────────────────────────────────────────────────────────────

/** 解析 published_at：可解析返回毫秒时间戳，否则 null（不抛异常）。 */
export function parsePublishedTime(value: string | null | undefined): number | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  const t = Date.parse(value);
  return Number.isNaN(t) ? null : t;
}

/** 非数字（缺失/NaN/Infinity/其他类型）→ 0；合法有限数字原样返回。 */
export function toFiniteNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** 综合排序使用的互动分：只用于平台内相对排名。 */
export function engagementScore(metrics: Record<string, number>): number {
  const m = metrics || {};
  return toFiniteNumber(m.like_count)
    + toFiniteNumber(m.collect_count) * 2
    + toFiniteNumber(m.comment_count) * 2
    + toFiniteNumber(m.share_count) * 3
    + toFiniteNumber(m.coin_count) * 2
    + toFiniteNumber(m.danmaku_count)
    + toFiniteNumber(m.view_count) * 0.01;
}

// ── Cross-platform aggregate ranking V1 ───────────────────────────────

export interface AggregateSortOptions {
  /** 必须注入当前时间，保证核心排序函数可复现。 */
  nowMs: number;
  /** 仅用于最终同分时的确定性排序。 */
  platformOrder?: readonly PlatformSlug[];
}

const AGGREGATE_WEIGHTS = {
  relevance: 0.4,
  rank: 0.25,
  freshness: 0.2,
  engagement: 0.15,
} as const;
const FRESHNESS_HALF_LIFE_DAYS = 30;
const DAY_MS = 24 * 60 * 60 * 1000;
const DIVERSITY_CONSECUTIVE_PENALTY = 0.35;
const DIVERSITY_OVERREPRESENTATION_PENALTY = 0.1;

function normalizeSearchText(value: string): string {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "")
    .trim();
}

function keywordTokens(keyword: string): string[] {
  const runs = keyword
    .normalize("NFKC")
    .toLocaleLowerCase()
    .match(/[\u4e00-\u9fff]+|[a-z0-9]+/g) || [];
  const tokens: string[] = [];
  runs.forEach((run) => {
    if (/^[\u4e00-\u9fff]+$/.test(run)) {
      // 不按单字累计；用相邻双字片段提供保守、确定性的中文 token 匹配。
      for (let i = 0; i < run.length - 1; i += 1) {
        tokens.push(run.slice(i, i + 2));
      }
    } else {
      tokens.push(run);
    }
  });
  return [...new Set(tokens)];
}

function fieldRelevanceScore(
  value: string | null | undefined,
  normalizedKeyword: string,
  tokens: readonly string[]
): number {
  const normalizedValue = normalizeSearchText(value || "");
  if (!normalizedValue) return 0;
  if (normalizedValue.includes(normalizedKeyword)) return 1;
  if (tokens.length === 0) return 0;
  const matched = tokens.filter((token) => normalizedValue.includes(token)).length;
  return matched === 0 ? 0 : 0.7 * (matched / tokens.length);
}

/** 关键词相关性：标题 > snippet > 作者，完整短语优先于保守 token 命中，范围 0–1。 */
export function keywordRelevanceScore(
  result: UnifiedSearchResult,
  keyword: string
): number {
  const normalizedKeyword = normalizeSearchText(keyword);
  if (!normalizedKeyword) return 0;
  const tokens = keywordTokens(keyword);
  const title = fieldRelevanceScore(result.title, normalizedKeyword, tokens);
  const snippet = fieldRelevanceScore(result.snippet, normalizedKeyword, tokens);
  const author = fieldRelevanceScore(result.author, normalizedKeyword, tokens);
  return Math.min(1, title * 0.6 + snippet * 0.3 + author * 0.1);
}

function relativeRankScore(
  value: number,
  values: readonly number[],
  descending: boolean
): number {
  const unique = [...new Set(values)].sort((a, b) => descending ? b - a : a - b);
  if (unique.length <= 1) return value === 0 ? 0 : 1;
  const index = unique.indexOf(value);
  return 1 - (index / (unique.length - 1));
}

/** 互动分只在同一平台内部做相对排名，供综合和“互动最多”共同使用。 */
export function platformRelativeEngagementScores(
  results: readonly UnifiedSearchResult[]
): number[] {
  const grouped = new Map<PlatformSlug, number[]>();
  results.forEach((result, index) => {
    const indexes = grouped.get(result.platform) || [];
    indexes.push(index);
    grouped.set(result.platform, indexes);
  });

  const scores = new Array<number>(results.length).fill(0);
  grouped.forEach((indexes) => {
    const values = indexes.map((index) => engagementScore(results[index].metrics));
    indexes.forEach((index) => {
      scores[index] = relativeRankScore(
        engagementScore(results[index].metrics),
        values,
        true
      );
    });
  });
  return scores;
}

const INTERACTION_STRENGTH_WEIGHTS = {
  comment_count: 5,
  collect_count: 3.5,
  coin_count: 3,
  share_count: 3,
  like_count: 2,
  danmaku_count: 1,
  view_count: 0.15,
} as const;

/**
 * “互动最多”的跨平台实际强度：各指标先 log1p 压缩，再按深度互动优先加权。
 * 这与综合排序的相对分是不同语义，但仍复用同一平台内相对分函数。
 */
export function interactionStrengthScore(metrics: Record<string, number>): number {
  return Object.entries(INTERACTION_STRENGTH_WEIGHTS).reduce(
    (score, [field, weight]) => score + weight * Math.log1p(
      Math.max(0, toFiniteNumber(metrics?.[field]))
    ),
    0
  );
}

export function interactionSortScores(
  results: readonly UnifiedSearchResult[]
): number[] {
  const relativeScores = platformRelativeEngagementScores(results);
  const strengths = results.map((result) => interactionStrengthScore(result.metrics));
  const maxStrength = Math.max(0, ...strengths);
  return strengths.map((strength, index) => {
    const actualStrength = maxStrength > 0 ? strength / maxStrength : 0;
    // 相对表现保持跨平台公平，实际强度用于打破各平台第一名并列。
    return relativeScores[index] * 0.6 + actualStrength * 0.4;
  });
}

function freshnessScore(
  publishedAt: string | null,
  nowMs: number
): number {
  const publishedMs = parsePublishedTime(publishedAt);
  if (publishedMs === null) return 0;
  const ageDays = Math.max(0, (nowMs - publishedMs) / DAY_MS);
  return 1 / (1 + ageDays / FRESHNESS_HALF_LIFE_DAYS);
}

interface AggregateScoredResult {
  result: UnifiedSearchResult;
  index: number;
  baseScore: number;
  relevance: number;
  rank: number;
  freshness: number;
  engagement: number;
}

/**
 * V1 综合排序：先在各平台内部计算 rank/互动相对分，再做加权评分，
 * 最后用轻量的连续平台惩罚进行贪心选取。所有计算只依赖输入和 nowMs，
 * 没有 LLM、embedding 或外部服务。
 */
export function aggregateSortResults(
  results: UnifiedSearchResult[],
  keyword: string,
  options: AggregateSortOptions
): UnifiedSearchResult[] {
  if (results.length <= 1) return [...results];

  const nowMs = options.nowMs;
  const platformOrder = options.platformOrder || PLATFORM_SLUGS;
  const platformPriority = new Map(platformOrder.map((platform, index) => [platform, index]));
  const grouped = new Map<PlatformSlug, number[]>();
  results.forEach((result, index) => {
    const indexes = grouped.get(result.platform) || [];
    indexes.push(index);
    grouped.set(result.platform, indexes);
  });
  const engagementScores = platformRelativeEngagementScores(results);

  const scored: AggregateScoredResult[] = results.map((result, index) => {
    const indexes = grouped.get(result.platform) || [index];
    const ranks = indexes.map((i) => Math.max(0, toFiniteNumber(results[i].rank)));
    const rankValue = Math.max(0, toFiniteNumber(result.rank));
    const relevance = keywordRelevanceScore(result, keyword);
    const rank = relativeRankScore(rankValue, ranks, false);
    const engagement = engagementScores[index];
    const freshness = freshnessScore(result.published_at, nowMs);
    const baseScore = AGGREGATE_WEIGHTS.relevance * relevance
      + AGGREGATE_WEIGHTS.rank * rank
      + AGGREGATE_WEIGHTS.freshness * freshness
      + AGGREGATE_WEIGHTS.engagement * engagement;
    return { result, index, baseScore, relevance, rank, freshness, engagement };
  });

  const remaining = [...scored];
  const selected: AggregateScoredResult[] = [];
  const selectedCounts = new Map<PlatformSlug, number>();
  let lastPlatform: PlatformSlug | null = null;
  let consecutiveCount = 0;

  while (remaining.length > 0) {
    const availablePlatforms = new Set(remaining.map((item) => item.result.platform));
    const minSelectedCount = Math.min(
      ...[...availablePlatforms].map((platform) => selectedCounts.get(platform) || 0)
    );
    const candidates = remaining.map((item) => {
      const platform = item.result.platform;
      const count = selectedCounts.get(platform) || 0;
      const consecutivePenalty = platform === lastPlatform
        ? DIVERSITY_CONSECUTIVE_PENALTY * consecutiveCount
        : 0;
      const overrepresentationPenalty = Math.max(0, count - minSelectedCount)
        * DIVERSITY_OVERREPRESENTATION_PENALTY;
      return {
        item,
        adjustedScore: item.baseScore - consecutivePenalty - overrepresentationPenalty,
      };
    });

    candidates.sort((a, b) => {
      if (b.adjustedScore !== a.adjustedScore) return b.adjustedScore - a.adjustedScore;
      if (b.item.baseScore !== a.item.baseScore) return b.item.baseScore - a.item.baseScore;
      const pa = platformPriority.get(a.item.result.platform) ?? Number.MAX_SAFE_INTEGER;
      const pb = platformPriority.get(b.item.result.platform) ?? Number.MAX_SAFE_INTEGER;
      return pa - pb || a.item.index - b.item.index;
    });

    const next = candidates[0].item;
    remaining.splice(remaining.indexOf(next), 1);
    selected.push(next);
    const platform = next.result.platform;
    selectedCounts.set(platform, (selectedCounts.get(platform) || 0) + 1);
    consecutiveCount = platform === lastPlatform ? consecutiveCount + 1 : 1;
    lastPlatform = platform;
  }

  return selected.map((item) => item.result);
}

/**
 * 按模式排序（稳定：同分/同时保持原始相对顺序，显式 index 比较，不依赖引擎）。
 * - default：综合 → 关键词、平台内原始 rank、时间、平台内互动相对分，
 *   再做轻量平台多样性调整。
 * - latest：published_at 可解析从新到旧；无/非法时间放最后，保持原相对顺序。
 * - engagement：互动分数从高到低；缺失数据按 0；同分保持原顺序。
 */
export function sortResults(
  results: UnifiedSearchResult[],
  mode: SearchSortMode,
  keyword = "",
  options: Partial<AggregateSortOptions> = {}
): UnifiedSearchResult[] {
  if (mode === "default") {
    return aggregateSortResults(results, keyword, {
      nowMs: options.nowMs ?? Date.now(),
      platformOrder: options.platformOrder,
    });
  }
  if (mode === "latest") {
    const withTime = results.map((r, i) => ({ r, i, t: parsePublishedTime(r.published_at) }));
    const dated = withTime
      .filter((e) => e.t !== null)
      .sort((a, b) => (b.t as number) - (a.t as number) || a.i - b.i);
    const undated = withTime.filter((e) => e.t === null);
    return [...dated, ...undated].map((e) => e.r);
  }
  // engagement
  const engagementScores = interactionSortScores(results);
  return results
    .map((r, i) => ({ r, i, s: engagementScores[i] }))
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map((e) => e.r);
}

// ── Interleave & merge ─────────────────────────────────────────────────

export function makeDedupKey(platform: PlatformSlug, contentId: string): string {
  return `${platform}|${contentId}`;
}

function groupedSourceToResult(source: GroupedSource): UnifiedSearchResult {
  return {
    platform: source.platform,
    content_id: source.content_id,
    content_type: source.content_type,
    title: source.title,
    snippet: source.snippet ?? null,
    author: source.author,
    url: source.url,
    published_at: source.published_at,
    cover_url: source.cover_url,
    metrics: { ...source.metrics },
    rank: source.rank,
    grouped_sources: null,
  };
}

/** 展开后只用于单平台视图或重试合并，不改变综合结果中的组。 */
export function expandGroupedResults(
  results: readonly UnifiedSearchResult[]
): UnifiedSearchResult[] {
  return results.flatMap((result) => {
    const sources = result.grouped_sources;
    return sources && sources.length >= 2
      ? sources.map(groupedSourceToResult)
      : [result];
  });
}

export function expandGroupedResultsForPlatform(
  results: readonly UnifiedSearchResult[],
  platform: PlatformSlug
): UnifiedSearchResult[] {
  return expandGroupedResults(results).filter((result) => result.platform === platform);
}

// ── Cross-platform de-duplication V1 ──────────────────────────────────

const CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH = 6;
const CROSS_PLATFORM_DEDUP_MIN_EXACT_TITLE_LENGTH = 4;
const CROSS_PLATFORM_DEDUP_FUZZY_THRESHOLD = 0.9;
const CROSS_PLATFORM_DEDUP_MIN_SNIPPET_LENGTH = 20;
const DEDUP_TITLE_SUFFIX_RE = /(?:附(?:完整)?(?:文档|资料|教程)|完整(?:版|文档)|完整版)$/u;

export function normalizeDedupText(value: string | null | undefined): string {
  if (!value) return "";
  return value
    .normalize("NFKC")
    .toLowerCase()
    .replace(/<[^>]*>/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, "");
}

// Python counts Unicode code points, not JavaScript UTF-16 code units.
function textLength(value: string): number {
  return Array.from(value).length;
}

function normalizeDedupTitle(value: string | null | undefined): string {
  return normalizeDedupText(value).replace(DEDUP_TITLE_SUFFIX_RE, "");
}

function sameDedupAuthor(left: string | null | undefined, right: string | null | undefined): boolean {
  const leftAuthor = normalizeDedupText(left);
  const rightAuthor = normalizeDedupText(right);
  if (!leftAuthor || !rightAuthor) return false;
  if (leftAuthor === rightAuthor) return true;
  const [shorter, longer] = [leftAuthor, rightAuthor].sort((a, b) => textLength(a) - textLength(b));
  // 例如“秋芝”和“秋芝2046”；只接受明确的四位数字后缀，避免泛化成作者别名库。
  return textLength(shorter) >= 2
    && longer.startsWith(shorter)
    && /^\d{4}$/u.test(longer.slice(shorter.length));
}

function textSimilarity(left: string, right: string): number {
  if (!left || !right) return 0;
  const leftChars = Array.from(left);
  const rightChars = Array.from(right);
  const previous = Array.from({ length: rightChars.length + 1 }, (_, i) => i);
  for (let i = 1; i <= leftChars.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= rightChars.length; j += 1) {
      current[j] = leftChars[i - 1] === rightChars[j - 1]
        ? previous[j - 1]
        : 1 + Math.min(previous[j - 1], previous[j], current[j - 1]);
    }
    for (let j = 0; j <= rightChars.length; j += 1) previous[j] = current[j];
  }
  return 1 - previous[rightChars.length] / Math.max(leftChars.length, rightChars.length);
}

function similarDedupSnippets(
  left: UnifiedSearchResult, right: UnifiedSearchResult,
  leftTitle: string, rightTitle: string
): boolean {
  const leftSnippet = normalizeDedupText(left.snippet);
  const rightSnippet = normalizeDedupText(right.snippet);
  return Math.min(textLength(leftSnippet), textLength(rightSnippet)) >= CROSS_PLATFORM_DEDUP_MIN_SNIPPET_LENGTH
    && leftSnippet !== leftTitle && rightSnippet !== rightTitle
    && textSimilarity(leftSnippet, rightSnippet) >= 0.88;
}

function isCrossPlatformDuplicate(
  left: UnifiedSearchResult,
  right: UnifiedSearchResult
): boolean {
  if (left.platform === right.platform) return false;
  const leftTitle = normalizeDedupTitle(left.title);
  const rightTitle = normalizeDedupTitle(right.title);
  if (Math.min(textLength(leftTitle), textLength(rightTitle)) < CROSS_PLATFORM_DEDUP_MIN_EXACT_TITLE_LENGTH) return false;
  const sameAuthor = sameDedupAuthor(left.author, right.author);
  if (leftTitle === rightTitle) return sameAuthor || similarDedupSnippets(left, right, leftTitle, rightTitle);
  if (Math.min(textLength(leftTitle), textLength(rightTitle)) < CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH) return false;

  const [shorterTitle, longerTitle] = [leftTitle, rightTitle].sort((a, b) => textLength(a) - textLength(b));
  const coreTitleContained = textLength(shorterTitle) >= CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH
    && textLength(shorterTitle) / textLength(longerTitle) >= 0.65
    && longerTitle.includes(shorterTitle);
  const titleSimilarity = textSimilarity(leftTitle, rightTitle);
  if (sameAuthor && (coreTitleContained || titleSimilarity >= 0.86)) return true;
  if (titleSimilarity < CROSS_PLATFORM_DEDUP_FUZZY_THRESHOLD) return false;

  return sameAuthor || similarDedupSnippets(left, right, leftTitle, rightTitle);
}

function resultCompletenessScore(result: UnifiedSearchResult): number {
  return Math.min(textLength(normalizeDedupText(result.title)), 40) / 40
    + (normalizeDedupText(result.snippet) ? 3 : 0)
    + (normalizeDedupText(result.author) ? 2 : 0)
    + (result.published_at ? 1 : 0)
    + (result.cover_url ? 1 : 0)
    + Math.min(Object.values(result.metrics || {}).filter((value) => value > 0).length, 4) * 0.25;
}

function hasCommonTitleAnchor(indexes: readonly number[], results: readonly UnifiedSearchResult[]): boolean {
  if (indexes.length <= 2) return true;
  const titles = indexes.map((index) => normalizeDedupTitle(results[index].title));
  if (new Set(titles).size === 1) return true;
  return titles.some((anchor) => textLength(anchor) >= CROSS_PLATFORM_DEDUP_MIN_TITLE_LENGTH
    && titles.every((candidate) => {
      if (candidate === anchor) return true;
      if (textLength(anchor) / textLength(candidate) < 0.65) return false;
      return candidate.includes(anchor);
    }));
}

/** Cross-platform grouping requires title similarity and author/snippet evidence. */
export function deduplicateCrossPlatformResults(
  results: UnifiedSearchResult[],
  platformOrder: readonly PlatformSlug[] = PLATFORM_SLUGS
): UnifiedSearchResult[] {
  if (results.length <= 1) return [...results];
  const parent = results.map((_, index) => index);
  const find = (index: number): number => {
    while (parent[index] !== index) {
      parent[index] = parent[parent[index]];
      index = parent[index];
    }
    return index;
  };
  const union = (left: number, right: number): void => {
    const leftRoot = find(left);
    const rightRoot = find(right);
    if (leftRoot !== rightRoot) parent[rightRoot] = leftRoot;
  };

  const componentMembers = (root: number): number[] => results
    .map((_, index) => index)
    .filter((index) => find(index) === root);

  for (let i = 0; i < results.length; i += 1) {
    for (let j = i + 1; j < results.length; j += 1) {
      if (!isCrossPlatformDuplicate(results[i], results[j])) continue;
      const leftRoot = find(i);
      const rightRoot = find(j);
      if (leftRoot === rightRoot) continue;
      const merged = [...componentMembers(leftRoot), ...componentMembers(rightRoot)];
      if (new Set(merged.map((index) => results[index].platform)).size !== merged.length) continue;
      // 两条结果保持原有 predicate 语义；三条以上还要共享一个标题核心，
      // 防止 A~B、B~C 的弱链路把明显不同的 C 传递合并进来。
      if (hasCommonTitleAnchor(merged, results)) union(i, j);
    }
  }

  const groups = new Map<number, number[]>();
  results.forEach((_, index) => {
    const root = find(index);
    groups.set(root, [...(groups.get(root) || []), index]);
  });
  const platformPriority = new Map(platformOrder.map((platform, index) => [platform, index]));
  const representatives = [...groups.values()].map((indexes) => {
    const winner = [...indexes].sort((left, right) => {
      const scoreDiff = resultCompletenessScore(results[right]) - resultCompletenessScore(results[left]);
      if (scoreDiff !== 0) return scoreDiff;
      return results[left].rank - results[right].rank
        || (platformPriority.get(results[left].platform) ?? Number.MAX_SAFE_INTEGER)
          - (platformPriority.get(results[right].platform) ?? Number.MAX_SAFE_INTEGER)
        || left - right;
    })[0];
    return { firstIndex: Math.min(...indexes), winner };
  });
  representatives.sort((left, right) => left.firstIndex - right.firstIndex);
  return representatives.map(({ winner, firstIndex }) => {
    const indexes = groups.get(find(firstIndex)) || [winner];
    const representative = results[winner];
    if (indexes.length <= 1) return representative;
    const rest = indexes
      .filter((index) => index !== winner)
      .sort((left, right) => results[left].rank - results[right].rank
        || (platformPriority.get(results[left].platform) ?? Number.MAX_SAFE_INTEGER)
          - (platformPriority.get(results[right].platform) ?? Number.MAX_SAFE_INTEGER)
        || left - right);
    const sourceIndexes = [winner, ...rest];
    return {
      ...representative,
      grouped_sources: sourceIndexes.map((index): GroupedSource => ({
        platform: results[index].platform,
        content_id: results[index].content_id,
        content_type: results[index].content_type,
        title: results[index].title,
        snippet: results[index].snippet ?? null,
        author: results[index].author,
        url: results[index].url,
        published_at: results[index].published_at,
        cover_url: results[index].cover_url,
        metrics: { ...results[index].metrics },
        rank: results[index].rank,
      })),
    };
  });
}

/**
 * 跨平台轮询交错（与后端 aggregate_search.models.interleave_results 一致）：
 * 按 platformOrder 轮流各取一条，跳过重复（platform+content_id），直到耗尽。
 * 保持各平台内部顺序。
 */
export function interleaveByPlatform(
  grouped: Map<PlatformSlug, UnifiedSearchResult[]>,
  platformOrder: PlatformSlug[]
): UnifiedSearchResult[] {
  const queues = new Map<PlatformSlug, UnifiedSearchResult[]>(
    platformOrder.map((p) => [p, [...(grouped.get(p) || [])]])
  );
  const merged: UnifiedSearchResult[] = [];
  const seen = new Set<string>();
  let changed = true;
  while (changed) {
    changed = false;
    for (const p of platformOrder) {
      const q = queues.get(p)!;
      while (q.length > 0) {
        const item = q.shift()!;
        const key = makeDedupKey(item.platform, item.content_id);
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(item);
          changed = true;
          break;
        }
      }
    }
  }
  return deduplicateCrossPlatformResults(merged, platformOrder);
}

/** 按平台重新分组（保持交错前各平台内部顺序）。 */
export function groupByPlatform(
  results: UnifiedSearchResult[]
): Map<PlatformSlug, UnifiedSearchResult[]> {
  const grouped = new Map<PlatformSlug, UnifiedSearchResult[]>();
  for (const r of results) {
    const list = grouped.get(r.platform);
    if (list) list.push(r);
    else grouped.set(r.platform, [r]);
  }
  return grouped;
}

/**
 * 单平台重搜合并：目标平台结果整体替换，其他平台保持；
 * 重新按 platformOrder 轮询交错生成综合顺序。
 *
 * "整体替换"对两种来源都成立：
 * - 重试**失败**平台（它本来没结果，替换 == 补齐）；
 * - 用户点 ⟳ 单平台重搜（后端已重置该平台的分页并重搜，这一批就是它的新内容）。
 */
export function mergeSinglePlatformRetry(
  prevResults: UnifiedSearchResult[],
  retryPlatform: PlatformSlug,
  newPlatformResults: UnifiedSearchResult[],
  platformOrder: PlatformSlug[]
): UnifiedSearchResult[] {
  const grouped = groupByPlatform(expandGroupedResults(prevResults));
  grouped.set(retryPlatform, newPlatformResults);
  return interleaveByPlatform(grouped, platformOrder);
}

// ── Overall recompute（重试后按各平台状态重算整体状态） ───────────────

const SUCCESS_LIKE = new Set(["succeeded", "empty", "cancelled"]);

export function recomputeOverall(
  statuses: readonly string[]
): "completed" | "partial" | "failed" {
  if (statuses.length === 0) return "failed";
  if (statuses.every((s) => SUCCESS_LIKE.has(s))) return "completed";
  if (statuses.some((s) => SUCCESS_LIKE.has(s))) return "partial";
  return "failed";
}

// ── Busy guard ─────────────────────────────────────────────────────────

/** 搜索/取消/单平台重试进行中 → 不允许发起新任务（否则后端 409）。 */
export function isSearchBlocked(flags: BusyFlags): boolean {
  return flags.isCreating || flags.isRunning || flags.isCancelling || flags.retryingPlatform !== null;
}

// ── Safe error summary ─────────────────────────────────────────────────

/**
 * 从 axios 错误提取用户可见的安全摘要：
 * - 409/404 固定文案（不显示 detail 中的内部信息）
 * - 422 detail 数组取 msg 拼接
 * - 否则取 string detail 或 message
 * 绝不包含响应体、Cookie、header、token 或 traceback。
 */
export function safeErrorSummary(err: unknown): string {
  const e = err as {
    response?: { status?: number; data?: { detail?: unknown } };
    message?: string;
  };
  if (e?.response?.status === 409) return "已有任务正在运行，请等待完成后再试。";
  if (e?.response?.status === 404) return "任务已失效，请重新搜索。";
  const detail = e?.response?.data?.detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d: { msg?: unknown }) => (typeof d === "object" && d !== null && typeof (d as { msg?: unknown }).msg === "string" ? (d as { msg: string }).msg : ""))
      .filter((s: string) => s.length > 0);
    if (msgs.length > 0) return msgs.join("; ");
  }
  if (typeof detail === "string" && detail.length > 0) return detail;
  return e?.message || "请求失败，请重试。";
}

// ── 搜索体验状态机（Round 12.1 生产 reducer，无 React 依赖） ────────────

/** 展示层状态：快照 / 刷新标记 / 重试标记 / 错误 / 取消提示 / 提交标记。 */
export interface SearchDisplayState {
  /** 当前展示给用户的快照（全量任务终态或重试合并产物）。 */
  jobResponse: SearchJobResponse | null;
  /** 当前 active job 的最新实时（非终态）响应；身份校验后写入。 */
  liveResponse: SearchJobResponse | null;
  /** 新全量任务运行中、快照为旧结果时为 true（UI 显示"正在更新"）。 */
  refreshing: boolean;
  /** 正在单平台重搜的平台；null 表示无重搜进行中。 */
  retryingPlatform: PlatformSlug | null;
  /** 单平台重试失败的安全摘要，key 为平台 slug。 */
  retryErrors: Partial<Record<PlatformSlug, string>>;
  /** 仅当观察到真实 job 终态 overall === "cancelled" 时置 true。 */
  cancelledNotice: boolean;
  /** 取消请求已发出（POST 挂起或后端清理中）；不清快照、不提前亮提示。 */
  cancelRequested: boolean;
  /** 取消请求失败的安全提示（Round 13：固定文案，不显示 axios 500 原文）。 */
  cancelError: string | null;
  /** 当前任务的 job_id：POST 被接受后写入；终态必须与其匹配才提交。 */
  activeJobId: string | null;
  /** POST 已发出但尚未被接受（无 job_id）：此时任何终态都拒绝。 */
  awaitingJobAcceptance: boolean;
  /** 本会话已应用终态的 job_id 集合 —— 任何重复/迟到终态都不重复提交。 */
  appliedJobIds: Set<string>;
}

export interface ExperienceState {
  display: SearchDisplayState;
  /** 最近搜索历史（10 条上限，去重移前）。 */
  history: SearchHistoryItem[];
  /** 平台选择偏好。首次进入（无存储值）为空集；一旦用户勾选过任一平台，本偏好落地后始终保持至少一个平台。 */
  platformPref: PlatformSlug[];
}

export type ExperienceEvent =
  /** 新全量任务开始（POST 尚未被接受）；快照保留。 */
  | { type: "search_start"; keyword: string; platforms: PlatformSlug[] }
  /** POST 被后端接受（返回了 job）；此刻写入身份与历史。 */
  | {
      type: "search_accepted";
      jobId: string;
      keyword: string;
      platforms: PlatformSlug[];
      nowIso: string;
    }
  /** 单平台重试被接受（返回了 job）；写入身份但不写历史。 */
  | { type: "retry_accepted"; jobId: string }
  /** POST 失败（被拒绝）：不写历史、快照保留；若正处于重试则记录失败摘要。 */
  | { type: "search_rejected"; errorSummary?: string }
  /** 单平台重搜开始（POST 尚未被接受）。 */
  | { type: "retry_start"; platform: PlatformSlug }
  /** 页面加载/刷新时恢复的后端现有任务：显式登记其身份。 */
  | { type: "job_recovered"; jobId: string }
  /** 任务终态到达（completed/partial/failed/cancelled）；需与 activeJobId 匹配。 */
  | { type: "job_terminal"; job: SearchJobResponse }
  /** 任务实时进度（非终态轮询响应）；需与 activeJobId 匹配；仅全量任务使用。 */
  | { type: "job_progress"; job: SearchJobResponse }
  /** 取消请求已发出（Round 13）：不清快照、不提前显示已取消提示。 */
  | { type: "cancel_requested" }
  /** 取消请求失败（Round 13）：记录安全提示，不改变任务身份、不伪造终态。 */
  | { type: "cancel_rejected"; safeMessage: string }
  /** 清空当前任务与展示结果；历史与平台偏好不变。 */
  | { type: "reset" }
  /** 删除单条历史（索引）；busy 时的拒绝在调用方 guard。 */
  | { type: "history_remove"; index: number }
  /** 清空全部历史。 */
  | { type: "history_clear" }
  /** 平台选择变化（立即持久化到偏好）。 */
  | { type: "platform_pref_set"; platforms: PlatformSlug[] };

export function createInitialExperienceState(
  history: SearchHistoryItem[] = [],
  platformPref: PlatformSlug[] = []
): ExperienceState {
  return {
    display: {
      jobResponse: null,
      liveResponse: null,
      refreshing: false,
      retryingPlatform: null,
      retryErrors: {},
      cancelledNotice: false,
      cancelRequested: false,
      cancelError: null,
      activeJobId: null,
      awaitingJobAcceptance: false,
      appliedJobIds: new Set(),
    },
    history,
    platformPref,
  };
}

const TERMINAL_OVERALLS = new Set(["completed", "partial", "failed", "cancelled"]);
const FAILURE_STATUSES = new Set(["failed", "timed_out", "rate_limited", "login_required"]);

function infoStatus(info: { status: string } | undefined): string {
  return info?.status ?? "";
}

/**
 * 搜索体验状态机 reducer（Round 12.2）。
 *
 * 任务身份保护（Round 12.2）：
 * - POST 发出后 awaitingJobAcceptance=true，此时任何终态都拒绝（无身份终态）。
 * - POST 被接受后 activeJobId=job_id；终态必须与 activeJobId 匹配才提交。
 * - 恢复任务（页面加载 /jobs/current）必须先经 job_recovered 登记身份。
 * - 提交后保留 activeJobId，旧任务迟到终态无法覆盖当前任务；
 *   appliedJobIds 集合拦截同一终态的重复投递。
 *
 * 其他规则：
 * - cancelled 终态：保留旧快照、置 cancelledNotice（取消提示只在真实终态出现）
 * - 重试终态：目标平台按 status 分支（失败保留旧结果+记错误；成功/empty 替换并重排）
 * - 全量终态：整体替换快照
 * - search_start 与全量终态提交都会清空 retryErrors（新搜索不残留旧重试错误）；
 *   retry_start 只清除目标平台自己的旧错误。
 * 历史只在 search_accepted（POST 被接受）后增加；search_rejected 不加。
 * reset 只清任务与展示，历史与平台偏好原样保留。
 */
export function applySearchTransition(state: ExperienceState, event: ExperienceEvent): ExperienceState {
  const d = state.display;
  switch (event.type) {
    case "search_start": {
      return {
        ...state,
        display: {
          ...d,
          // 新任务开始：快照保留；有旧结果才标记"正在更新"。
          refreshing: d.jobResponse !== null,
          liveResponse: null, // 上一任务的实时进度作废
          retryingPlatform: null,
          retryErrors: {}, // 新的完整搜索不残留旧重试错误（Round 12.2）。
          cancelledNotice: false,
          cancelRequested: false, // 新搜索清除旧的取消失败提示（Round 13）
          cancelError: null,
          awaitingJobAcceptance: true, // POST 尚未被接受：拒绝无身份终态
        },
      };
    }
    case "search_accepted": {
      return {
        ...state,
        // 只有 POST 被接受才写入身份与历史。
        display: {
          ...d,
          activeJobId: event.jobId,
          awaitingJobAcceptance: false,
        },
        history: addHistoryItem(state.history, event.keyword, event.platforms, event.nowIso),
      };
    }
    case "retry_accepted": {
      // 重试被接受：写入身份，不写历史（与 Round 12 语义一致）。
      return {
        ...state,
        display: {
          ...d,
          activeJobId: event.jobId,
          awaitingJobAcceptance: false,
        },
      };
    }
    case "search_rejected": {
      const retrying = d.retryingPlatform;
      return {
        ...state,
        display: {
          ...d,
          refreshing: false,
          liveResponse: null, // POST 失败：无新任务的实时进度
          awaitingJobAcceptance: false,
          // 重试创建失败：记录失败摘要，退出重试态（快照保留）。
          ...(retrying
            ? {
                retryingPlatform: null,
                retryErrors: {
                  ...d.retryErrors,
                  [retrying]: event.errorSummary || "更新失败，请稍后重试",
                },
              }
            : {}),
        },
      };
    }
    case "retry_start": {
      return {
        ...state,
        display: {
          ...d,
          retryingPlatform: event.platform,
          liveResponse: null, // 重试是新的身份，旧实时进度作废
          cancelledNotice: false,
          cancelRequested: false, // 新任务开始清除旧的取消失败提示（Round 13）
          cancelError: null,
          // 只清除目标平台自己的旧错误（其他平台错误保留）。
          retryErrors: omitKey(d.retryErrors, event.platform),
          awaitingJobAcceptance: true, // POST 尚未被接受：拒绝无身份终态
        },
      };
    }
    case "job_recovered": {
      // 页面加载恢复的任务：仅当没有用户发起的任务时登记身份。
      if (d.activeJobId !== null || d.awaitingJobAcceptance) return state;
      return {
        ...state,
        display: {
          ...d,
          activeJobId: event.jobId,
        },
      };
    }
    case "job_progress": {
      const job = event.job;
      // 与 job_terminal 相同的身份保护（Phase 2 渐进展示）：
      // 1. POST 未接受（无身份）→ 拒绝任何进度。
      // 2. 无当前任务身份（未恢复/已 reset）→ 拒绝。
      // 3. 进度 job_id 与当前任务不匹配（旧任务迟到）→ 拒绝。
      // 4. 已应用终态的 job_id 不接收进度（终态已提交，轮询迟到响应作废）。
      // 5. 终态 overall 交给 job_terminal，这里不处理。
      if (d.awaitingJobAcceptance) return state;
      if (d.activeJobId === null) return state;
      if (job.job_id !== d.activeJobId) return state;
      if (d.appliedJobIds.has(job.job_id)) return state;
      if (TERMINAL_OVERALLS.has(job.overall)) return state;
      // 单平台重试保持"终态后合并"策略：不做渐进替换（Phase 2 语义）。
      if (d.retryingPlatform) return state;
      return {
        ...state,
        display: {
          ...d,
          liveResponse: job,
        },
      };
    }
    case "job_terminal": {
      const job = event.job;
      // 身份保护（Round 12.2）：
      // 1. POST 已发出但未被接受（无身份）→ 拒绝任何终态。
      // 2. 无当前任务身份（未恢复、已 reset）→ 拒绝。
      // 3. 终态 job_id 与当前任务不匹配（旧任务迟到）→ 拒绝。
      if (d.awaitingJobAcceptance) return state;
      if (d.activeJobId === null) return state;
      if (job.job_id !== d.activeJobId) return state;
      // 终态提交后，后台 hydration 仍会重复返回同一个 job_id；只替换
      // 结果文本/状态，不重复写历史或改变任务身份。
      if (d.appliedJobIds.has(job.job_id)) {
        if (d.jobResponse && (job.hydration_status === "running" ||
            job.hydration_status === "completed")) {
          // 同一个 job 的补全轮询：只应该多信息（摘要/指标），不应该少结果。
          // 结果变少 = 后端回归（曾因此把单平台重搜后的其它平台结果清掉），保住当前结果。
          const results = job.results.length >= (d.jobResponse.results.length ?? 0)
            ? job.results
            : d.jobResponse.results;
          return {
            ...state,
            display: {
              ...d,
              jobResponse: { ...job, results },
            },
          };
        }
        return state;
      }
      if (!TERMINAL_OVERALLS.has(job.overall)) return state;

      if (job.overall === "cancelled") {
        // 取消：真实终态才亮提示；终态响应自身带回已收集的部分结果 →
        // 有结果则保留这些部分结果，一条都没有才保留旧快照（Phase 2）。
        const hasPartialResults = (job.results?.length ?? 0) > 0;
        return {
          ...state,
          display: {
            ...d,
            jobResponse: hasPartialResults || job.exploration ? job : d.jobResponse,
            liveResponse: null,
            refreshing: false,
            retryingPlatform: null,
            cancelledNotice: true,
            cancelRequested: false,
            cancelError: null,
            appliedJobIds: new Set(d.appliedJobIds).add(job.job_id),
          },
        };
      }

      if (d.retryingPlatform) {
        const retryTarget = d.retryingPlatform;
        const newInfo = job.platforms[retryTarget];
        const status = infoStatus(newInfo);
        const prev = d.jobResponse;
        // 重试必有 prev（keyword 来自 prev）；prev 的四平台信息全量保留，
        // 只覆盖目标平台 —— 其他平台状态与数量不变。
        const platformsInfo = { ...(prev?.platforms ?? {}) } as Record<
          PlatformSlug,
          SearchJobResponse["platforms"][PlatformSlug]
        >;
        platformsInfo[retryTarget] = newInfo;
        const overall = recomputeOverall(Object.values(platformsInfo).map((i) => i.status));
        const baseResponse: SearchJobResponse = {
          job_id: job.job_id,
          overall,
          keyword: prev?.keyword ?? job.keyword,
          created_at: prev?.created_at ?? job.created_at,
          completed_at: job.completed_at,
          platforms: platformsInfo,
          results: [],
          // exploration 必须带回：后续单平台重搜靠它判断"能否续会话"
          // （continue_from）。丢了它，下一次重搜会变成独立会话，
          // 后端整体替换会话后其它平台的结果就再也回不来了。
          exploration: job.exploration ?? prev?.exploration,
          hydration_status: job.hydration_status,
          total_ms: job.total_ms,
        };
        if (FAILURE_STATUSES.has(status)) {
          // 失败：保留目标平台旧结果与其他平台结果（不调用 merge），
          // 只更新状态为 failed 并记录安全错误摘要。
          return {
            ...state,
            display: {
              ...d,
              jobResponse: { ...baseResponse, results: prev?.results ?? [] },
              liveResponse: null,
              retryErrors: {
                ...d.retryErrors,
                [retryTarget]: eventSafeRetrySummary(newInfo?.error_summary),
              },
              retryingPlatform: null,
              refreshing: false,
              cancelRequested: false, // 正常终态清除取消失败提示（Round 13）
              cancelError: null,
              appliedJobIds: new Set(d.appliedJobIds).add(job.job_id),
            },
          };
        }
        // 成功 / empty：替换目标平台结果与状态，重新轮询交错生成综合顺序，
        // 清除该平台此前的失败提示。
        // 单平台重搜（⟳）走这里：后端已把该平台的分页重置并重搜，
        // 所以只替换它在当前批次里的内容，其它平台与批次结构都不动。
        const mergedResults = mergeSinglePlatformRetry(
          prev?.results ?? [],
          retryTarget,
          job.results.filter((r) => r.platform === retryTarget),
          prev ? (Object.keys(prev.platforms) as PlatformSlug[]) : PLATFORM_SLUGS
        );
        return {
          ...state,
          display: {
            ...d,
            jobResponse: { ...baseResponse, results: mergedResults },
            liveResponse: null,
            retryErrors: omitKey(d.retryErrors, retryTarget),
            retryingPlatform: null,
            refreshing: false,
            cancelRequested: false, // 正常终态清除取消失败提示（Round 13）
            cancelError: null,
            appliedJobIds: new Set(d.appliedJobIds).add(job.job_id),
          },
        };
      }

      // ── 全量任务终态：整体替换快照；再次确保旧重试错误不残留；
      //    正常终态同时清除取消失败提示（Round 13）。 ──
      return {
        ...state,
        display: {
          ...d,
          jobResponse: job,
          liveResponse: null,
          refreshing: false,
          retryErrors: {}, // Round 12.2：新全量终态不恢复旧重试错误
          cancelRequested: false,
          cancelError: null,
          appliedJobIds: new Set(d.appliedJobIds).add(job.job_id),
        },
      };
    }
    case "cancel_requested": {
      // 取消请求已发出：不清快照、不提前显示已取消提示；
      // 清除上一失败提示（允许再次取消重试）。
      return {
        ...state,
        display: {
          ...d,
          cancelRequested: true,
          cancelError: null,
        },
      };
    }
    case "cancel_rejected": {
      // 取消请求失败：记录安全提示，不改变任务身份、不伪造终态。
      // 任务仍在运行，轮询继续；用户可稍后再次取消。
      return {
        ...state,
        display: {
          ...d,
          cancelRequested: false,
          cancelError: event.safeMessage,
        },
      };
    }
    case "reset": {
      return {
        ...state,
        // 历史与平台偏好保留；只清任务与展示（身份与提交标记一并清空，
        // 之后迟到的旧任务终态因 activeJobId=null 被拒绝）。
        display: {
          jobResponse: null,
          liveResponse: null,
          refreshing: false,
          retryingPlatform: null,
          retryErrors: {},
          cancelledNotice: false,
          cancelRequested: false,
          cancelError: null,
          activeJobId: null,
          awaitingJobAcceptance: false,
          appliedJobIds: new Set(),
        },
      };
    }
    case "history_remove": {
      return { ...state, history: removeHistoryItem(state.history, event.index) };
    }
    case "history_clear": {
      return { ...state, history: [] };
    }
    case "platform_pref_set": {
      // 规范化：过滤非法 slug、去重；空 → 保持原偏好。
      // 不变量的边界：首次进入（readPlatformPref 无存储值）允许空集；但偏好一旦
      // 落地（用户勾选过任一平台），传入空集时保留原偏好，避免把已落地的偏好清空。
      // UI 层始终允许手动取消到零，只是提交时统一提示"先选平台"。
      const valid = [...new Set(event.platforms.filter(isPlatformSlug))];
      if (valid.length === 0) return state;
      return { ...state, platformPref: valid };
    }
    default:
      return state;
  }
}

function omitKey<K extends string, V>(obj: Partial<Record<K, V>>, key: K): Partial<Record<K, V>> {
  const next: Record<string, V> = { ...obj } as Record<string, V>;
  delete next[key];
  return next as Partial<Record<K, V>>;
}

/** 重试失败的安全摘要：优先用平台 error_summary（后端已脱敏），否则固定文案。 */
function eventSafeRetrySummary(errorSummary: string | null | undefined): string {
  const summary =
    typeof errorSummary === "string" && errorSummary.trim() ? errorSummary.trim() : "";
  return summary || "更新失败，请稍后重试";
}

// ── 平台标签（activeTab）回退 ───────────────────────────────────────────

/**
 * 当前激活标签不在可见标签集合中时回退到 fallback（"全部"）。
 * 例如：单平台重试后目标平台消失、或新搜索结果不含某平台。
 */
export function resolveActiveTab<T extends string>(
  activeTab: T | null | undefined,
  visibleTabs: readonly T[],
  fallback: T
): T {
  if (activeTab !== null && activeTab !== undefined && visibleTabs.includes(activeTab)) {
    return activeTab;
  }
  return fallback;
}

// ── 渐进结果展示（Phase 2 生产 selector，无 React 依赖） ────────────────

export interface SearchPresentation {
  /** UI 实际渲染的响应（结果 + 平台状态 + overall）。 */
  jobResponse: SearchJobResponse | null;
  /** true：结果列表仍是旧快照，新任务在搜索中（live 尚无首条结果）。 */
  showingStaleSnapshot: boolean;
  /** 实时提示：`已返回 N 条，仍在搜索 X 个平台` / 旧快照提示；终态为 null。 */
  liveHint: string | null;
}

/**
 * 决定当前展示内容（Phase 2）：
 * - 无 live 响应（POST 挂起 / 终态已提交）：返回已提交快照。
 * - 有 live 响应：状态卡片与 overall 始终用 live（当前任务实时状态）；
 *   结果列表在 live 出现首条结果后立即切换为 live 结果，之前保留旧快照
 *   并给出"正在搜索，暂时显示上次结果"提示。
 * - 实时提示在任务终态后自动消失。
 */
export function selectSearchPresentation(state: ExperienceState): SearchPresentation {
  const d = state.display;
  const live = d.liveResponse;
  const committed = d.jobResponse;
  if (!live) {
    return { jobResponse: committed, showingStaleSnapshot: false, liveHint: null };
  }

  const liveCount = live.results.length;
  const terminal = TERMINAL_OVERALLS.has(live.overall);
  const searching = Object.values(live.platforms).filter(
    (i) => i.status === "pending" || i.status === "running"
  ).length;

  let liveHint: string | null = null;
  if (!terminal) {
    if (liveCount > 0 && searching > 0) {
      liveHint = `已返回 ${liveCount} 条，仍在搜索 ${searching} 个平台`;
    } else if (liveCount === 0 && committed !== null) {
      liveHint = "正在搜索，暂时显示上次结果";
    }
  }

  const showLiveResults = liveCount > 0;
  return {
    jobResponse: {
      ...live,
      results: showLiveResults ? live.results : (committed?.results ?? []),
    },
    showingStaleSnapshot: !terminal && !showLiveResults && committed !== null,
    liveHint,
  };
}
