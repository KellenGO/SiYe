import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeft, Clock3, RotateCcw, Loader2, UserCog, RefreshCw } from "lucide-react";
import { SearchBar } from "./SearchBar";
import { PlatformStatus } from "./PlatformStatus";
import { ResultTabs } from "./ResultTabs";
import { TrendingBoard } from "@/components/trending/TrendingBoard";
import { useSearchExperience } from "@/hooks/useSearchExperience";
import { usePlatformLimits } from "@/hooks/usePlatformLimits";
import type { PlatformSlug } from "@/types/search";
import { PLATFORM_LABELS } from "@/types/search";
import { useBookmarks } from "@/hooks/useBookmarks";
import { TOOL_BUTTON } from "./ResultTools";
import { useHomePreferencesStore } from "@/store/homePreferencesStore";
import { useTranslation } from "react-i18next";

interface SearchPageProps {
  homeRequested?: boolean;
  onSearchStarted?: () => void;
  onNavigateAccounts?: () => void;
}

/** 这些状态都算"这个平台这次没搜到"：失败就自动取消勾选，并弹一次顶部提示。
 *  `empty`（0 条）也算 —— 用户明确要求"没搜到东西就算搜索失败"。 */
const FAILED_PLATFORM_STATUSES = ["failed", "login_required", "timed_out", "rate_limited", "empty"] as const;

/** 已提醒过的 "job:平台" 组合。
 *  必须是模块级而不是组件内 ref：切到别的页面再切回来时 SearchPage 会重新挂载，
 *  任务响应会被重新恢复 —— ref 挡不住重复弹窗，模块级集合挡得住（SPA 生命周期内有效）。 */
const handledFailureKeys = new Set<string>();

/** 弹窗里陈述的原因（不吓人、不复述 error_summary 原文）—— 平台自带安全原因时优先用它的。 */
const FAILURE_REASON_KEYS: Record<string, string> = {
  login_required: "search.reasonLoginRequired",
  failed: "search.reasonFailed",
  timed_out: "search.reasonTimedOut",
  rate_limited: "search.reasonRateLimited",
  empty: "search.reasonEmpty",
};

/**
 * 搜索页主体从搜索框开始，下面直接呈现状态与搜索结果。
 * 业务状态逻辑原样保留：快照 / 单平台重试合并 / 取消 /
 * 历史 / 任务恢复 —— 本组件只改布局与视觉。
 */
export function SearchPage({ homeRequested = false, onSearchStarted, onNavigateAccounts }: SearchPageProps) {
  const { t } = useTranslation();
  const library = useBookmarks();
  const homePreferences = useHomePreferencesStore();
  // 每个平台独立搜索数量（展示用；搜索请求由 useSearchExperience 读取）。
  const { limits } = usePlatformLimits();
  const {
    displayJobResponse: latestJobResponse,
    showingStaleSnapshot,
    liveHint,
    refreshing,
    retryingPlatform,
    retryErrors,
    cancelledNotice,
    cancelError,
    sortMode,
    setSortMode,
    history,
    removeHistory,
    clearHistory,
    updatePlatformPref,
    platformPref,
    handleFullSearch,
    handleRefresh,
    handleNextBatch,
    handleFetchPlatform,
    handleCancel,
    handleReset,
    isCancelling,
    createError,
    pollError,
    busy,
  } = useSearchExperience();
  const [selectedRound, setSelectedRound] = useState<number | null>(null);
  useEffect(() => setSelectedRound(null), [latestJobResponse?.job_id]);
  const exploration = latestJobResponse?.exploration;
  const previousBatch = exploration?.previous_batches.find((batch) => batch.number === selectedRound);
  const displayJobResponse = previousBatch && latestJobResponse
    ? { ...latestJobResponse, ...previousBatch, hydration_status: "completed" as const }
    : latestJobResponse;
  const hasMore = Object.values(exploration?.platforms ?? {}).some((info) => info.has_more);
  const fetchedAt = useMemo(() => Object.fromEntries(Object.entries(displayJobResponse?.platforms || {})
    .map(([platform, info]) => [platform, info.fetched_at ?? null])), [displayJobResponse]);

  // 受控输入：初始平台选择来自 localStorage 偏好（至少一个平台）。
  const [keyword, setKeyword] = useState("");
  const [keywordPickRequest, setKeywordPickRequest] = useState(0);
  const [selectedPlatforms, setSelectedPlatforms] = useState<Set<PlatformSlug>>(
    () => new Set(platformPref)
  );

  // 平台选择变化：同步受控控件 + 立即持久化偏好（两次点击间不丢）。
  const handlePlatformsChange = useCallback(
    (platforms: PlatformSlug[]) => {
      setSelectedPlatforms(new Set(platforms));
      updatePlatformPref(platforms);
    },
    [updatePlatformPref]
  );

  const handleGoAccounts = useCallback(() => {
    onNavigateAccounts?.();
  }, [onNavigateAccounts]);

  // 取消：只发起取消请求；"已取消"提示由 hook 观察真实 job 终态驱动。
  const handleCancelClick = useCallback(() => {
    void handleCancel();
  }, [handleCancel]);

  const handleFullSearchLocal = useCallback(
    (kw: string, platforms: PlatformSlug[]) => {
      onSearchStarted?.();
      void handleFullSearch(kw, platforms);
    },
    [handleFullSearch, onSearchStarted]
  );

  const handleResetLocal = useCallback(() => {
    handleReset();
  }, [handleReset]);

  // 历史记录和推荐词统一只填入关键词。递增请求即使关键词相同也会让
  // SearchBar 关闭浮层并重新聚焦；当前平台勾选和持久化偏好保持不变。
  const handleKeywordPick = useCallback((nextKeyword: string) => {
    setKeyword(nextKeyword);
    setKeywordPickRequest((current) => current + 1);
  }, []);

  // 点一条热搜：直接用当前勾选的平台发起搜索 —— 这才是这个卡片的用法
  // （"看各平台对同一条热搜的不同反应"）。
  // 一个平台都没勾选时只填入关键词，交给搜索框的"先勾选至少一个平台"提示。
  const handleTrendingPick = useCallback((word: string) => {
    const platforms = Array.from(selectedPlatforms);
    if (platforms.length === 0) {
      handleKeywordPick(word);
      return;
    }
    setKeyword(word);
    handleFullSearchLocal(word, platforms);
  }, [selectedPlatforms, handleKeywordPick, handleFullSearchLocal]);

  const isCancellingState = isCancelling;
  const hasError = !!createError || !!pollError;
  const isTerminal =
    displayJobResponse?.overall === "completed" ||
    displayJobResponse?.overall === "partial" ||
    displayJobResponse?.overall === "failed" ||
    displayJobResponse?.overall === "cancelled";

  // 搜索结束后，把这次失败的平台从勾选里去掉，并弹一次顶部提示。
  // 取消会写进持久化偏好（用户明确要求：勾选保持上次的选择），所以下次进来不会白搜一遍。
  // 同一 job 的同一平台只提醒一次：记录在模块级 handledFailureKeys 里，
  // 切页返回导致组件重新挂载也不会重复弹。
  useEffect(() => {
    const job = displayJobResponse;
    if (!job || !isTerminal) return;
    const failed = (Object.keys(job.platforms) as PlatformSlug[]).filter((platform) =>
      (FAILED_PLATFORM_STATUSES as readonly string[]).includes(job.platforms[platform].status)
    );
    const fresh = failed.filter((platform) => !handledFailureKeys.has(`${job.job_id}:${platform}`));
    if (fresh.length === 0) return;
    fresh.forEach((platform) => handledFailureKeys.add(`${job.job_id}:${platform}`));

    // 只取消"确实勾着"的失败平台；单独获取一个本来就没勾选的平台失败时，
    // 不该动搜索范围，提示也不该说"已自动取消勾选"。
    const wasChecked = fresh.filter((platform) => selectedPlatforms.has(platform));
    if (wasChecked.length > 0) {
      handlePlatformsChange(Array.from(selectedPlatforms).filter((platform) => !fresh.includes(platform)));
    }

    fresh.forEach((platform) => {
      const label = PLATFORM_LABELS[platform] || platform;
      const info = job.platforms[platform];
      // 平台自带的安全原因（例如抖音"疑似平台风控"）优先，比通用文案有信息量。
      const reason = info.error_summary
        || t(FAILURE_REASON_KEYS[info.status] ?? "search.reasonFailed");
      toast(t("search.platformFailedToast", { platform: label, reason }), {
        description: wasChecked.includes(platform)
          ? t("search.autoUnchecked", { platform: label })
          : t("search.failedNotChecked", { platform: label }),
        position: "top-center",
        duration: 6000,
        className: "siye-toast-info",
        action:
          job.platforms[platform].status === "login_required" && onNavigateAccounts
            ? { label: `${t("search.goAccounts")} →`, onClick: handleGoAccounts }
            : undefined,
      });
    });
  }, [displayJobResponse, isTerminal, selectedPlatforms, handlePlatformsChange, handleGoAccounts, onNavigateAccounts, t]);

  // Type-safe error extractor for axios errors (including 422 detail arrays)
  function getErrorMessage(err: unknown): string {
    const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
    if (e?.response?.status === 409) return "已有任务正在运行，请等待完成后再试。";
    if (e?.response?.status === 404) return "任务已失效，请重新搜索。";
    const detail = e?.response?.data?.detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { msg?: string }) => d.msg || "").join("; ") || "请求参数无效";
    }
    return (typeof detail === "string" ? detail : null) || e?.message || "请求失败，请重试。";
  }

  const showInitialIdle = !displayJobResponse && !busy && !hasError;
  const showInitialLoading = !displayJobResponse && busy && !hasError;
  // 首页视图与结果视图是**两页**，可以来回切：
  // - 「返回首页」只切视图，**不删结果、不动任务**；
  // - 首页在有结果时会显示一个「查看上次结果」入口切回来；
  // - 发起点搜索（busy）时自动切到结果视图，否则用户会看着首页等结果；
  // - 视图只在内存（刷新后：有结果就是结果页，保持原来的"恢复搜索状态"行为）。
  const [view, setView] = useState<"results" | "home">(
    latestJobResponse ? "results" : "home"
  );
  const prevHomeRequested = useRef(homeRequested);
  useEffect(() => {
    // 只在"首页被新点了一次"（false → true）时切过去；挂载时不抢，
    // 这样带结果恢复页面仍然停在结果页。
    if (homeRequested && !prevHomeRequested.current) setView("home");
    prevHomeRequested.current = homeRequested;
  }, [homeRequested]);
  useEffect(() => {
    if (busy) setView("results");
  }, [busy]);
  const isHome = view === "home";
  const goHome = useCallback(() => setView("home"), []);
  const showLastResults = useCallback(() => setView("results"), []);

  return (
    <div className={isHome ? `home ${homePreferences.mode === "min" ? "minimal" : ""}` : "preview-container search-shell"}>
      {isHome && <div className="hero"><div className="wordmark" aria-label="四野"><b>四野</b><svg className="swoosh" viewBox="0 0 120 12" aria-hidden="true"><defs><linearGradient id="wordmark-gradient"><stop stopColor="#6677fb"/><stop offset="1" stopColor="#29ddcc"/></linearGradient></defs><path d="M3 9Q60 0 117 9" stroke="url(#wordmark-gradient)" strokeWidth="3.5" fill="none" strokeLinecap="round"/></svg></div></div>}
      {/* 搜索区：结果页在搜索框**左侧**放一个主题色圆角「返回首页」按钮；首页只有搜索框。 */}
      <div className="search-zone">
        {!isHome && (
          <button type="button" className="btn primary home-back" onClick={goHome}>
            <ArrowLeft className="w-3.5 h-3.5" />
            {t("search.backToHome")}
          </button>
        )}
        {/* 搜索面板（含聚焦浮层：最近搜索 / 推荐搜索） */}
        <SearchBar
          home={isHome}
          keyword={keyword}
          onKeywordChange={setKeyword}
          selectedPlatforms={selectedPlatforms}
          onPlatformsChange={handlePlatformsChange}
          onSearch={handleFullSearchLocal}
          isSearching={busy}
          onCancel={handleCancelClick}
          isCancelling={isCancellingState}
          onReset={handleResetLocal}
          history={history}
          onKeywordPick={handleKeywordPick}
          keywordPickRequest={keywordPickRequest}
          onHistoryRemove={removeHistory}
          onHistoryClear={clearHistory}
          limits={limits}
          // 单独获取某个平台：只跑这一个平台并把结果并进现有结果（其它平台不动，
          // 平台是否勾选都不影响）。实现见 handleFetchPlatform。
          // **只在结果页出现**：首页那排平台只用来勾选搜索范围，不提供单独重搜。
          onPlatformFetch={!isHome && displayJobResponse ? handleFetchPlatform : undefined}
          fetchingPlatform={retryingPlatform}
        />
      </div>

      {/* 带着结果回到首页时的回程入口：结果没有被清掉，点这里回去。 */}
      {isHome && displayJobResponse && (
        <div className="home-back-row">
          <button type="button" className="btn ghost small" onClick={showLastResults}>
            {t("search.viewLastSearch", { count: displayJobResponse.results.length })}
          </button>
        </div>
      )}

      {isHome && homePreferences.mode === "full" && (homePreferences.history || homePreferences.trending) && (
        <div className="home-panels" style={!homePreferences.history || !homePreferences.trending ? { gridTemplateColumns: "1fr" } : undefined} aria-label="首页快捷内容">
          {homePreferences.history && <section className="panel">
            <div className="panel-heading"><h2>最近搜索</h2>{history.length ? <button type="button" className="text-link" onClick={clearHistory}>清空</button> : <Clock3 />}</div>
            {history.length > 0 ? (
              <div className="words">
                {history.slice(0, 10).map((item) => (
                  <button key={`${item.keyword}-${item.searchedAt}`} type="button" onClick={() => handleKeywordPick(item.keyword)}>
                    {item.keyword}
                  </button>
                ))}
              </div>
            ) : (
              <p className="secondary text-[13px]">你的探索，从第一次搜索开始。</p>
            )}
            <p className="panel-footnote">{history.length ? "从上次的好奇，继续探索。" : "搜索过的关键词会出现在这里。"}</p>
          </section>}
          {/* 热搜卡片取代了原来的「最近搜到」：那一块的内容在历史与搜索结果里都能看到，
              而"各平台在热什么"是这里唯一能提供的新信息。 */}
          {homePreferences.trending && <TrendingBoard onPick={handleTrendingPick} />}
        </div>
      )}
      {isHome && homePreferences.mode === "full" && <p className="home-note">小红书、抖音、B站、知乎 · 一次搜索，几种视角。</p>}

      {!isHome && <div className="search-headline">
        <h1>{displayJobResponse?.keyword || keyword || "搜索结果"}</h1>
        <span>{busy ? "正在跨平台搜索" : "相关内容"}</span>
      </div>}
      {!isHome && <div>
      {/* 平台搜索状态（统一浅色状态卡） */}
      <PlatformStatus
        response={displayJobResponse ?? undefined}
        onRetry={handleFetchPlatform}
        retryingPlatform={retryingPlatform}
        retryDisabled={busy || !!previousBatch}
      />

      {/* Cancelling */}
      {isCancellingState && (
        <div className="mt-4 flex items-center gap-2 px-4 py-2.5 rounded-xl border border-warn/40 bg-warn-soft text-warn text-sm w-fit">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t("search.cancelling")}
        </div>
      )}

      {/* 取消失败：固定安全文案，绝不显示 axios 500 原文；
          任务仍在运行（轮询继续），提供"再次取消"，不清除旧结果，
          不错误显示"已取消"。 */}
      {cancelError && (
        <div className="mt-4 flex items-center gap-3 px-4 py-3 rounded-xl border border-warn/50 bg-warn-soft text-warn text-sm max-w-2xl w-full">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">{cancelError}</span>
          <button
            onClick={handleCancelClick}
            disabled={isCancellingState}
            className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-lg border border-warn/50 hover:bg-warn/10 text-xs transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Loader2 className="w-3 h-3" />{t("search.cancelAgain")}
          </button>
        </div>
      )}

      {/* Cancelled（取消保留旧结果，提示由真实终态驱动） */}
      {cancelledNotice && !busy && !createError && !cancelError && (
        <div className="mt-6 text-center">
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-warn/40 bg-warn-soft text-warn text-sm">
            ⏹ {t("search.cancelledNotice")}
          </div>
        </div>
      )}

      {/* Error */}
      {(createError || pollError) && (
        <div className="mt-4 flex items-center gap-3 px-4 py-3 rounded-xl border border-danger/40 bg-danger-soft text-danger text-sm max-w-2xl w-full">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">{getErrorMessage(createError || pollError)}</span>
          <button onClick={handleReset} className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-lg border border-danger/40 hover:bg-danger/10 text-xs transition-all">
            <RotateCcw className="w-3 h-3" />重试
          </button>
        </div>
      )}

      {/* Initial idle */}
      {showInitialIdle && !isHome && (
        <div className="mt-16 text-center">
          <p className="text-sm text-cyber-text-muted">
            {t("search.enterKeyword")}
          </p>
        </div>
      )}

      {/* Initial loading（首次搜索，无旧结果可展示） */}
      {showInitialLoading && (
        <div className="mt-16 text-center">
          <div className="inline-block animate-dsh-spin rounded-full h-8 w-8 border-2 border-brand border-t-transparent" />
          <p className="mt-4 text-sm text-cyber-text-muted">
            {t("search.searching")}
          </p>
        </div>
      )}

      {/* Failed (all platforms) */}
      {displayJobResponse && displayJobResponse.overall === "failed" && (
        <div className="mt-6 w-full max-w-2xl">
          <div className="text-center mb-3 px-4 py-2 rounded-xl border border-danger/40 bg-danger-soft text-danger text-sm">
            <AlertTriangle className="w-4 h-4 inline mr-2" />{t("search.allFailed")}
          </div>
          {Object.entries(displayJobResponse.platforms).map(([p, info]) => (
            <div key={p} className="flex items-center justify-between px-3.5 py-2.5 mb-1.5 rounded-lg bg-cyber-bg-secondary border border-cyber-border-subtle text-sm">
              <span className="text-cyber-text-secondary">
                {PLATFORM_LABELS[p as PlatformSlug] || p}: {info.error_summary || info.status}
              </span>
              {info.status === "login_required" && (
                <button onClick={handleGoAccounts}
                  className="px-3 py-1 rounded-lg bg-brand-soft border border-brand/40 text-brand-strong hover:bg-brand/10 text-xs transition-all">
                  <UserCog className="w-3 h-3 inline mr-1" />{t("search.goAccounts")}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Partial */}
      {displayJobResponse && displayJobResponse.overall === "partial" && (
        <div className="mt-4 w-full max-w-2xl px-3.5 py-2 rounded-xl border border-warn/40 bg-warn-soft text-warn text-xs">
          ⚠ {t("search.partialFailed")}: {Object.entries(displayJobResponse.platforms)
            .filter(([, i]) => !["succeeded", "empty"].includes(i.status))
            .map(([p, i]) => `${PLATFORM_LABELS[p as PlatformSlug] || p}(${i.error_summary || i.status})`)
            .join(", ")}
        </div>
      )}

      {/* Results（全宽布局，无右侧栏） */}
      {displayJobResponse && (displayJobResponse.overall !== "failed" || exploration) && (
        <div className={`w-full mt-4 transition-opacity ${refreshing || showingStaleSnapshot ? "opacity-60" : "opacity-100"}`}>
          {(liveHint || refreshing) && (
            <div className="mb-3 flex items-center gap-2 px-3.5 py-2 rounded-xl border border-brand/40 bg-brand-soft text-brand-strong text-xs w-fit">
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              {liveHint ?? t("search.updating")}
            </div>
          )}

          <div className="result-summary-line">
            <p>
              <span>{displayJobResponse.results.length} 条内容</span>
              {isTerminal && displayJobResponse.completed_at && (
                <span className="ml-3">{t("search.completedAt")} {new Date(displayJobResponse.completed_at).toLocaleTimeString("zh-CN")}</span>
              )}
              {!isTerminal && <span className="ml-3 text-brand-strong animate-pulse">{t("search.searchingLive")}</span>}
            </p>
            {isTerminal && <div className="button-row">
              {exploration && <button type="button" onClick={handleNextBatch}
                disabled={busy || !hasMore} className={TOOL_BUTTON}>
                <RefreshCw className="w-3.5 h-3.5" />换一批
              </button>}
              <details key={latestJobResponse?.job_id} className="relative text-xs">
                <summary className={`${TOOL_BUTTON} cursor-pointer`}>更多</summary>
                <div className="absolute right-0 top-full z-20 mt-2 w-52 rounded-xl border border-cyber-border-subtle bg-cyber-bg-secondary p-3 shadow-lg space-y-3">
                  <button type="button" onClick={handleRefresh} disabled={busy} className={TOOL_BUTTON}
                    aria-label="刷新结果">刷新结果</button>
                  <p className="text-cyber-text-muted">从头搜索，开始新的轮次记录。</p>
                  {exploration && <p className="text-cyber-text-muted">本轮列表请求 {exploration.page_requests} 次，过滤重复来源 {exploration.duplicates} 条。每个主题最多 20 轮。</p>}
                  {exploration && <label className="block text-cyber-text-secondary">查看轮次
                    <select aria-label="查看轮次" disabled={busy} value={selectedRound ?? exploration.round}
                      onChange={(event) => setSelectedRound(Number(event.target.value) === exploration.round ? null : Number(event.target.value))}
                      className="mt-1 w-full rounded-lg border border-cyber-border-subtle bg-cyber-bg-primary p-2">
                      {exploration.previous_batches.map((batch) => <option key={batch.number} value={batch.number}>第 {batch.number} 批（{batch.results.length} 条）</option>)}
                      <option value={exploration.round}>第 {exploration.round} 批（当前）</option>
                    </select>
                  </label>}
                </div>
              </details>
            </div>}
          </div>
          {exploration && <div className="mb-3 text-xs text-cyber-text-muted space-y-1" aria-live="polite">
            <p>第 {previousBatch?.number ?? exploration.round} 批 · {previousBatch ? `${previousBatch.results.length} 条内容` : `新增 ${exploration.new_contents} 条内容`}
              {Object.entries(exploration.platforms).map(([p, info]) => <span className="ml-3" key={p}>{PLATFORM_LABELS[p as PlatformSlug]}累计 {info.collected}/{exploration.max_per_platform}</span>)}
            </p>
            {!hasMore && <p>已到本次探索上限（最多 20 轮）或平台暂无更多结果，可在“更多”中刷新结果。</p>}
            {!previousBatch && exploration.new_contents === 0 && hasMore && <p>本轮没有新的独立内容；重复内容已过滤，新平台版本已补充到之前的卡片。可回看或稍后再换一批。</p>}
          </div>}

          {/* 单平台重试失败提示（保留旧结果，仅显示安全摘要） */}
          {Object.entries(retryErrors).map(([platform, message]) => (
            <div key={platform} className="mb-2 px-3.5 py-2 rounded-xl border border-danger/30 bg-danger-soft text-danger text-xs">
              {t("search.retryFailed")}：{PLATFORM_LABELS[platform as PlatformSlug] || platform} {message}
            </div>
          ))}

          {/* 失败平台不再挂常驻黄卡：改为顶部弹窗（会自动消失）+ 自动取消勾选，
              平台自身的失败状态由 PlatformStatus 的状态块呈现。 */}

          {displayJobResponse.hydration_status === "running" && (
            <p role="status" className="mt-3 text-xs text-cyber-text-muted">正在补充指标和简介，已有结果可以先查看。</p>
          )}
          <ResultTabs
            results={displayJobResponse.results}
            keyword={displayJobResponse.keyword}
            overall={displayJobResponse.overall}
            jobId={displayJobResponse.job_id}
            hydrationStatus={displayJobResponse.hydration_status}
            platforms={Object.keys(displayJobResponse.platforms) as PlatformSlug[]}
            sortMode={sortMode}
            onSortModeChange={setSortMode}
            library={library}
            fetchedAt={fetchedAt}
            pageSize={100}
          />
        </div>
      )}
      </div>}
    </div>
  );
}
