/**
 * Round 12/12.1 搜索体验 hook：快照保留 / 单平台重试 / 历史 / 平台偏好。
 *
 * Round 12.1 变更：
 * - 状态机提取为生产纯函数 applySearchTransition（lib/searchExperience.ts），
 *   本 hook 只做接线：把 base 事件转成 reducer 事件、持久化历史/偏好。
 * - 历史只在 POST 被接受（search_accepted）后写入，失败不加历史；
 *   异步完成后的旧请求（taskSeq 已前进）不写入。
 * - busy 时拒绝一切历史修改（回放/删除/清空）。
 * - cancelledNotice 只在观察到真实 job 终态 overall === "cancelled" 后出现，
 *   新搜索/重试/reset 清除，取消失败不显示。
 * - 平台偏好每次选择变化立即持久化（effect 写回 localStorage）。
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useAggregateSearch } from "./useAggregateSearch";
import { usePlatformLimits } from "./usePlatformLimits";
import type { PlatformSlug } from "@/types/search";
import { selectedPlatformLimits } from "@/lib/platformLimits";
import {
  applySearchTransition,
  createInitialExperienceState,
  isSearchBlocked,
  readHistory,
  readPlatformPref,
  safeErrorSummary,
  selectSearchPresentation,
  writeHistory,
  writePlatformPref,
  type SearchHistoryItem,
  type SearchSortMode,
} from "@/lib/searchExperience";

const TERMINAL_OVERALLS = new Set(["completed", "partial", "failed", "cancelled"]);

export function useSearchExperience() {
  const base = useAggregateSearch();
  // Round 15: 每个平台独立搜索数量（localStorage 持久化）。
  // 全量搜索、历史回放、单平台重试统一使用当前设置；不保存历史搜索时的数量。
  const { limits } = usePlatformLimits();

  // ── 状态机：展示层 + 历史 + 平台偏好全部由生产 reducer 维护 ──────────
  const [state, dispatch] = useReducer(
    applySearchTransition,
    undefined,
    () =>
      createInitialExperienceState(
        readHistory(localStorage),
        readPlatformPref(localStorage)
      )
  );

  // ── 排序（内存级，搜索/切换标签时保留） ───────────────────────────────
  const [sortMode, setSortMode] = useState<SearchSortMode>("default");

  // ── 异步防重入：POST 尚未返回（busy 尚未渲染）时再次发起必须被拒绝 ───
  const taskSeqRef = useRef(0);
  const taskInFlightRef = useRef(false);

  // ── 任务身份来源标记：区分"用户发起"与"页面加载恢复" ──────────────────
  // userStartedRef：是否曾由用户发起过搜索/重试（reset 后重置）。
  // recoveredOnceRef：本挂载周期内是否已为恢复任务登记过身份。
  const userStartedRef = useRef(false);
  const recoveredOnceRef = useRef(false);

  // ── 持久化：state 变化即写回 localStorage（安全回退，失败静默） ───────
  useEffect(() => {
    writeHistory(localStorage, state.history);
  }, [state.history]);
  useEffect(() => {
    writePlatformPref(localStorage, state.platformPref);
  }, [state.platformPref]);

  const isRunning = base.overall === "running" || base.isCreating;
  const busy = isSearchBlocked({
    isCreating: base.isCreating,
    isRunning,
    isCancelling: base.isCancelling,
    retryingPlatform: state.display.retryingPlatform,
  });

  // ── 全量搜索 / 历史回放（keyword/platforms 直接来自调用方参数） ──────
  const handleFullSearch = useCallback(
    async (keyword: string, platforms: PlatformSlug[], bypassCache = false, continueFrom?: string) => {
      if (busy || taskInFlightRef.current) return;
      const seq = ++taskSeqRef.current;
      taskInFlightRef.current = true;
      userStartedRef.current = true;
      dispatch({ type: "search_start", keyword, platforms });
      try {
        // startSearch 返回 POST 的结果：直接使用返回值的 job_id 派发
        // 身份事件（不复制 API 调用）。数量使用当前设置（Round 15）。
        const job = await base.startSearch(
          keyword,
          platforms,
          20,
          selectedPlatformLimits(limits, platforms),
          bypassCache,
          continueFrom
        );
        if (taskSeqRef.current !== seq) return; // 已发起更新的任务，本结果作废
        // 只有 POST 被后端接受后才写入身份与历史。
        dispatch({
          type: "search_accepted",
          jobId: job.job_id,
          keyword,
          platforms,
          nowIso: new Date().toISOString(),
        });
      } catch (err) {
        if (taskSeqRef.current !== seq) return;
        // POST 被拒绝：不写历史、快照保留；若为重试则记录失败摘要。
        dispatch({ type: "search_rejected", errorSummary: safeErrorSummary(err) });
      } finally {
        if (taskSeqRef.current === seq) taskInFlightRef.current = false;
      }
    },
    [busy, base, limits]
  );

  // ── 单平台重搜（"搜索范围"里每颗胶囊右边的 ⟳，以及平台卡片上的「重试」）──
  // 三个刷新动作的分工（用户明确区分过）：
  //   换一批（handleNextBatch）  → 不重合的新内容，往后叠加新批次；
  //   刷新结果（handleRefresh）  → 整组重搜，可以重合，替换当前视图；
  //   本函数                     → 单平台版的"重搜"：只搜这一个平台，
  //                                **替换它在当前批次里的内容，不开新批次**。
  // 因此后端带 replace_platforms：它会重置该平台的分页进度（从第 1 页重搜，
  // 也顺带解决"上一轮它一条没搜到、还被判定取尽"时点不动的死结），
  // 完成后原地替换；不属于换批，所以不会产生第 2 批。
  // 走 retry_* 事件：不写搜索历史、不改搜索范围勾选。
  const handleFetchPlatform = useCallback(
    async (platform: PlatformSlug) => {
      if (busy || taskInFlightRef.current) return;
      const current = state.display.jobResponse;
      const keyword = current?.keyword;
      if (!current || !keyword) return;
      const seq = ++taskSeqRef.current;
      taskInFlightRef.current = true;
      userStartedRef.current = true;
      dispatch({ type: "retry_start", platform });
      try {
        const job = await base.startSearch(
          keyword,
          [platform],
          20,
          selectedPlatformLimits(limits, [platform]),
          true,
          current.exploration ? current.job_id : undefined,
          true
        );
        if (taskSeqRef.current !== seq) return;
        dispatch({ type: "retry_accepted", jobId: job.job_id });
      } catch (err) {
        if (taskSeqRef.current !== seq) return;
        dispatch({ type: "search_rejected", errorSummary: safeErrorSummary(err) });
      } finally {
        if (taskSeqRef.current === seq) taskInFlightRef.current = false;
      }
    },
    [busy, base, state.display.jobResponse, limits]
  );

  // 用户明确点击"重新搜索"：整组绕过短缓存，获取平台新结果。
  const handleRefresh = useCallback(() => {
    const current = state.display.jobResponse;
    if (!current?.keyword || busy || taskInFlightRef.current) return;
    void handleFullSearch(
      current.keyword,
      Object.keys(current.exploration?.platforms ?? current.platforms) as PlatformSlug[],
      true,
    );
  }, [busy, handleFullSearch, state.display.jobResponse]);

  const handleNextBatch = useCallback(() => {
    const current = state.display.jobResponse;
    if (!current?.exploration || busy) return;
    const platforms = (Object.keys(current.exploration.platforms) as PlatformSlug[])
      .filter((p) => current.exploration!.platforms[p]?.has_more);
    if (platforms.length) void handleFullSearch(current.keyword, platforms, true, current.job_id);
  }, [busy, handleFullSearch, state.display.jobResponse]);

  // ── 任务观察：区分"用户发起"与"页面加载恢复" ────────────────────────
  // - 恢复任务（/jobs/current 或 sessionStorage 恢复）：首次观察到任意状态
  //   即派发 job_recovered 登记身份，之后终态按 job_terminal 正常提交。
  // - 用户发起的任务：终态直接派发 job_terminal（reducer 校验 activeJobId）。
  // - 非终态轮询响应派发 job_progress（Phase 2 渐进展示；reducer 同样做
  //   身份校验，旧任务/已应用终态的迟到进度会被拒绝）。
  // - reset 之后：不再登记恢复身份，迟到的终态因无身份被 reducer 拒绝。
  useEffect(() => {
    const resp = base.jobResponse;
    if (!resp) return;
    if (!userStartedRef.current && !recoveredOnceRef.current) {
      recoveredOnceRef.current = true;
      dispatch({ type: "job_recovered", jobId: resp.job_id });
    }
    if (TERMINAL_OVERALLS.has(resp.overall) && resp.completed_at) {
      dispatch({ type: "job_terminal", job: resp });
    } else {
      dispatch({ type: "job_progress", job: resp });
    }
  }, [base.jobResponse]);

  // ── 取消（Round 13）───────────────────────────────────────────────────
  // - 请求发出即派发 cancel_requested（不清快照、不提前显示"已取消"）。
  // - 请求失败派发 cancel_rejected（固定安全文案，绝不显示 axios 500 原文）：
  //   任务仍由轮询跟踪，可稍后再次取消；真实终态到达后由 reducer 清除提示。
  // - 展示保持（旧结果保留）；"已取消"提示只由真实终态 effect 驱动。
  const handleCancel = useCallback(async () => {
    dispatch({ type: "cancel_requested" });
    try {
      await base.cancel();
    } catch {
      dispatch({
        type: "cancel_rejected",
        safeMessage: "取消失败，当前搜索仍在继续，可稍后重试",
      });
    }
  }, [base]);

  // ── reset：清除当前任务与展示结果；不清历史与平台偏好 ─────────────────
  const handleReset = useCallback(() => {
    taskSeqRef.current += 1; // 使在途 POST 的结果作废
    taskInFlightRef.current = false;
    userStartedRef.current = false; // reset 后迟到终态不再视为恢复/用户任务
    dispatch({ type: "reset" });
    base.reset();
  }, [base]);

  // ── 历史操作（busy 时全部拒绝，避免与任务状态不一致） ────────────────
  const removeHistory = useCallback(
    (index: number) => {
      if (busy) return;
      dispatch({ type: "history_remove", index });
    },
    [busy]
  );

  const clearHistory = useCallback(() => {
    if (busy) return;
    dispatch({ type: "history_clear" });
  }, [busy]);

  const handleHistoryClick = useCallback(
    (item: SearchHistoryItem) => {
      void handleFullSearch(item.keyword, item.platforms);
    },
    [handleFullSearch]
  );

  // ── 平台偏好：每次选择变化立即 dispatch（effect 同步写回 localStorage） ──
  const updatePlatformPref = useCallback((platforms: PlatformSlug[]) => {
    dispatch({ type: "platform_pref_set", platforms });
  }, []);

  // Phase 2：渐进展示 selector —— UI 全部消费 presentation 输出。
  const presentation = selectSearchPresentation(state);

  return {
    // 展示层（UI 全部消费这一层）
    displayJobResponse: presentation.jobResponse,
    showingStaleSnapshot: presentation.showingStaleSnapshot,
    liveHint: presentation.liveHint,
    refreshing: state.display.refreshing,
    retryingPlatform: state.display.retryingPlatform,
    retryErrors: state.display.retryErrors,
    cancelledNotice: state.display.cancelledNotice,
    cancelRequested: state.display.cancelRequested,
    cancelError: state.display.cancelError,
    sortMode,
    setSortMode,
    history: state.history,
    removeHistory,
    clearHistory,
    handleHistoryClick,
    updatePlatformPref,
    platformPref: state.platformPref,
    // 动作
    handleFullSearch,
    handleRefresh,
    handleNextBatch,
    handleFetchPlatform,
    handleCancel,
    handleReset,
    // base 透传
    isCreating: base.isCreating,
    isCancelling: base.isCancelling,
    createError: base.createError,
    pollError: base.pollError,
    // Round 13: 取消请求的原始失败（仅用于 reset 取消 mutation 状态；
    // 用户可见文案一律来自 state.display.cancelError）。
    resetCancel: base.resetCancel,
    busy,
    searchedPlatforms: base.searchedPlatforms,
  };
}
