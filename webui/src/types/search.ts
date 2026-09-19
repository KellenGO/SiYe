export type PlatformSlug = "xhs" | "douyin" | "bilibili" | "zhihu";

export type PlatformStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "empty"
  | "login_required"
  | "rate_limited"
  | "timed_out"
  | "failed"
  | "cancelled";

export type OverallStatus = "running" | "completed" | "partial" | "failed" | "cancelling" | "cancelled";
export type HydrationStatus = "not_started" | "running" | "completed";

export interface GroupedSource {
  platform: PlatformSlug;
  content_id: string;
  content_type: string;
  title: string;
  url: string;
  author: string | null;
  published_at: string | null;
  snippet?: string | null;
  metrics: Record<string, number>;
  cover_url: string | null;
  /** 原平台 rank，用于单平台 Tab 的稳定排序。 */
  rank: number;
}

export interface UnifiedSearchResult {
  platform: PlatformSlug;
  content_id: string;
  content_type: string;
  title: string;
  snippet?: string | null;
  author: string | null;
  url: string;
  published_at: string | null;
  cover_url: string | null;
  metrics: Record<string, number>;
  rank: number;
  grouped_sources?: GroupedSource[] | null;
  collection_names?: string[];
  metrics_status?: "pending" | "complete" | "partial" | "unavailable" | "failed" | null;
  metrics_updated_at?: number | null;
  metrics_approximate?: string[];
}

export interface FavoritePlatformInfo {
  synced_at?: string | null;
  status: PlatformStatus;
  result_count: number;
  error_summary: string | null;
}

export interface FavoritesJobResponse {
  persistence_error?: string | null;
  job_id: string;
  overall: "running" | "completed" | "partial" | "failed";
  created_at: string;
  completed_at: string | null;
  platforms: Partial<Record<PlatformSlug, FavoritePlatformInfo>>;
  results: UnifiedSearchResult[];
}

export interface PlatformTimingInfo {
  page_requests?: number;
  duplicate_count?: number;
  /** worker 子进程创建耗时（毫秒） */
  spawn_ms: number | null;
  /** 从 job 开始到该平台首条合法结果（毫秒） */
  first_result_ms: number | null;
  /** 平台进入终态的总耗时（毫秒） */
  total_ms: number | null;
  /** 实际完成搜索的安全 provider slug；旧响应可能缺失 */
  provider_used?: "session_api" | "light_api" | "browser" | "page_api" | "public_search" | null;
  provider_attempt_count?: number | null;
  provider_attempts?: Array<"session_api" | "light_api" | "browser" | "page_api" | "public_search"> | null;
  fallback_active?: boolean | null;
  fallback_reason?: string | null;
}

export interface PlatformStatusInfo {
  status: PlatformStatus;
  result_count: number;
  error_summary: string | null;
  /** 耗时指标；后端无数据时为 null，旧响应可能缺失 */
  timings?: PlatformTimingInfo | null;
  cache_hit?: boolean;
  fetched_at?: string | null;
  cooldown_until?: string | null;
  cooldown_skipped?: boolean;
}

export interface SearchJobResponse {
  exploration?: SearchExploration | null;
  job_id: string;
  overall: OverallStatus;
  keyword: string;
  created_at: string;
  completed_at: string | null;
  /** job 级总耗时（毫秒）；job 未完成时为 null */
  total_ms?: number | null;
  platforms: Record<PlatformSlug, PlatformStatusInfo>;
  results: UnifiedSearchResult[];
  hydration_status?: HydrationStatus;
}

export interface SearchBatch {
  number: number;
  job_id: string;
  overall: OverallStatus;
  completed_at: string | null;
  platforms: Record<PlatformSlug, PlatformStatusInfo>;
  results: UnifiedSearchResult[];
}

export interface SearchExploration {
  id: string;
  round: number;
  max_per_platform: number;
  new_sources: number;
  new_contents: number;
  page_requests: number;
  duplicates: number;
  platforms: Partial<Record<PlatformSlug, { collected: number; has_more: boolean }>>;
  previous_batches: SearchBatch[];
}

export interface SearchJobRequest {
  continue_from?: string;
  keyword: string;
  platforms?: PlatformSlug[];
  limit_per_platform?: number;
  /** 按平台独立数量（1–40 整数）；缺失平台回退 limit_per_platform。 */
  platform_limits?: Partial<Record<PlatformSlug, number>>;
  /** 普通搜索允许命中短缓存；显式重新搜索时设为 true。 */
  bypass_cache?: boolean;
  /**
   * 单平台重搜（搜索结果页「搜索范围」里的 ⟳）：把本次结果作为**当前批次里
   * 这些平台的替换**，而不是新增一批。与「换一批」的区别就在这里。
   */
  replace_platforms?: boolean;
}

export const PLATFORM_LABELS: Record<PlatformSlug, string> = {
  xhs: "小红书",
  douyin: "抖音",
  bilibili: "B站",
  zhihu: "知乎",
};

export const PLATFORM_COLORS: Record<PlatformSlug, string> = {
  xhs: "#ef3340",
  douyin: "#111111",
  bilibili: "#23ade5",
  zhihu: "#1677c8",
};

export const STATUS_LABELS: Record<PlatformStatus, string> = {
  pending: "等待中",
  running: "搜索中",
  succeeded: "已完成",
  empty: "无结果",
  login_required: "需要登录",
  rate_limited: "请求受限，稍后重试",
  timed_out: "超时",
  failed: "失败",
  cancelled: "已取消",
};
