import type { GroupedSource, PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { PLATFORM_SLUGS } from "../platformMeta.js";

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
    duration_seconds: source.duration_seconds,
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
        duration_seconds: results[index].duration_seconds,
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
