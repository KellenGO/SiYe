import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { PLATFORM_SLUGS } from "../platformMeta.js";

export type SearchSortMode = "default" | "latest" | "engagement";

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
