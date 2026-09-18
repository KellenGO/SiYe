import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, RefreshCw, Trash2, ExternalLink, Plug, ShieldCheck, ChevronDown, ChevronRight, Minus, Plus, Palette, SlidersHorizontal, UserRound, QrCode } from "lucide-react";
import { PLATFORM_LABELS, PLATFORM_COLORS } from "@/types/search";
import type { PlatformSlug } from "@/types/search";
import { invalidateAccounts, useAccounts } from "@/hooks/useAccounts";
import { usePlatformLimits } from "@/hooks/usePlatformLimits";
import {
  accountSearchVerdict,
  accountActionHint,
  accountCardStatusLabel,
  diagnosticAccountStateLabel,
  diagnosticSearchModeLabel,
  accountUsageHint,
  accountOperationLabel,
  summarizeAccounts,
  type SearchVerdictKind,
} from "@/lib/accounts";
import { MAX_PLATFORM_LIMIT, MIN_PLATFORM_LIMIT, PLATFORM_ORDER, parsePlatformLimitInput } from "@/lib/platformLimits";
import {
  BULK_SYNC_PLATFORM_ORDER,
  buildBulkBlockedMessage,
  buildBulkSummaryMessage,
  createBulkSyncGuard,
  resolveBulkTargets,
  runBulkSync,
  summarizeBulkOutcomes,
  type BulkSyncBlockReason,
  type SyncAttemptOutcome,
} from "@/lib/accountBulkSync";
import {
  ACCOUNTS_API_BASE,
  SYNC_RESPONSE_TIMEOUT_MS,
  detectExtension,
  requestPlatformSync,
  type SyncResult,
} from "@/lib/extensionSync";
import {
  SCAN_LOGIN_API_BASE,
  SCAN_LOGIN_POLL_INTERVAL_MS,
  clearAllScanLogins,
  clearScanLoginActive,
  isScanLoginActive,
  isScanLoginTerminal,
  markScanLoginActive,
  pendingScanLoginJob,
  scanLoginActionLabel,
  scanLoginErrorMessage,
  scanLoginView,
  type ScanLoginJob,
  type ScanLoginTone,
} from "@/lib/scanLogin";
import type { SettingsSection } from "@/components/layout/Header";
import { useThemeStore } from "@/store/themeStore";
import { useHomePreferencesStore } from "@/store/homePreferencesStore";

const API_BASE = ACCOUNTS_API_BASE;

/** 扫码登录状态行的文字色调（与 VERDICT_BADGE 的语义保持一致）。 */
const LOGIN_TONE_TEXT: Record<ScanLoginTone, string> = {
  idle: "text-cyber-text-muted",
  active: "text-brand-strong",
  ok: "text-[#3d7d60]",
  bad: "text-danger",
};

const SYNC_STAGE_TEXT: Record<string, string> = {
  profile_import: "导入 Cookie",
  verification: "验证会话",
  completed: "已完成",
};

const PLATFORM_LOGIN_URLS: Record<string, string> = {
  xhs: "https://www.xiaohongshu.com",
  douyin: "https://www.douyin.com",
  bilibili: "https://www.bilibili.com",
  zhihu: "https://www.zhihu.com",
};

/** 与后端 LOGIN_MARKER_NAMES 一致的白名单标记名（仅用于展示布尔值）。 */
const LOGIN_MARKERS: Record<string, string[]> = {
  xhs: ["web_session"],
  douyin: ["LOGIN_STATUS", "sessionid", "sessionid_ss"],
  bilibili: ["SESSDATA", "DedeUserID"],
  zhihu: ["z_c0", "d_c0"],
};

const BACKEND_TEXT: Record<string, string> = {
  chrome: "Chrome",
  edge: "Edge",
  playwright_chromium: "Playwright Chromium",
  custom: "自定义浏览器",
};

/** 卡片主结论徽章（可用 / 不可用 / 验证中），只用现有 token 色。 */
const VERDICT_BADGE: Record<SearchVerdictKind, string> = {
  available: "bg-ok-soft text-ok border-ok/40",
  unavailable: "bg-danger-soft text-danger border-danger/40",
  pending: "bg-cyber-bg-tertiary text-cyber-text-muted border-cyber-border-subtle",
};

const VERDICT_LINE: Record<SearchVerdictKind, string> = {
  available: "text-ok",
  unavailable: "text-danger",
  pending: "text-warn",
};

const VERDICT_LABEL: Record<SearchVerdictKind, string> = {
  available: "可用",
  unavailable: "不可用",
  pending: "验证中",
};

/**
 * 单个平台的搜索数量设置行（Round 15）：
 * - 减号/加号不越过 1–40；
 * - 可直接编辑数字；输入框暂时为空时不立即变成 1；
 * - blur 或 Enter 校正：小于 1 → 1、大于 40 → 40、小数取整、非法/空 → 恢复上次有效值；
 * - 修改一个平台不影响其他平台。
 */
function LimitRow({
  platform,
  value,
  onChange,
}: {
  platform: PlatformSlug;
  value: number;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const lastValidRef = useRef(value);

  useEffect(() => {
    setDraft(String(value));
    lastValidRef.current = value;
  }, [value]);

  const commit = (raw: string) => {
    const parsed = parsePlatformLimitInput(raw);
    if (parsed === null) {
      // 非法或空值 → 恢复该平台上一次有效值
      setDraft(String(lastValidRef.current));
      return;
    }
    lastValidRef.current = parsed;
    setDraft(String(parsed));
    onChange(parsed);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    setDraft(raw);
    const parsed = parsePlatformLimitInput(raw);
    if (parsed !== null) {
      // 合法输入立即生效（自动保存）；空/非法等待 blur/Enter 校正
      lastValidRef.current = parsed;
      onChange(parsed);
    }
  };

  const color = PLATFORM_COLORS[platform] || "#4ca4dc";

  return (
    <div className="setting-row">
      <div>
        <div className="setting-label"><i className="pd" style={{ backgroundColor: color }} />{PLATFORM_LABELS[platform]}</div>
        <p className="setting-desc">每轮获取 {MIN_PLATFORM_LIMIT}–{MAX_PLATFORM_LIMIT} 条内容</p>
      </div>
      <div className="stepper">
        <button
          type="button"
          aria-label={`减少${PLATFORM_LABELS[platform]}数量`}
          onClick={() => { const next = value - 1; if (next >= MIN_PLATFORM_LIMIT) onChange(next); }}
          disabled={value <= MIN_PLATFORM_LIMIT}
          className="stepper-button"
        >
          <Minus className="w-4 h-4" />
        </button>
        <input
          type="text"
          inputMode="numeric"
          value={draft}
          onChange={handleChange}
          onBlur={() => commit(draft)}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
          aria-label={`${PLATFORM_LABELS[platform]}搜索数量`}
          className="stepper-input"
        />
        <button
          type="button"
          aria-label={`增加${PLATFORM_LABELS[platform]}数量`}
          onClick={() => { const next = value + 1; if (next <= MAX_PLATFORM_LIMIT) onChange(next); }}
          disabled={value >= MAX_PLATFORM_LIMIT}
          className="stepper-button"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

interface AccountsPageProps {
  activeSection: SettingsSection;
  onSectionChange: (section: SettingsSection) => void;
  onNavigateSearch?: () => void;
  onNavigateHelp?: () => void;
}

export function AccountsPage({ activeSection, onSectionChange, onNavigateSearch, onNavigateHelp }: AccountsPageProps) {
  const { accounts, apiRunning, browserAvailable } = useAccounts();
  // 账号 sync/verify/delete 完成后立即刷新共享缓存。
  const queryClient = useQueryClient();
  // One queue on page entry. The server deduplicates recent checks; GET polling
  // only reads local evidence and never causes platform traffic.
  useEffect(() => {
    if (activeSection !== "accounts" || apiRunning !== true) return;
    let disposed = false;
    const queue = [...PLATFORM_ORDER];
    const run = async () => {
      while (!disposed && queue.length) {
        const platform = queue.shift()!;
        try { await axios.post(`${API_BASE}/${platform}/verify?reuse_recent=true`); }
        catch { /* Busy/limited checks are not retried automatically. */ }
        finally { invalidateAccounts(queryClient); }
      }
    };
    void run(); void run();
    return () => { disposed = true; };
  }, [activeSection, apiRunning, queryClient]);
  // 每个平台独立搜索数量（localStorage 持久化，修改即保存）。
  const { limits, setLimit, resetAll } = usePlatformLimits();
  const { theme, setTheme } = useThemeStore();
  const homePreferences = useHomePreferencesStore();
  const [extensionState, setExtensionState] = useState<
    "checking" | "connected" | "outdated" | "not-installed" | "unknown"
  >("checking");
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [lastDiag, setLastDiag] = useState<Record<string, SyncResult>>({});
  /** 诊断信息默认折叠，用户点击"查看诊断"再展开。 */
  const [openDiag, setOpenDiag] = useState<Record<string, boolean>>({});
  // ── 应用自带扫码登录────────────────────────────────
  // 面板按平台独立展开：旧实现用单个布尔量，展开一个平台会串到所有卡片。
  const [loginOpen, setLoginOpen] = useState<Record<string, boolean>>({});
  /** 每个平台最近一次登录任务（不随折叠/切换卡片丢失，用户回头能看到结果）。 */
  const [loginJobs, setLoginJobs] = useState<Record<string, ScanLoginJob>>({});
  // 轮询需要读到最新任务但不能成为 effect 依赖：用 ref 镜像 + 已汇报集合。
  const loginJobsRef = useRef<Record<string, ScanLoginJob>>({});
  loginJobsRef.current = loginJobs;
  /** 已汇报过终态的任务（同一次轮询可能重复读到终态，toast 只出一次）。 */
  const reportedLoginJobsRef = useRef<Set<string>>(new Set());
  const [extensionVersion, setExtensionVersion] = useState("");

  // ── 扩展检测（content script ping/pong，协议实现在 lib/extensionSync）──
  useEffect(() => {
    let cancelled = false;
    void detectExtension().then((probe) => {
      if (cancelled) return;
      setExtensionVersion(probe.version);
      // 协议版本 2 只是兼容门；实际扩展版本必须 ≥ 1.1.3 —— 否则可能仍是
      // 旧脚本（协议同为 2，但 ready/pong 不带 extension_version，
      // 后端已引入的 login_marker_presence 等字段不会被正确转发）。
      setExtensionState(probe.state);
    });
    return () => { cancelled = true; };
  }, []);

  const setBusyPlatform = (platform: string | null, label = "") => {
    setBusy((prev) => {
      const next = { ...prev };
      if (platform) next[platform] = label;
      else Object.keys(next).forEach((k) => delete next[k]);
      return next;
    });
  };

  // 同步/验证期间保持 busy 状态，直到账号轮询看到终态才解除
  // （后端"正在导入/正在验证"可能持续数十秒，绝不能提前解除）。
  useEffect(() => {
    if (!accounts) return;
    const terminal = new Set(["disconnected", "unverified", "connected", "expired", "failed", "unavailable"]);
    setBusy((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const p of Object.keys(next)) {
        const a = accounts.find((x) => x.platform === p);
        if (a && terminal.has(a.status)) { delete next[p]; changed = true; }
      }
      return changed ? next : prev;
    });
  }, [accounts]);

  // ── 打开官方登录页（当前浏览器，绝不启动 Playwright）────────────────
  const openOfficial = useCallback((platform: string) => {
    window.open(PLATFORM_LOGIN_URLS[platform] || "https://www.xiaohongshu.com", "_blank");
  }, []);

  // ── 同步当前浏览器登录状态（返回结构化结果，支持静默） ──
  // 单平台按钮调用 silent=false（保留现有 toast）；一键同步调用
  // silent=true（避免连续四组单平台 toast，最终只出一条汇总 toast）。
  const syncAccount = useCallback(
    async (platform: PlatformSlug, opts?: { silent?: boolean }): Promise<SyncAttemptOutcome> => {
      const silent = opts?.silent === true;
      // 安全失败结果（不含 Cookie/ticket/header/响应体/traceback）。
      const fail = (outcome: {
        safeErrorCode?: string; safeMessage?: string; blockQueue?: BulkSyncBlockReason;
      }): SyncAttemptOutcome => {
        if (!silent && outcome.safeMessage) toast.error(outcome.safeMessage);
        return {
          platform,
          kind: "failed",
          success: false,
          verified: false,
          safeErrorCode: outcome.safeErrorCode,
          safeMessage: outcome.safeMessage,
          blockQueue: outcome.blockQueue,
        };
      };

      if (extensionState === "not-installed") {
        return fail({
          safeErrorCode: "extension_not_installed",
          safeMessage: "未检测到浏览器扩展。可以改用该平台的「扫码登录」，或安装扩展后刷新本页再试。",
        });
      }
      if (extensionState === "outdated") {
        // 禁止继续同步：旧脚本（1.1.2 及更早）协议同为 v2，但可能缺少
        // 后端需要的字段，继续同步会得到不可靠的诊断结果。
        return fail({
          safeErrorCode: "extension_outdated",
          safeMessage: "扩展版本过旧，请在 edge://extensions 点击\"重新加载\"后刷新本页再同步；也可以改用「扫码登录」。",
        });
      }
      setBusyPlatform(platform, "syncing");
      try {
        // 申请一次性票据 → 请求扩展读取并上报当前浏览器的平台会话
        // （协议往返在 lib/extensionSync，与自动同步共用同一份实现）。
        const result = await requestPlatformSync(platform);

        if (result === null) {
          setBusyPlatform(platform, "");
          const msg = `扩展 ${Math.round(SYNC_RESPONSE_TIMEOUT_MS / 1000)} 秒未响应。`
            + "请确认：1) 扩展已加载并启用；2) 已刷新本页（扩展注入后需刷新一次）；"
            + "3) 后端验证会话最长约 30 秒，若仍在验证可稍等后查看账号卡片诊断。"
            + "也可以改用该平台的「扫码登录」。";
          if (!silent) toast.error(msg);
          return {
            platform, kind: "failed", success: false, verified: false,
            safeErrorCode: "extension_no_response", safeMessage: msg,
          };
        }
        setLastDiag((prev) => ({ ...prev, [platform]: result }));
        // 同步不成功：显示后端/扩展返回的安全错误（可能是 Cookie 未读取到、
        // 格式不兼容、会话导入失败或正在搜索不能同步等）。
        if (!result.success) {
          setBusyPlatform(platform, "");
          const counts = typeof result.received_cookie_count === "number"
            ? `（读取 ${result.received_cookie_count} 条）` : "";
          const code = result.safe_error_code;
          const isSearchConflict = code === "search_in_progress";
          const msg = `同步失败${counts}：${result.safe_message || code || "未知错误"}`;
          if (!silent) toast.error(msg);
          return {
            platform, kind: "failed", success: false, verified: false,
            safeErrorCode: code || undefined,
            safeMessage: result.safe_message || undefined,
            ...(isSearchConflict ? { blockQueue: "search_in_progress" as const } : {}),
          };
        }
        // success toast 只允许在真实验证通过时显示 ——
        // status==="connected" && verified===true && 无安全错误码。
        // （unavailable 等场景绝不显示"同步成功且登录验证通过"。）
        if (result.verified && result.status === "connected" && !result.safe_error_code) {
          setBusyPlatform(platform, "");
          const counts = typeof result.received_cookie_count === "number"
            ? `（读取 ${result.received_cookie_count} 条 / 接受 ${result.accepted_cookie_count ?? "?"} 条）` : "";
          if (!silent) toast.success(`同步成功且登录验证通过。${counts}`);
          return { platform, kind: "verified", success: true, verified: true };
        }
        const importedCounts = typeof result.received_cookie_count === "number"
          ? `（读取 ${result.received_cookie_count} 条 / 接受 ${result.accepted_cookie_count ?? "?"} 条）` : "";
        // 有界验证超时，验证仍在后台进行：busy 保持 "verifying"，直到账号
        // 轮询看到终态（见上面的 busy 清理 effect）。
        if (result.status === "verifying") {
          setBusyPlatform(platform, "verifying");
          const msg = `会话已导入，仍在后台验证 ${importedCounts}。可在本卡片查看诊断，或稍后点击"重新验证"确认结果。`;
          if (!silent) toast.info(msg);
          return { platform, kind: "verifying", success: true, verified: false, safeMessage: msg };
        }
        // 验证暂不可用（网络/超时/403 风控/导航失败）必须优先
        // 显示后端 safe_message，绝不落入下方"尚未确认账号登录"的提示
        // （那会错误地声称明确未登录）。
        if (result.status === "unavailable" || result.safe_error_code === "login_verification_unavailable") {
          setBusyPlatform(platform, "");
          const msg = result.safe_message || "当前无法验证登录状态，仍可尝试搜索或稍后重新验证";
          if (!silent) toast.warning(msg);
          return {
            platform, kind: "unavailable", success: true, verified: false,
            safeErrorCode: result.safe_error_code || "login_verification_unavailable",
            safeMessage: msg,
          };
        }
        // 明确未登录（expired / unverified）：已导入但未确认登录，这不是
        // 失败 —— 公开搜索仍可尝试。
        setBusyPlatform(platform, "");
        const msg = `会话已导入，但尚未确认账号登录。你仍可以尝试搜索；如搜索需要登录，再重新同步。${importedCounts}`;
        if (!silent) toast.info(msg);
        return {
          platform, kind: "imported", success: true, verified: false,
          safeErrorCode: result.safe_error_code || "login_not_verified",
          safeMessage: msg,
        };
      } catch (e) {
        setBusyPlatform(platform, "");
        // 解析后端结构化错误（safe_message / detail），而不是统一显示"API 可能未启动"
        const resp = (e as { response?: { status?: number; data?: { safe_message?: string; detail?: string; safe_error_code?: string } } }).response;
        const status = resp?.status;
        const code = resp?.data?.safe_error_code;
        const msg = resp?.data?.safe_message || resp?.data?.detail;
        const finalMsg = msg
          ? `同步请求失败：${msg}`
          : "同步请求失败，请确认本地 API 已启动后刷新页面重试。";
        if (!silent) toast.error(finalMsg);
        return {
          platform, kind: "failed", success: false, verified: false,
          safeErrorCode: code || (status === 409 ? "conflict" : "sync_request_failed"),
          safeMessage: msg || "同步请求失败，请确认本地 API 已启动",
          ...(status === 409 && code === "search_in_progress"
            ? { blockQueue: "search_in_progress" as const } : {}),
        };
      } finally {
        // 同步完成（成功/失败/后台验证中）后立即刷新账号缓存。
        invalidateAccounts(queryClient);
      }
    },
    [extensionState, queryClient]
  );

  // ── 重新验证 ─────────────────────────────────────────────────────────
  const verifyAccount = useCallback(async (platform: string) => {
    setBusyPlatform(platform, "verifying");
    try {
      await axios.post(`${API_BASE}/${platform}/verify`);
      setTimeout(() => setBusyPlatform(platform, ""), 2000);
    } catch (e) {
      setBusyPlatform(platform, "");
      const resp = (e as { response?: { status?: number; data?: { safe_message?: string; detail?: string } } }).response;
      const msg = resp?.data?.safe_message || resp?.data?.detail;
      toast.error(msg
        ? `验证请求失败：${msg}`
        : "验证请求失败，请确认本地 API 已启动后刷新页面重试。");
    } finally {
      // 验证完成（含后台验证进行中）后立即刷新账号缓存。
      invalidateAccounts(queryClient);
    }
  }, [queryClient]);

  // ── 清除登录状态（二次确认；破坏性操作保留原生 confirm 双重确认） ──
  const deleteSession = useCallback(async (platform: string) => {
    const name = PLATFORM_LABELS[platform as keyof typeof PLATFORM_LABELS] || platform;
    if (!window.confirm(`确定清除 ${name} 的登录状态吗？\n\n此操作会删除本地后台登录会话，之后需要重新同步。此操作不可撤销。`)) {
      return;
    }
    if (!window.confirm(`再次确认：将删除 ${name} 的后台登录数据（仅本地 browser_data 目录内），确定继续？`)) {
      return;
    }
    setBusyPlatform(platform, "deleting");
    try {
      await axios.delete(`${API_BASE}/${platform}/session`);
      toast.success(`已清除 ${name} 的登录状态。`);
    } catch (e) {
      const resp = (e as { response?: { status?: number; data?: { safe_message?: string; detail?: string } } }).response;
      const msg = resp?.data?.safe_message || resp?.data?.detail;
      toast.error(msg ? `清除失败：${msg}` : "清除失败，请确认本地 API 已启动。");
    } finally {
      setBusyPlatform(platform, "");
      // 删除完成后立即刷新账号缓存。
      invalidateAccounts(queryClient);
    }
  }, [queryClient]);

  // ── 一键同步四个平台（/ ）────────────────────────
  // 固定顺序 xhs → douyin → bilibili → zhihu，最大并发 2（生产模块
  // runBulkSync 编排）；复用 syncAccount（silent=true，不弹单平台 toast）。
  const [bulkSyncing, setBulkSyncing] = useState(false);
  const [bulkActive, setBulkActive] = useState<PlatformSlug[]>([]);
  const [bulkCompleted, setBulkCompleted] = useState(0);
  /** 本次队列的平台总数（只同步部分平台时不能用固定 4）。 */
  const [bulkTotal, setBulkTotal] = useState(BULK_SYNC_PLATFORM_ORDER.length);
  // 双击 guard：同步 ref（不能只依赖异步 React state）。
  const bulkGuardRef = useRef<ReturnType<typeof createBulkSyncGuard> | null>(null);
  if (bulkGuardRef.current === null) bulkGuardRef.current = createBulkSyncGuard();

  /**
   * 手动"一键同步四个平台"队列。
   * 打开程序时的自动同步由应用根部的 useAutoAccountSync 负责（），
   * 本页只保留用户主动触发的这一条路径。
   */
  const runSyncQueue = useCallback(async (
    platforms: readonly PlatformSlug[] | undefined,
  ) => {
    const guard = bulkGuardRef.current;
    if (!guard || !guard.tryStart()) return; // 双击/并发 guard：拒绝第二套队列
    // 明确全局阻断（扩展未连接/过旧、API 不可用）→ 不启动队列，直接提示。
    const immediateBlock: BulkSyncBlockReason | null =
      extensionState === "outdated"
        ? "extension_outdated"
        : extensionState !== "connected"
          ? "extension_not_connected"
          : apiRunning === false
            ? "api_unavailable"
            : null;
    if (immediateBlock) {
      toast.warning(buildBulkBlockedMessage(immediateBlock));
      guard.finish();
      return;
    }
    setBulkSyncing(true);
    setBulkActive([]);
    setBulkCompleted(0);
    setBulkTotal(resolveBulkTargets(platforms).length);
    try {
      const result = await runBulkSync({
        platforms,
        // 单平台 toast 关闭（silent），最终只出一条汇总 toast。
        syncOne: (platform) => syncAccount(platform, { silent: true }),
        checkBlock: () => (
          extensionState !== "connected"
            ? (extensionState === "outdated" ? "extension_outdated" : "extension_not_connected")
            : apiRunning === false
              ? "api_unavailable"
              : null
        ),
        onProgress: (p) => {
          setBulkActive(p.activePlatforms);
          setBulkCompleted(p.completedCount);
        },
      });
      // 汇总：全局阻断 → 一条总体提示；否则 → 一条汇总 toast。
      if (result.blocked && result.blockReason) {
        toast.warning(buildBulkBlockedMessage(result.blockReason));
      } else {
        const summary = buildBulkSummaryMessage(summarizeBulkOutcomes(result.outcomes));
        if (summary.tone === "success") {
          toast.success(summary.title);
        } else if (summary.tone === "warning") {
          toast.warning(summary.title, { description: summary.description });
        } else {
          toast.info(summary.title);
        }
      }
    } finally {
      setBulkSyncing(false);
      setBulkActive([]);
      guard.finish();
    }
  }, [extensionState, apiRunning, syncAccount]);

  const handleBulkSync = useCallback(() => runSyncQueue(undefined), [runSyncQueue]);

  // ── 应用自带扫码登录────────────────────────────────
  // 后端 POST /api/search/login 会用用户自己的 Edge/Chrome 打开一个可见窗口，
  // 扫码成功后会话直接写进搜索实际读取的 profile，并做一次真实验证。
  // 这是不依赖任何浏览器扩展的登录方式。
  const startScanLogin = useCallback(async (platform: string) => {
    const name = PLATFORM_LABELS[platform as keyof typeof PLATFORM_LABELS] || platform;
    // 先展开面板并给出即时反馈：POST 返回前也要让用户看到"正在启动"。
    setLoginOpen((prev) => (prev[platform] ? prev : { ...prev, [platform]: true }));
    setLoginJobs((prev) => ({
      ...prev,
      [platform]: pendingScanLoginJob(platform, `正在启动 ${name} 登录窗口…`),
    }));
    // 登录期间搜索会被后端 409 拒绝：登记起来，让搜索提交前等待（见 lib/accountGate.ts）。
    markScanLoginActive(platform);
    try {
      const { data } = await axios.post(SCAN_LOGIN_API_BASE, { platform });
      setLoginJobs((prev) => ({ ...prev, [platform]: data as ScanLoginJob }));
    } catch (e) {
      clearScanLoginActive(platform);
      const message = scanLoginErrorMessage(e);
      setLoginJobs((prev) => ({
        ...prev,
        [platform]: { job_id: "", platform, status: "failed", message },
      }));
      toast.error(`${name}：${message}`);
    }
  }, []);

  /** 登录任务到达终态：提示 + 让账号卡片立刻反映新会话。 */
  const onScanLoginTerminal = useCallback((job: ScanLoginJob) => {
    clearScanLoginActive(job.platform);
    const name = PLATFORM_LABELS[job.platform as keyof typeof PLATFORM_LABELS] || job.platform;
    if (job.status === "succeeded") {
      toast.success(`${name} 登录成功，会话已验证并保存。`);
      // The backend already accepted the worker's in-context verification.
      invalidateAccounts(queryClient);
      return;
    }
    if (job.status === "timed_out") {
      toast.warning(`${name} 登录超时：10 分钟内未完成扫码，可重新打开登录窗口。`);
    } else {
      toast.error(`${name} 登录失败：${job.message || "请重试"}`);
    }
    invalidateAccounts(queryClient);
  }, [queryClient]);

  // 轮询进行中的登录任务（后端最长 10 分钟），到达终态即停止对该任务的轮询。
  useEffect(() => {
    const id = setInterval(() => {
      const active = Object.values(loginJobsRef.current)
        .filter((job) => job.job_id && isScanLoginActive(job.status));
      for (const job of active) {
        void (async () => {
          let next: ScanLoginJob;
          try {
            const { data } = await axios.get(`${SCAN_LOGIN_API_BASE}/${job.job_id}`);
            next = data as ScanLoginJob;
          } catch {
            // 单轮失败（网络抖动 / 后端重启）不改变状态，下一轮继续。
            return;
          }
          setLoginJobs((prev) => ({ ...prev, [job.platform]: next }));
          if (!isScanLoginTerminal(next.status)) return;
          if (reportedLoginJobsRef.current.has(next.job_id)) return;
          reportedLoginJobsRef.current.add(next.job_id);
          onScanLoginTerminal(next);
        })();
      }
    }, SCAN_LOGIN_POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [onScanLoginTerminal]);

  // App 在切页时保留本组件和轮询；真正卸载应用时清理登记。
  useEffect(() => () => { clearAllScanLogins(); }, []);

  const toggleDiag = (platform: string) => {
    setOpenDiag((prev) => ({ ...prev, [platform]: !prev[platform] }));
  };

  /** 已真实验证登录的平台数（只统计 connected + verified）。 */
  const verifiedCount = accounts ? summarizeAccounts(accounts).verified : 0;
  const totalPlatforms = PLATFORM_ORDER.length;

  // ── 渲染 ─────────────────────────────────────────────────────────────
  return (
    <div className="accounts-page preview-container">
      <div className="settings-layout">
        <aside className="settings-nav" aria-label="设置分类">
          <button type="button" className={activeSection === "search" ? "active" : ""} onClick={() => onSectionChange("search")}><SlidersHorizontal />搜索设置</button>
          <button type="button" className={activeSection === "accounts" ? "active" : ""} onClick={() => onSectionChange("accounts")}><UserRound />账号与登录</button>
          <button type="button" className={activeSection === "appearance" ? "active" : ""} onClick={() => onSectionChange("appearance")}><Palette />外观与首页</button>
          <p className="aside-note">让工具适应你的习惯。<br />设置保存在当前浏览器。</p>
        </aside>

        <div className="settings-content">

      {/* ── 搜索设置（） ── */}
      {activeSection === "search" && <section>
        <div className="settings-title"><h2>搜索设置</h2><p>为不同平台，留出合适的搜索数量。</p></div>
        <div>
          {PLATFORM_ORDER.map((p) => (
            <LimitRow
              key={p}
              platform={p}
              value={limits[p]}
              onChange={(v) => setLimit(p, v)}
            />
          ))}
        </div>
        <div className="setting-footer"><span>修改后从下一次搜索开始生效</span><button type="button" className="text-link" onClick={resetAll}>恢复默认数量</button></div>
        <div className="info-box"><h3>多一点内容，也需要多一点时间</h3><p>数量越大，搜索耗时可能越长，也更容易遇到平台请求限制。默认每个平台 20 条；需要更多时，可以在结果页继续搜索。</p></div>
      </section>}

      {/* ── 账号与登录 ── */}
      {activeSection === "accounts" && <section>
        <div className="settings-title"><h2>账号与登录</h2><p>连接你的平台账号，让搜索与收藏顺畅一点。</p></div>
        <div className="account-top">
          <p>
            {verifiedCount > 0
              ? `已连接 ${verifiedCount}/${totalPlatforms} 个平台`
              : "推荐：先扫码连接 1–2 个常用平台"}
          </p>
          {/* 「从浏览器同步」是可选加速：需要扩展，扫码登录不依赖它。 */}
          <button
            type="button"
            onClick={handleBulkSync}
            disabled={bulkSyncing || extensionState !== "connected" || apiRunning === false}
            className="btn small"
            title={extensionState === "connected" ? undefined : "需要先安装浏览器扩展（可选加速方式，不装也能用扫码登录）"}
          >
            {bulkSyncing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {bulkActive.length > 0
                  ? `正在同步：${bulkActive.map((p) => PLATFORM_LABELS[p]).join("、")} · ${Math.min(bulkCompleted + bulkActive.length, bulkTotal)}/${bulkTotal}`
                  : `${bulkCompleted}/${bulkTotal}`}
              </>
            ) : (
              <>
                <RefreshCw className="w-4 h-4" />
                从浏览器同步（需扩展）
              </>
            )}
          </button>
        </div>

      {/* 扩展状态（可选加速：扫码登录不依赖它） */}
      <div className={`extension-status ${
        extensionState === "connected"
          ? "border-ok/40 bg-ok-soft text-[#3d7d60]"
          : extensionState === "outdated" || extensionState === "not-installed"
            ? "border-warn/40 bg-warn-soft text-warn"
            : "border-cyber-border-subtle bg-cyber-bg-secondary text-cyber-text-muted"
      }`}>
        <Plug className="w-3.5 h-3.5 inline mr-1.5" />
        {extensionState === "checking" && "正在检测浏览器扩展…（可选，不装也能用扫码登录）"}
        {extensionState === "connected"
          && `扩展已安装并连接（v${extensionVersion || "?"}，协议 v2）· 可用「从浏览器同步」快速复用登录状态`}
        {extensionState === "outdated"
          && `扩展版本过旧${extensionVersion ? `（检测到 v${extensionVersion}）` : ""}，请在 edge://extensions 点击"重新加载"后刷新本页；也可以直接用扫码登录`}
        {extensionState === "not-installed" && "未安装扩展（可选加速）。不装也可以直接点平台卡片的「扫码登录」"}
        {apiRunning === false && " · 本地 API 未运行"}
      </div>

      {/* 本机浏览器不可用：搜索第一道关卡（全局），四个平台都搜不了 */}
      {browserAvailable === false && (
        <div className="mb-3 px-3.5 py-2.5 rounded-lg bg-danger-soft border border-danger/40 text-sm text-danger">
          本机浏览器不可用：四个平台现在都无法搜索。请安装 Chrome / Edge，或执行
          <code className="mx-1 px-1 py-0.5 rounded bg-cyber-bg-tertiary border border-cyber-border-subtle text-cyber-text-secondary">playwright install chromium</code>
          后刷新本页。
        </div>
      )}

      {/* 平台卡片：统一浅色账号卡 */}
      <div className="flex flex-col gap-3">
        {(!accounts || accounts.length === 0) && PLATFORM_ORDER.map((platform) => (
          <article key={platform} className="account-card">
            <div className="account-card-head">
              <h3><i className="pd" style={{ backgroundColor: PLATFORM_COLORS[platform] }} aria-hidden="true" />{PLATFORM_LABELS[platform]}</h3>
              <span className="pill">状态暂不可用</span>
            </div>
            <p>
              {apiRunning === false
                ? "本地服务尚未返回账号状态。启动服务后即可扫码登录。"
                : "账号状态正在加载。可以先扫码登录，或稍等片刻查看。"
              }
            </p>
            <div className="account-actions">
              <button
                type="button"
                className="btn small primary"
                disabled={apiRunning === false}
                onClick={() => startScanLogin(platform)}
              >
                <QrCode className="w-3.5 h-3.5 inline mr-1.5" />扫码登录
              </button>
              <button type="button" className="btn small" disabled>从浏览器同步</button>
              <button type="button" className="btn ghost small" disabled>重新验证</button>
              <button type="button" className="btn ghost small" onClick={() => openOfficial(platform)}>前往登录 <ExternalLink /></button>
            </div>
            <details><summary>诊断与其他方式</summary><div className="diagnostic">本地 API 未连接，暂时无法读取诊断信息。</div></details>
          </article>
        ))}
        {(accounts || []).map((acc) => {
          const busyLabel = busy[acc.platform];
          const name = PLATFORM_LABELS[acc.platform as keyof typeof PLATFORM_LABELS] || acc.platform;
          const color = PLATFORM_COLORS[acc.platform as keyof typeof PLATFORM_COLORS] || "#4ca4dc";
          const diagnostic = acc.diagnostic;
          const snippetLabel = diagnostic?.snippet_available === true
            ? "简介可用"
            : diagnostic?.snippet_available === false
              ? "简介暂不可用"
              : "简介状态未知";
          // 扫码登录（方案 A 主路径）该平台的状态。
          const loginJob = loginJobs[acc.platform];
          const loginBusy = isScanLoginActive(loginJob?.status);
          const loginView = scanLoginView(loginJob);
          const loginLabel = scanLoginActionLabel(loginJob);
          const extensionReady = extensionState === "connected";
          // 卡片主结论（可用 / 不可用 / 验证中）与动作提示：只回答「现在能不能搜、该点哪个按钮」。
          const verdict = accountSearchVerdict(acc, browserAvailable);
          const actionHint = accountActionHint(acc, verdict);
          return (
            <article key={acc.platform} className="account-card">
              {/* 头部：平台标记 + 名称 + 主结论徽章 + busy */}
              <div className="account-card-head">
                <div>
                  <h3>
                    <i className="pd" style={{ backgroundColor: color }} aria-hidden="true" />
                    <span>{name}</span>
                    <span className={`px-2 py-0.5 rounded-full text-[10.5px] border ${VERDICT_BADGE[verdict.kind]}`}>
                      {VERDICT_LABEL[verdict.kind]}
                      {acc.verified && <ShieldCheck className="w-3 h-3 inline ml-1" />}
                    </span>
                  </h3>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  {busyLabel && (
                    <span className="flex items-center gap-1.5 text-xs text-brand-strong">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      {busyLabel === "syncing" && "同步中…"}
                      {busyLabel === "verifying" && "验证中…"}
                      {busyLabel === "deleting" && "清除中…"}
                    </span>
                  )}
                </div>
              </div>

              {/* 主结论：可用 / 不可用（最短原因） */}
              <p className={`text-sm font-medium ${VERDICT_LINE[verdict.kind]}`}>
                {verdict.kind === "available" ? "✓ 可用" : verdict.kind === "unavailable" ? "✗ 不可用" : "… 验证中"}
                {" · "}{verdict.reason}
              </p>

              {/* 动作提示：现在该点哪个按钮，或为什么现在不能搜 */}
              <p className="text-xs text-cyber-text-secondary">{actionHint}</p>

              {/* 详情：所有具体原因、过程、诊断折叠在这里（默认收起） */}
              <div className="mb-3">
                <button
                  type="button"
                  onClick={() => toggleDiag(acc.platform)}
                  className="flex items-center gap-1 text-[11.5px] text-cyber-text-muted hover:text-brand-strong transition-colors"
                >
                  {openDiag[acc.platform] ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  {openDiag[acc.platform] ? "收起详情" : "详情"}
                </button>
                {openDiag[acc.platform] && (
                  <div className="mt-2 px-3.5 py-2.5 rounded-lg bg-cyber-bg-tertiary border border-cyber-border-subtle text-[11px] text-cyber-text-secondary space-y-1">
                    {/* 本机状态与原因 */}
                    <div className="text-cyber-text-primary font-semibold">本机状态</div>
                    <div>账号状态：{accountCardStatusLabel(acc)}</div>
                    <div>本机登录信息：{acc.profile_exists ? "已保存" : "不存在"}</div>
                    <div>浏览器后端：{acc.browser_backend ? (BACKEND_TEXT[acc.browser_backend] || acc.browser_backend) : "未知"}</div>
                    {acc.verified && (
                      <div>最近验证于 {acc.last_verified_at ? new Date(acc.last_verified_at).toLocaleString("zh-CN") : "本次会话"}</div>
                    )}
                    {acc.verification && (
                      <div>最近检查：{new Date(acc.verification.checked_at).toLocaleString("zh-CN")} · 平台登录验证</div>
                    )}
                    <div>搜索能力：{accountUsageHint(acc)}</div>
                    <div className="account-evidence" aria-live="polite">
                      <span>{accountOperationLabel("search", acc.usage?.search)}</span>
                      <span>{accountOperationLabel("favorites", acc.usage?.favorites)}</span>
                    </div>
                    {acc.status === "unverified" && (
                      <div>已导入登录信息但还没验证：可以先试着搜索（公开搜索不一定有结果），搜不到再重新同步或扫码。</div>
                    )}
                    {acc.platform === "douyin" && (
                      <div>抖音允许公开搜索：即使未登录也可能返回结果（公开内容，可能比登录态少）。</div>
                    )}
                    {acc.safe_message && (
                      <div className="text-warn">提示：{acc.safe_message}</div>
                    )}

                    {diagnostic && (
                      <div className="pt-2 mt-2 border-t border-cyber-border-subtle space-y-1">
                        <div className="text-cyber-text-primary font-semibold">排查诊断</div>
                        <p className="text-cyber-text-muted">以下为历史记录，仅供排查，不代表此刻一定能搜到。</p>
                        <div>记录路径：{diagnosticSearchModeLabel(diagnostic.search_mode)}</div>
                        <div>账号状态：{diagnosticAccountStateLabel(diagnostic.account_state)}</div>
                        <div>备用路径：{diagnostic.fallback_active ? "正在使用" : "未启用"}</div>
                        <div>简介能力：{snippetLabel}</div>
                        <div>诊断记录：{diagnostic.user_message || "暂无记录"}</div>
                        <div>记录中的建议：{diagnostic.recommended_action || "无"}</div>
                      </div>
                    )}
                    {lastDiag[acc.platform] && (
                      <div className={`${diagnostic ? "pt-2 mt-2 border-t border-cyber-border-subtle space-y-1" : "space-y-1"}`}>
                        <div className="text-cyber-text-primary font-semibold">最近一次同步细节</div>
                        <div>
                          阶段：{SYNC_STAGE_TEXT[lastDiag[acc.platform].sync_stage] || lastDiag[acc.platform].sync_stage || "—"}
                          {" · "}读取 {lastDiag[acc.platform].received_cookie_count ?? "—"} 条
                          {" / 接受 "}{lastDiag[acc.platform].accepted_cookie_count ?? "—"} 条
                          {" / 跳过 "}{lastDiag[acc.platform].skipped_cookie_count ?? "—"} 条
                        </div>
                        <div>
                          登录标记：
                          {(LOGIN_MARKERS[acc.platform] || []).map((m) => {
                            const v = lastDiag[acc.platform].login_marker_presence?.[m];
                            return v === undefined ? null : `${m} ${v ? "✓" : "✗"}`;
                          }).filter(Boolean).join(" · ") || "—"}
                        </div>
                        <div>
                          标记判定（启发式，非登录结论）：{lastDiag[acc.platform].required_cookie_present === null
                            ? "—" : lastDiag[acc.platform].required_cookie_present ? "有" : "无"}
                          {" · 已验证（真实验证）："}{lastDiag[acc.platform].verified ? "是" : "否"}
                        </div>
                        {lastDiag[acc.platform].safe_error_code && (
                          <div>
                            错误码：{lastDiag[acc.platform].safe_error_code}
                            {lastDiag[acc.platform].safe_message && ` · ${lastDiag[acc.platform].safe_message}`}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* 主要操作：方案 A —— 扫码登录是主路径，扩展同步是可选加速 */}
              <div className="account-actions">
                <button
                  onClick={() => startScanLogin(acc.platform)}
                  disabled={loginBusy || apiRunning === false || bulkSyncing}
                  className="btn small primary"
                  title={apiRunning === false
                    ? "本地服务未运行，无法打开登录窗口"
                    : "用手机 App 扫码登录，不需要安装任何东西"}
                >
                  {loginBusy
                    ? <Loader2 className="w-3.5 h-3.5 inline mr-1.5 animate-spin" />
                    : <QrCode className="w-3.5 h-3.5 inline mr-1.5" />}
                  {loginLabel}
                </button>
                <button
                  onClick={() => syncAccount(acc.platform as PlatformSlug)}
                  disabled={!!busyLabel || !extensionReady || bulkSyncing}
                  className="btn small"
                  title={extensionReady
                    ? "复用当前浏览器里已有的登录状态（需扩展）"
                    : "可选加速：安装浏览器扩展后才能复用浏览器登录状态；不装也可以直接用扫码登录"}
                >
                  <Plug className="w-3.5 h-3.5 inline mr-1.5" />
                  从浏览器同步
                </button>
                <button
                  onClick={() => openOfficial(acc.platform)}
                  className="btn ghost small"
                >
                  <ExternalLink className="w-3.5 h-3.5 inline mr-1.5" />打开官网
                </button>
                <button
                  onClick={() => verifyAccount(acc.platform)}
                  disabled={!!busyLabel || bulkSyncing}
                  className="btn ghost small"
                >
                  <RefreshCw className="w-3.5 h-3.5 inline mr-1.5" />重新验证
                </button>
                <button
                  onClick={() => deleteSession(acc.platform)}
                  disabled={!!busyLabel || !acc.profile_exists || bulkSyncing}
                  className="text-link danger-link"
                >
                  <Trash2 className="w-3.5 h-3.5 inline mr-1.5" />清除登录状态
                </button>
              </div>

              {/* 扫码登录面板（主路径，按平台独立展开；登录结果不随折叠丢失） */}
              {loginOpen[acc.platform] && (
                <div className="mt-3 px-3.5 py-2.5 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/60">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-[11.5px] text-cyber-text-secondary">
                      会在你电脑上打开一个浏览器窗口，用手机 App 扫码即可；登录成功后窗口自动关闭，
                      会话保存在本机，不经过网页也不依赖任何插件。
                    </p>
                    <button
                      type="button"
                      onClick={() => setLoginOpen((prev) => ({ ...prev, [acc.platform]: false }))}
                      className="text-[11px] text-cyber-text-muted hover:text-cyber-text-primary transition-colors whitespace-nowrap"
                    >
                      收起
                    </button>
                  </div>
                  <div className="mt-2 flex items-center gap-2 flex-wrap">
                    {loginBusy && <Loader2 className="w-3 h-3 animate-spin text-brand-strong" />}
                    {loginView.tone === "ok" && <Check className="w-3.5 h-3.5 text-[#3d7d60]" />}
                    {loginView.text && (
                      <span className={`text-[11.5px] ${LOGIN_TONE_TEXT[loginView.tone]}`}>
                        {loginView.text}
                      </span>
                    )}
                    {loginBusy && loginJob?.job_id && (
                      <span className="text-[11px] text-cyber-text-muted">
                        · 未完成扫码可等到 10 分钟；期间不能同时搜索或同步
                      </span>
                    )}
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>

      {/* 首次使用说明：主路径是扫码登录，扩展是可选加速 */}
      <div className="info-box">
        <h3>第一次使用？先扫码登录</h3>
        <p>点常用平台卡片的「扫码登录」，四野会在你电脑上打开一个浏览器窗口，用手机 App 扫一下即可。先连接 1–2 个平台就能开始搜索，其余平台以后按需添加。</p>
        <p className="mt-2">想复用浏览器里已经登录好的状态？装一次浏览器扩展，就能用「从浏览器同步」——这是可选加速方式，不装也能用。</p>
        {onNavigateHelp && <button type="button" className="text-link" onClick={onNavigateHelp}>查看登录与安装说明 <ExternalLink /></button>}
      </div>
      </section>}

      {activeSection === "appearance" && <section>
        <div className="settings-title"><h2>外观与首页</h2><p>安静一点，或多一些内容。按你的习惯来。</p></div>
        <div className="theme-choices">
          {(["light", "dark"] as const).map((value) => <button key={value} type="button" className="theme-card" aria-pressed={theme === value} onClick={() => setTheme(value)}>
            <div className={`theme-preview ${value === "dark" ? "night" : ""}`} aria-hidden="true" />
            <span>{value === "dark" ? "深色" : "浅色"}{theme === value && <Check />}</span>
          </button>)}
        </div>
        <div className="setting-row"><div><div className="setting-label">首页模式</div><p className="setting-desc">极简留白，实用展示最近的探索</p></div><div className="segmented">
          <button type="button" className="segment" aria-pressed={homePreferences.mode === "min"} onClick={() => homePreferences.setMode("min")}>极简</button>
          <button type="button" className="segment" aria-pressed={homePreferences.mode === "full"} onClick={() => homePreferences.setMode("full")}>实用</button>
        </div></div>
        <div className="setting-row"><div><div className="setting-label">最近搜索</div><p className="setting-desc">回到上一次搜索过的关键词</p></div><button type="button" className="switch" role="switch" aria-checked={homePreferences.history} aria-label="首页显示最近搜索" onClick={() => homePreferences.setSection("history", !homePreferences.history)} /></div>
        <div className="setting-row"><div><div className="setting-label">最近搜到</div><p className="setting-desc">继续阅读上次获取的内容</p></div><button type="button" className="switch" role="switch" aria-checked={homePreferences.recent} aria-label="首页显示最近搜到" onClick={() => homePreferences.setSection("recent", !homePreferences.recent)} /></div>
        <div className="setting-footer"><span>主题与首页偏好会保存在当前浏览器</span>{onNavigateSearch && <button type="button" className="text-link" onClick={onNavigateSearch}>回首页看看 →</button>}</div>
        <div className="info-box"><p>极简模式会暂时隐藏首页板块，不清除板块选择。开启任一板块会自动切回实用模式。</p></div>
      </section>}
        </div>
      </div>
    </div>
  );
}
