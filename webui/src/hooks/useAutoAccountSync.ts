/**
 * 打开程序时自动同步/复核登录状态（Round 18，方案 A 扩展）。
 *
 * 用户的实际顺序通常是：在浏览器登录平台（或扫码登录）→ 打开本项目 →
 * 开始搜索。之前必须自己进"账号设置"点"一键同步四个平台"，本 hook 把这步自动化。
 *
 * 两条路径（互斥，共用同一冷却时间戳，同一轮不会都启动浏览器）：
 * 1. **扩展已连接**：走扩展同步（导入当前浏览器 Cookie + 验证），即原行为；
 * 2. **扩展不可用**（方案 A 主路径：扫码登录）：本地已有 profile 但后端未确认
 *    登录的平台 → 直接调 `POST /accounts/{platform}/verify` 复核一次。
 *    后端的"已验证"是内存态，重启后 profile 还在但状态退化成
 *    "已导入，未确认登录"，没有扩展时没人补这一步，账号卡片就会一直停在未确认。
 *
 * 触发时机：
 * - 组件挂载（= 打开程序、页面加载完成，等账号状态首次就绪后判定）；
 * - 页面从后台回到前台（visibilitychange / focus）—— 覆盖"去浏览器登录完
 *   再切回来"的情况。冷却未到时补一次定时重试，所以切回来一定会被处理。
 *
 * 刻意**不**按账号轮询反复评估：那会让一个始终无法同步的平台（例如浏览器
 * 里根本没登录过）每轮都重新启动浏览器。首次尝试之后只由"回到前台"触发。
 *
 * 守卫（全部由纯函数 decideAutoSync / decideStartupVerify 判定，便于测试）：
 * - 四个平台都已验证登录 → 什么都不做（平时打开程序零额外请求）；
 * - 扩展未连接/过旧、本地 API 不可用 → 不启动；
 * - 已有同步/验证队列在跑 → 不叠加；
 * - 两次尝试之间有冷却（AUTO_SYNC_COOLDOWN_MS）。
 *
 * 与搜索的互斥：同步/复核期间平台状态是 syncing/verifying，搜索会在提交前等待
 * （见 lib/accountGate.ts），因此不会把用户的搜索顶成 409。
 *
 * 打扰策略：扩展同步沿用 shouldAnnounceAutoSync（同步到东西才提示）；
 * 复核反过来 —— 全部确认成功是预期结果，只有出问题才提示
 * （shouldAnnounceStartupVerify）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import type { PlatformSlug } from "@/types/search";
import { useAccounts, invalidateAccounts } from "@/hooks/useAccounts";
import {
  AUTO_SYNC_COOLDOWN_MS,
  buildBulkBlockedMessage,
  buildBulkSummaryMessage,
  buildVerifySummaryMessage,
  createBulkSyncGuard,
  decideAutoSync,
  decideStartupVerify,
  runBulkSync,
  shouldAnnounceAutoSync,
  shouldAnnounceStartupVerify,
  summarizeBulkOutcomes,
  type BulkSyncBlockReason,
} from "@/lib/accountBulkSync";
import {
  EXTENSION_PROBE_TOTAL_MS,
  detectExtension,
  mapSyncResultToOutcome,
  requestPlatformSync,
  requestPlatformVerify,
  type ExtensionState,
} from "@/lib/extensionSync";

/** 页面重新可见后，等一小段再评估，避免切换标签时立刻发起同步。 */
const RESUME_SETTLE_MS = 1000;

/** 同步结束后结果留在页面上的时间（之后自动收起，不长期占用版面）。 */
const NOTE_VISIBLE_MS = 8000;

export interface AutoAccountSyncState {
  /** 自动同步是否正在进行。 */
  running: boolean;
  /** 最近一次自动同步的结果文案（null = 不需要提示）。 */
  note: string | null;
}

export function useAutoAccountSync(): AutoAccountSyncState {
  const { accounts, apiRunning } = useAccounts();
  const queryClient = useQueryClient();
  const [extensionState, setExtensionState] = useState<ExtensionState>("checking");
  const [running, setRunning] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const noteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 结果提示：留一段时间再收起（自动同步必须让人看得见）。 */
  const showNote = useCallback((text: string | null) => {
    if (noteTimerRef.current) clearTimeout(noteTimerRef.current);
    noteTimerRef.current = null;
    setNote(text);
    if (text) {
      noteTimerRef.current = setTimeout(() => {
        noteTimerRef.current = null;
        setNote(null);
      }, NOTE_VISIBLE_MS);
    }
  }, []);

  // guard 必须同步持有（React state 在一次事件内可能还没更新）。
  const guardRef = useRef<ReturnType<typeof createBulkSyncGuard> | null>(null);
  if (guardRef.current === null) guardRef.current = createBulkSyncGuard();
  /** 上次自动同步尝试的时间戳；null = 本次页面生命周期还没试过。 */
  const lastAttemptRef = useRef<number | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 触发判定时读取最新值，但不作为 effect 依赖（否则账号轮询会反复评估）。
  const latest = useRef({ accounts, apiRunning, extensionState, running });
  latest.current = { accounts, apiRunning, extensionState, running };

  // ── 扩展探测（与账号页共用同一份协议实现）────────────────────────────
  useEffect(() => {
    let cancelled = false;
    void detectExtension().then((probe) => {
      if (!cancelled) setExtensionState(probe.state);
    });
    return () => { cancelled = true; };
  }, []);

  const runAutoSync = useCallback(async (platforms: readonly PlatformSlug[]) => {
    const guard = guardRef.current;
    if (!guard || !guard.tryStart()) return;
    const checkBlock = (): BulkSyncBlockReason | null => {
      const ext = latest.current.extensionState;
      if (ext === "outdated") return "extension_outdated";
      if (ext !== "connected") return "extension_not_connected";
      if (latest.current.apiRunning === false) return "api_unavailable";
      return null;
    };
    lastAttemptRef.current = Date.now();
    setRunning(true);
    showNote("正在自动同步登录状态…");
    try {
      const result = await runBulkSync({
        platforms,
        checkBlock,
        syncOne: async (platform) => {
          try {
            return mapSyncResultToOutcome(platform, await requestPlatformSync(platform));
          } catch (err) {
            const resp = (err as {
              response?: {
                status?: number;
                data?: { safe_error_code?: string; safe_message?: string };
              };
            }).response;
            const code = resp?.data?.safe_error_code;
            const blocked = resp?.status === 409 && code === "search_in_progress";
            return {
              platform, kind: "failed" as const, success: false, verified: false,
              safeErrorCode: code || "sync_request_failed",
              safeMessage: resp?.data?.safe_message,
              ...(blocked ? { blockQueue: "search_in_progress" as const } : {}),
            };
          }
        },
      });
      // 自动同步只给一条汇总提示：它是用户没主动要求的后台动作，但静默失败
      // 会让用户以为已经同步好了。
      if (result.blocked && result.blockReason) {
        // 搜索抢先拿到租约是正常情况（用户意图优先），不打扰用户。
        if (result.blockReason !== "search_in_progress") {
          toast.warning(buildBulkBlockedMessage(result.blockReason));
        }
        showNote(buildBulkBlockedMessage(result.blockReason));
      } else {
        const counts = summarizeBulkOutcomes(result.outcomes);
        if (!shouldAnnounceAutoSync(counts)) {
          // 浏览器里本来就没有可同步的会话（例如四个平台都没登录过）：
          // 这不是用户需要立刻处理的事，账号徽章已经说明了状态，保持安静，
          // 否则每次打开程序都会弹一条失败提示。
          showNote(null);
        } else {
          const summary = buildBulkSummaryMessage(counts);
          if (summary.tone === "success") {
            toast.success(summary.title, { position: "top-center" });
          } else if (summary.tone === "warning") {
            toast.warning(summary.title, { description: summary.description, position: "top-center" });
          } else {
            // "已导入待确认"这类结果也必须看得见 —— 否则用户会以为什么都没发生。
            toast.info(summary.title, { position: "top-center" });
          }
          showNote(summary.title);
        }
      }
    } finally {
      setRunning(false);
      guard.finish();
    }
  }, [showNote]);

  /**
   * 启动后复核本地已有的登录状态（方案 A：不依赖扩展）。
   *
   * 后端的"已验证"只存在内存里，重启后 profile 还在但状态退化成
   * "已导入，未确认登录"。装了扩展时上面的 runAutoSync 会顺带验回来；
   * 没装扩展（扫码登录主路径）时这里是唯一的补救路径：
   * 对"本地有 profile 但未确认"的平台，让后端用它起无头上下文跑一次 pong。
   *
   * 提示策略与自动同步不同：全部确认成功是**预期结果**，保持安静；
   * 只有出现需要用户处理的结果（过期/不可用/失败/仍未确认）才提示。
   */
  const runStartupVerify = useCallback(async (platforms: readonly PlatformSlug[]) => {
    const guard = guardRef.current;
    if (!guard || !guard.tryStart()) return;
    lastAttemptRef.current = Date.now();
    setRunning(true);
    showNote("正在复核已有登录状态…");
    try {
      const result = await runBulkSync({
        platforms,
        checkBlock: () => (
          latest.current.apiRunning === false ? "api_unavailable" : null
        ),
        syncOne: async (platform) => {
          try {
            return mapSyncResultToOutcome(platform, await requestPlatformVerify(platform));
          } catch (err) {
            const resp = (err as {
              response?: {
                status?: number;
                data?: { safe_error_code?: string; safe_message?: string };
              };
            }).response;
            const code = resp?.data?.safe_error_code;
            const blocked = resp?.status === 409 && code === "search_in_progress";
            return {
              platform, kind: "failed" as const, success: false, verified: false,
              safeErrorCode: code || "verify_request_failed",
              safeMessage: resp?.data?.safe_message,
              ...(blocked ? { blockQueue: "search_in_progress" as const } : {}),
            };
          }
        },
      });
      // 复核会改写后端状态：立即刷新账号缓存，不等下一次轮询。
      invalidateAccounts(queryClient);
      if (result.blocked && result.blockReason) {
        // 用户抢先发起搜索是正常情况（用户意图优先），不打扰。
        if (result.blockReason !== "search_in_progress") {
          showNote(buildBulkBlockedMessage(result.blockReason));
        }
        return;
      }
      const counts = summarizeBulkOutcomes(result.outcomes);
      if (!shouldAnnounceStartupVerify(counts)) {
        showNote(null);
        return;
      }
      const summary = buildVerifySummaryMessage(counts);
      if (summary.tone === "warning") {
        toast.warning(summary.title, { description: summary.description, position: "top-center" });
      } else {
        toast.info(summary.title, { position: "top-center" });
      }
      showNote(summary.title);
    } finally {
      setRunning(false);
      guard.finish();
    }
  }, [showNote, queryClient]);

  /**
   * 评估是否需要自动同步。
   * `scheduleRetry`（回到前台时）会在冷却未到时补一次定时重试，
   * 保证"去浏览器登录完再切回来"最终一定会被处理。
   * `extensionState` 允许调用方传入刚刚探测到的结果，避免等 React
   * 重新渲染后才能读到新状态。
   */
  const evaluate = useCallback((opts?: {
    scheduleRetry?: boolean;
    extensionState?: ExtensionState;
  }) => {
    const state = latest.current;
    const last = lastAttemptRef.current;
    const decision = decideAutoSync({
      accounts: state.accounts,
      extensionState: opts?.extensionState ?? state.extensionState,
      apiRunning: state.apiRunning,
      syncing: state.running,
      msSinceLastAttempt: last === null ? null : Date.now() - last,
    });
    if (decision.run) {
      void runAutoSync(decision.platforms);
      return;
    }
    // 扩展不可用时没有"同步"可做，但本地已有的 profile 仍值得复核一次 ——
    // 否则重启后账号会一直停在"已导入，未确认登录"（方案 A 的主路径下没有
    // 扩展来补这一刀）。冷却时间戳与自动同步共用，两条路径不会重复启动浏览器。
    if (decision.skipReason === "extension_not_connected"
        || decision.skipReason === "extension_outdated") {
      const verify = decideStartupVerify({
        accounts: state.accounts,
        apiRunning: state.apiRunning,
        syncing: state.running,
        msSinceLastAttempt: last,
        extensionState: opts?.extensionState ?? state.extensionState,
      });
      if (verify.run) {
        void runStartupVerify(verify.platforms);
        return;
      }
    }
    if (opts?.scheduleRetry && decision.skipReason === "cooldown" && last !== null) {
      const wait = Math.max(0, AUTO_SYNC_COOLDOWN_MS - (Date.now() - last)) + 250;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      retryTimerRef.current = setTimeout(() => {
        retryTimerRef.current = null;
        evaluate();
      }, wait);
    }
  }, [runAutoSync, runStartupVerify]);

  // 挂载/依赖就绪时评估一次；首次尝试之后不再由账号轮询驱动。
  useEffect(() => {
    if (lastAttemptRef.current !== null) return;
    evaluate();
  }, [evaluate, accounts, apiRunning, extensionState]);

  // 页面回到前台：从浏览器登录完再切回来是最常见的场景。
  // 同时重新探测扩展 —— 用户可能刚在扩展管理页启用/重新加载了扩展，
  // 或者 content script 直到此刻才注入完成。
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    const onResume = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(async () => {
        let probeState: ExtensionState | undefined;
        if (latest.current.extensionState !== "connected") {
          const probe = await detectExtension({ totalMs: EXTENSION_PROBE_TOTAL_MS });
          if (cancelled) return;
          probeState = probe.state;
          setExtensionState(probe.state);
        }
        if (cancelled) return;
        evaluate({ scheduleRetry: true, extensionState: probeState });
      }, RESUME_SETTLE_MS);
    };
    document.addEventListener("visibilitychange", onResume);
    window.addEventListener("focus", onResume);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      if (noteTimerRef.current) clearTimeout(noteTimerRef.current);
      document.removeEventListener("visibilitychange", onResume);
      window.removeEventListener("focus", onResume);
    };
  }, [evaluate]);

  return { running, note };
}
