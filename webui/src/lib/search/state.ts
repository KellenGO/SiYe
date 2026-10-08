import type { PlatformSlug, SearchJobResponse } from "@/types/search";
import { PLATFORM_SLUGS, isPlatformSlug } from "../platformMeta.js";
import type { SearchHistoryItem } from "./preferences.js";
import { addHistoryItem, removeHistoryItem } from "./preferences.js";
import { expandGroupedResults, mergeSinglePlatformRetry } from "./results.js";

export interface BusyFlags {
  isCreating: boolean;
  isRunning: boolean;
  isCancelling: boolean;
  retryingPlatform: string | null;
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

// ── 搜索体验状态机 ────────────

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
  /** 取消请求失败的安全提示。 */
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
  /** 全量搜索或单平台重搜的实时进度；需与 activeJobId 匹配。 */
  | { type: "job_progress"; job: SearchJobResponse }
  /** 取消请求已发出：不清快照、不提前显示已取消提示。 */
  | { type: "cancel_requested" }
  /** 取消请求失败：记录安全提示，不改变任务身份、不伪造终态。 */
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
 * 搜索体验状态机 reducer。
 *
 * 任务身份保护：
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
          retryErrors: {}, // 新的完整搜索不残留旧重试错误。
          cancelledNotice: false,
          cancelRequested: false, // 新搜索清除旧的取消失败提示
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
      // 重试被接受：写入身份，不写历史。
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
          cancelRequested: false, // 新任务开始清除旧的取消失败提示
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
      // 与 job_terminal 相同的身份保护：
      // 1. POST 未接受（无身份）→ 拒绝任何进度。
      // 2. 无当前任务身份（未恢复/已 reset）→ 拒绝。
      // 3. 进度 job_id 与当前任务不匹配（旧任务迟到）→ 拒绝。
      // 4. 已应用终态的 job_id 不接收进度（终态已提交，轮询迟到响应作废）。
      // 5. 已完成收尾的终态交给 job_terminal；收尾中仍可展示结果。
      if (d.awaitingJobAcceptance) return state;
      if (d.activeJobId === null) return state;
      if (job.job_id !== d.activeJobId) return state;
      if (d.appliedJobIds.has(job.job_id)) return state;
      if (TERMINAL_OVERALLS.has(job.overall) && job.completed_at) return state;
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
      // 身份保护：
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
        // 有结果则保留这些部分结果，一条都没有才保留旧快照。
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
          // 已经展示的新结果不会因后续失败消失；尚无新结果时保留旧结果。
          const partial = expandGroupedResults(job.results).filter((r) => r.platform === retryTarget);
          return {
            ...state,
            display: {
              ...d,
              jobResponse: { ...baseResponse, results: partial.length
                ? mergeSinglePlatformRetry(prev?.results ?? [], retryTarget, partial,
                    Object.keys(platformsInfo) as PlatformSlug[])
                : prev?.results ?? [] },
              liveResponse: null,
              retryErrors: {
                ...d.retryErrors,
                [retryTarget]: eventSafeRetrySummary(newInfo?.error_summary),
              },
              retryingPlatform: null,
              refreshing: false,
              cancelRequested: false, // 正常终态清除取消失败提示
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
          expandGroupedResults(job.results).filter((r) => r.platform === retryTarget),
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
            cancelRequested: false, // 正常终态清除取消失败提示
            cancelError: null,
            appliedJobIds: new Set(d.appliedJobIds).add(job.job_id),
          },
        };
      }

      // ── 全量任务终态：整体替换快照；再次确保旧重试错误不残留；
      //    正常终态同时清除取消失败提示。 ──
      return {
        ...state,
        display: {
          ...d,
          jobResponse: job,
          liveResponse: null,
          refreshing: false,
          retryErrors: {}, // 新全量终态不恢复旧重试错误
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
      // 偏好 = 用户最后一次的勾选，空集同样落地。零勾选是合法状态（首次进入就是零，
      // 提交时由 SearchBar 提示"先勾选至少一个平台"），所以这里不再保留原偏好：
      // 保留过一次就会让存储与界面分叉 —— 用户把平台全取消、刷新后又被自动勾回来。
      const valid = [...new Set(event.platforms.filter(isPlatformSlug))];
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

// ── 渐进结果展示（生产 selector，无 React 依赖） ───────────────────────

export interface SearchPresentation {
  /** UI 实际渲染的响应（结果 + 平台状态 + overall）。 */
  jobResponse: SearchJobResponse | null;
  /** true：结果列表仍是旧快照，新任务在搜索中（live 尚无首条结果）。 */
  showingStaleSnapshot: boolean;
  /** 实时提示：`已返回 N 条，仍在搜索 X 个平台` / 旧快照提示；终态为 null。 */
  liveHint: string | null;
}

/**
 * 决定当前展示内容：
 * - 无 live 响应（POST 挂起 / 终态已提交）：返回已提交快照。
 * - 有 live 响应：状态卡片与 overall 始终用 live（当前任务实时状态）；
 *   结果列表在 live 出现首条结果后立即切换为 live 结果，之前保留旧快照
 *   并给出"正在搜索，暂时显示上次结果"提示。
 * - 单平台重搜只渐进替换目标平台，不改动其他平台的已有内容。
 * - 实时提示在任务终态后自动消失。
 */
export function selectSearchPresentation(state: ExperienceState): SearchPresentation {
  const d = state.display;
  const live = d.liveResponse;
  const committed = d.jobResponse;
  if (!live) {
    return { jobResponse: committed, showingStaleSnapshot: false, liveHint: null };
  }

  if (d.retryingPlatform && committed) {
    const target = d.retryingPlatform;
    const fresh = expandGroupedResults(live.results).filter((r) => r.platform === target);
    return {
      jobResponse: {
        ...live,
        platforms: { ...committed.platforms, ...live.platforms },
        exploration: live.exploration ?? committed.exploration,
        results: fresh.length ? mergeSinglePlatformRetry(
          committed.results, target, fresh,
          Object.keys({ ...committed.platforms, ...live.platforms }) as PlatformSlug[],
        ) : committed.results,
      },
      showingStaleSnapshot: fresh.length === 0,
      liveHint: fresh.length ? `已返回 ${fresh.length} 条，仍在搜索` : "正在搜索，暂时显示上次结果",
    };
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
