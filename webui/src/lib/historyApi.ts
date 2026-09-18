/**
 * 观看历史 API 客户端（后端 /api/history）。
 *
 * 历史是用户点开看过的搜索结果的自动记录，存本机 SQLite，不上传。
 * 与收藏库刻意分开（避免历史污染收藏的「全部」视图）。
 *
 * 分两层，便于测试：
 * - 纯函数：后端 view -> 前端 HistoryView 的转换（historyApi.test.ts 覆盖）；
 * - IO 函数：axios 调用后端；记录是 fire-and-forget，失败被吞掉，绝不阻塞跳转。
 */

import axios from "axios";

import type { UnifiedSearchResult } from "@/types/search";
import { publicResult } from "./bookmarks.js";

export const HISTORY_API_BASE = "/api/history";

export interface HistoryView {
  id: number;
  key: string;
  result: UnifiedSearchResult;
  firstViewedAt: string;
  lastViewedAt: string;
  viewCount: number;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** 后端 view -> 前端 HistoryView；格式不合法时返回 null（跳过而不是整页崩）。 */
export function toHistoryView(raw: unknown): HistoryView | null {
  if (!isRecord(raw)) return null;
  let result: UnifiedSearchResult;
  try {
    result = publicResult(raw.result);
  } catch {
    return null;
  }
  return {
    id: Number(raw.id) || 0,
    key: typeof raw.key === "string" && raw.key ? raw.key : `${result.platform}|${result.content_id}`,
    result,
    firstViewedAt: typeof raw.first_viewed_at === "string" ? raw.first_viewed_at : new Date().toISOString(),
    lastViewedAt: typeof raw.last_viewed_at === "string" ? raw.last_viewed_at : new Date().toISOString(),
    viewCount: Number(raw.view_count) || 0,
  };
}

export function toHistoryViews(raw: unknown): HistoryView[] {
  const list = isRecord(raw) && Array.isArray(raw.items) ? raw.items : [];
  return list.map(toHistoryView).filter((view): view is HistoryView => view !== null);
}

/** 记录一次观看：fire-and-forget，任何失败都吞掉，绝不阻塞跳转。 */
export async function recordView(result: UnifiedSearchResult): Promise<void> {
  try {
    await axios.post(`${HISTORY_API_BASE}/views`, { result });
  } catch {
    /* 记录失败不影响用户继续浏览 */
  }
}

export async function fetchViews(limit?: number, offset = 0): Promise<HistoryView[]> {
  const { data } = await axios.get(`${HISTORY_API_BASE}/views`, {
    params: limit == null ? {} : { limit, offset },
  });
  return toHistoryViews(data);
}

export async function deleteView(key: string): Promise<void> {
  const index = key.indexOf("|");
  if (index <= 0 || index === key.length - 1) return;
  const platform = key.slice(0, index);
  const contentId = key.slice(index + 1);
  await axios.delete(
    `${HISTORY_API_BASE}/views/${encodeURIComponent(platform)}/${encodeURIComponent(contentId)}`,
  );
}

export async function clearViews(): Promise<void> {
  await axios.delete(`${HISTORY_API_BASE}/views`);
}
