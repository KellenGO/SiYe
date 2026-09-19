/**
 * 热搜词 API 客户端（后端 /api/trending）。
 *
 * 热搜榜只取"词"：点一条 → 交给既有的聚合搜索去搜，内容不用这里管。
 * 按平台分开返回，聚合视图（如果有）由展示层决定 —— 聚合不可逆，分平台随时能合。
 *
 * 分两层，便于测试：
 * - 纯函数：规整 / 选平台 / 取前 N / 热度格式化（trending.test.ts 覆盖）；
 * - IO 函数：axios 调后端。
 */

import axios from "axios";

import type { PlatformSlug } from "@/types/search";
import { PLATFORM_SLUGS } from "./platformMeta.js";

export const TRENDING_API_BASE = "/api/trending";

/** 面板每列最多放几条词（后端会返回更多，展示层自己截）。 */
export const MAX_WORDS_PER_COLUMN = 10;

export interface TrendingWord {
  rank: number;
  word: string;
  hotValue: number | null;
}

export type PlatformTrendingStatus = "ok" | "unavailable" | "failed";

export interface PlatformTrending {
  status: PlatformTrendingStatus;
  words: TrendingWord[];
  activeTime: string | null;
  message: string | null;
}

export interface TrendingSnapshot {
  platforms: Partial<Record<PlatformSlug, PlatformTrending>>;
  fetchedAt: string | null;
  cached: boolean;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const STATUSES: PlatformTrendingStatus[] = ["ok", "unavailable", "failed"];

/** 后端 word -> 前端 TrendingWord；缺 word 的项跳过。 */
export function toTrendingWords(raw: unknown): TrendingWord[] {
  if (!Array.isArray(raw)) return [];
  const words: TrendingWord[] = [];
  for (const item of raw) {
    if (!isRecord(item)) continue;
    const word = typeof item.word === "string" ? item.word.trim() : "";
    if (!word) continue;
    const hot = item.hot_value;
    words.push({
      rank: Number.isFinite(Number(item.rank)) ? Number(item.rank) : words.length + 1,
      word,
      hotValue: typeof hot === "number" && Number.isFinite(hot) ? hot : null,
    });
  }
  return words;
}

export function toPlatformTrending(raw: unknown): PlatformTrending {
  if (!isRecord(raw)) {
    return { status: "failed", words: [], activeTime: null, message: null };
  }
  const status = STATUSES.includes(raw.status as PlatformTrendingStatus)
    ? (raw.status as PlatformTrendingStatus)
    : "failed";
  return {
    status,
    words: toTrendingWords(raw.words),
    activeTime: typeof raw.active_time === "string" ? raw.active_time : null,
    message: typeof raw.message === "string" ? raw.message : null,
  };
}

export function toTrendingSnapshot(raw: unknown): TrendingSnapshot {
  const platformsRaw = isRecord(raw) && isRecord(raw.platforms) ? raw.platforms : {};
  const platforms: Partial<Record<PlatformSlug, PlatformTrending>> = {};
  for (const slug of PLATFORM_SLUGS) {
    if (slug in platformsRaw) platforms[slug] = toPlatformTrending(platformsRaw[slug]);
  }
  return {
    platforms,
    fetchedAt: isRecord(raw) && typeof raw.fetched_at === "string" ? raw.fetched_at : null,
    cached: isRecord(raw) ? raw.cached === true : false,
  };
}

/** 有词的平台，按固定顺序（小红书 / 抖音 / B站 / 知乎）。 */
export function visiblePlatforms(snapshot: TrendingSnapshot | undefined): PlatformSlug[] {
  if (!snapshot) return [];
  return PLATFORM_SLUGS.filter((slug) => (snapshot.platforms[slug]?.words.length ?? 0) > 0);
}

/** 每列只展示前 N 条。 */
export function topWords(words: TrendingWord[], limit: number = MAX_WORDS_PER_COLUMN): TrendingWord[] {
  return words.slice(0, Math.max(0, limit));
}

/** 热度：`1.2亿` / `1194万` / `3456`；拿不到热度返回 null（不显示占位数字）。 */
export function formatHeat(value: number | null): string | null {
  if (value === null || !Number.isFinite(value) || value < 0) return null;
  const trim = (text: string) => text.replace(/\.0$/, "");
  if (value >= 1e8) return `${trim((value / 1e8).toFixed(1))}亿`;
  if (value >= 1e4) return `${trim((value / 1e4).toFixed(value >= 1e6 ? 0 : 1))}万`;
  return String(value);
}

export async function fetchTrending(refresh = false): Promise<TrendingSnapshot> {
  const { data } = await axios.get(TRENDING_API_BASE, {
    params: refresh ? { refresh: true } : {},
  });
  return toTrendingSnapshot(data);
}
