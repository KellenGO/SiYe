import type { PlatformSlug, UnifiedSearchResult } from "../types/search.js";
import { PLATFORM_LABELS } from "../types/search.js";

export type ContentFilter = "all" | "video" | "note" | "article";
export interface ResultFilters {
  days: 0 | 7 | 30;
  contentType: ContentFilter;
  query: string;
}
export const DEFAULT_FILTERS: ResultFilters = { days: 0, contentType: "all", query: "" };

export function resultKey(result: Pick<UnifiedSearchResult, "platform" | "content_id">): string {
  return `${result.platform}|${result.content_id}`;
}

export function resultSources(result: UnifiedSearchResult): UnifiedSearchResult[] {
  const sources = result.grouped_sources;
  return (sources && sources.length >= 2 ? sources : [result])
    .map((source) => ({ ...source, metrics: { ...source.metrics }, grouped_sources: null }));
}

export function groupKey(result: UnifiedSearchResult): string {
  return JSON.stringify(resultSources(result).map(resultKey).sort());
}

/**
 * 把 groupKey 还原成 resultKey 列表（与 groupKey 对称）。
 *
 * 结果列表按「分组」勾选，但收藏库的接口认的是单条 `platform|content_id`
 * （见 libraryApi.splitKey），所以批量加入/移出收藏夹前必须做一次转换。
 * 无法解析时返回空数组，由调用方决定是否提示。
 */
export function parseGroupKey(key: string): string[] {
  try {
    const parsed: unknown = JSON.parse(key);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is string => typeof item === "string" && item.includes("|"));
  } catch {
    return [];
  }
}

export function safeContentUrl(url: string): string | null {
  const domains = ["xiaohongshu.com", "xhslink.com", "rednote.com", "douyin.com", "bilibili.com", "zhihu.com"];
  try {
    const parsed = new URL(url.trim());
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
    return domains.some((domain) => parsed.hostname === domain || parsed.hostname.endsWith(`.${domain}`))
      ? parsed.href : null;
  } catch {
    return null;
  }
}

const normalize = (text: string) => text.normalize("NFKC").toLowerCase();

export function matchesFilters(result: UnifiedSearchResult, filters: ResultFilters, nowMs: number): boolean {
  if (filters.days) {
    const published = Date.parse(result.published_at || "");
    if (!Number.isFinite(published) || published > nowMs || published < nowMs - filters.days * 86400000) return false;
  }
  const types: Record<Exclude<ContentFilter, "all">, string[]> = {
    video: ["video", "short_video", "zvideo"], note: ["note", "post"], article: ["article", "answer"],
  };
  if (filters.contentType !== "all" && !types[filters.contentType].includes(result.content_type)) return false;
  const haystack = normalize([result.title, result.author, result.snippet].filter(Boolean).join(" "));
  return normalize(filters.query).trim().split(/\s+/u).filter(Boolean).every((token) => haystack.includes(token));
}

/** All conditions must match the same source; keep only matching versions of a group. */
export function filterResultGroups(
  results: readonly UnifiedSearchResult[], filters: ResultFilters, nowMs: number,
  platform: "all" | PlatformSlug = "all"
): UnifiedSearchResult[] {
  const seen = new Set<string>();
  return results.flatMap((result) => {
    const sources = resultSources(result).filter((source) =>
      (platform === "all" || source.platform === platform) && matchesFilters(source, filters, nowMs));
    if (!sources.length) return [];
    const representative = sources.find((source) => resultKey(source) === resultKey(result)) || sources[0];
    const visible = { ...representative, grouped_sources: sources.length >= 2 ? sources : null };
    // 后端会合并旧 default 归档与已识别账号归档；这里仍守住边界，
    // 不能把同一 identity 交给 React，否则切换页签时会复用错误的卡片节点。
    const key = groupKey(visible);
    if (seen.has(key)) return [];
    seen.add(key);
    return [visible];
  });
}

export function highlightSegments(text: string, query: string): Array<{ text: string; matched: boolean }> {
  const tokens = [...new Set(query.trim().split(/\s+/u).filter(Boolean))].sort((a, b) => b.length - a.length);
  if (!tokens.length) return [{ text, matched: false }];
  const pattern = tokens.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  return text.split(new RegExp(`(${pattern})`, "giu"))
    .map((part, index) => ({ text: part, matched: index % 2 === 1 }));
}

/**
 * 互动数据统一展示顺序：播放 → 点赞 → 投币 → 评论 → 收藏，分享排在最后。
 * 投币只有 B站 有（且仅详情接口下发），排在点赞之后。弹幕对判断内容质量帮助
 * 有限，不参与展示。平台不提供的字段不会占位。
 */
export const METRIC_ORDER: ReadonlyArray<readonly [string, string]> = [
  ["view_count", "播放"],
  ["like_count", "点赞"],
  ["coin_count", "投币"],
  ["comment_count", "评论"],
  ["collect_count", "收藏"],
  ["share_count", "分享"],
];

/** 按固定顺序显示已知指标，包括真实的 0；缺失或非法值不占位。 */
export function orderedMetrics(
  metrics: Record<string, number> | null | undefined, limit = 6
): Array<{ key: string; label: string }> {
  const source = metrics || {};
  return METRIC_ORDER
    .filter(([key]) => Number.isFinite(source[key]) && source[key] >= 0)
    .slice(0, limit)
    .map(([key, label]) => ({ key, label }));
}

export interface ExportRow {
  result: UnifiedSearchResult;
  fetchedAt: string | null;
  savedAt: string | null;
  note: string;
}

export function exportRows(
  results: readonly UnifiedSearchResult[],
  metadata: (source: UnifiedSearchResult) => Omit<ExportRow, "result">
): ExportRow[] {
  const seen = new Set<string>();
  return results.flatMap(resultSources).flatMap((result) => {
    const key = resultKey(result);
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ result, ...metadata(result) }];
  });
}

function csvCell(value: string): string {
  // Keep untrusted titles/notes as text when the CSV is opened in a spreadsheet.
  const safe = /^[\s\u0000-\u001f]*[=+\-@]/u.test(value) ? `'${value}` : value;
  return `"${safe.replace(/"/g, '""')}"`;
}

export function resultsCsv(rows: readonly ExportRow[]): string {
  const header = ["平台", "平台收藏夹", "标题", "作者", "内容类型", "发布时间", "采集时间", "收藏时间", "备注", "摘要", "原文链接"];
  const values = rows.map(({ result, fetchedAt, savedAt, note }) => [
    PLATFORM_LABELS[result.platform], (result.collection_names || []).join(" / "), result.title, result.author || "", result.content_type,
    result.published_at || "", fetchedAt || "", savedAt || "", note, result.snippet || "",
    safeContentUrl(result.url) || "",
  ]);
  return "\uFEFF" + [header, ...values].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

const markdownText = (value: string) => value.replace(/[\r\n]+/g, " ").replace(/[\\`*_{}[\]()<>#!|]/g, "\\$&");

export function resultsMarkdown(rows: readonly ExportRow[]): string {
  return "# 搜索结果\n\n" + rows.map(({ result, fetchedAt, savedAt, note }) => {
    const url = safeContentUrl(result.url)?.replace(/</g, "%3C").replace(/>/g, "%3E");
    return [
      `## ${markdownText(result.title)}`,
      `平台：${PLATFORM_LABELS[result.platform]} · 作者：${markdownText(result.author || "未知")}`,
      result.collection_names?.length ? `平台收藏夹：${markdownText(result.collection_names.join(" / "))}` : "",
      `发布时间：${markdownText(result.published_at || "未知")} · 采集时间：${markdownText(fetchedAt || "未知")}`,
      savedAt ? `收藏时间：${markdownText(savedAt)}` : "",
      result.snippet ? markdownText(result.snippet) : "",
      note ? `备注：${markdownText(note)}` : "",
      url ? `[打开原文](<${url}>)` : "原文链接不可用",
    ].filter(Boolean).join("\n\n");
  }).join("\n\n---\n\n") + "\n";
}

export function resultLinks(rows: readonly ExportRow[]): string {
  return [...new Set(rows.map((row) => safeContentUrl(row.result.url)).filter(Boolean))].join("\n");
}
