/**
 * 收藏相关的共享常量与校验。
 *
 * 历史说明：收藏最早存浏览器 localStorage（本文件曾有读写/备份/合并等函数），
 * 收藏库改造（后端 /api/library + SQLite）后这些逻辑移到了后端与 libraryApi.ts，
 * 这里只保留仍然被前端使用的部分：
 * - ``publicResult``：把一条结果裁剪成可持久化的公开字段（入库白名单）；
 * - ``MAX_NOTE_LENGTH`` / ``MAX_BACKUP_BYTES``：备注与备份文件的前端校验上限；
 * - ``Bookmark``：收藏条目的前端形状。
 */

import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { safeContentUrl } from "./resultTools.js";
import { isPlatformSlug } from "./platformMeta.js";

export const MAX_NOTE_LENGTH = 1000;
export const MAX_BACKUP_BYTES = 64 * 1024 * 1024;

export interface Bookmark {
  result: UnifiedSearchResult;
  savedAt: string;
  fetchedAt: string | null;
  note: string;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const optionalText = (value: unknown): string | null => typeof value === "string" ? value : null;
const validTime = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));

/** Store public DTO fields only; never persist arbitrary extra data from a response.
 *  收藏库（后端 SQLite）也复用这个白名单，保证入库内容与本地收藏一致。 */
export function publicResult(value: unknown): UnifiedSearchResult {
  if (!isRecord(value) || !isPlatformSlug(value.platform)
      || typeof value.content_id !== "string" || !value.content_id || typeof value.title !== "string"
      || typeof value.url !== "string" || !safeContentUrl(value.url)) throw new Error("收藏内容格式或原文链接无效");
  const metrics: Record<string, number> = {};
  if (isRecord(value.metrics)) {
    for (const key of ["like_count", "view_count", "collect_count", "comment_count", "share_count", "coin_count", "danmaku_count"]) {
      const count = value.metrics[key];
      if (typeof count === "number" && Number.isFinite(count) && count >= 0) metrics[key] = count;
    }
  }
  const collectionNames = Array.isArray(value.collection_names)
    ? value.collection_names.filter((name): name is string => typeof name === "string" && name.length <= 200).slice(0, 20)
    : [];
  return {
    platform: value.platform as PlatformSlug, content_id: value.content_id, title: value.title,
    url: safeContentUrl(value.url)!, content_type: typeof value.content_type === "string" ? value.content_type : "note",
    author: optionalText(value.author), snippet: optionalText(value.snippet),
    published_at: validTime(value.published_at) ? value.published_at : null,
    cover_url: optionalText(value.cover_url), metrics,
    rank: typeof value.rank === "number" && Number.isFinite(value.rank) ? value.rank : 0,
    grouped_sources: null,
    collection_names: collectionNames,
    metrics_status: ["pending", "complete", "partial", "unavailable", "failed"].includes(String(value.metrics_status))
      ? value.metrics_status as UnifiedSearchResult["metrics_status"] : null,
    metrics_updated_at: typeof value.metrics_updated_at === "number" && Number.isFinite(value.metrics_updated_at) ? value.metrics_updated_at : null,
    metrics_approximate: Array.isArray(value.metrics_approximate)
      ? value.metrics_approximate.filter((key): key is string => typeof key === "string" && key in metrics) : [],
  };
}
