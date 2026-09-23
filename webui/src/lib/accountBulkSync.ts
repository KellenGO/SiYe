/**
 * 批量同步四平台（Round 14.3 / Phase 4.3，无 React 依赖）。
 *
 * 职责：
 * - 固定平台顺序：xhs → douyin → bilibili → zhihu（启动顺序恒定）；
 * - worker-pool 最大并发 2（Phase 4.3：不同平台可重叠，但不 Promise.all 四个）；
 * - 单个平台失败/异常 → 记录结构化结果，继续后续平台；
 * - 全局阻断（扩展未连接/过旧、API 不可用、搜索进行中）→ 停止启动新平台，
 *   已开始的平台允许安全结束；
 * - outcomes 最终按固定平台顺序排列（不按完成顺序乱序）；
 * - 进度回调（activePlatforms + completedCount）；
 * - 结果汇总（不含 Cookie/ticket/header/响应体/traceback）。
 *
 * AccountsPage 必须调用本模块的 runBulkSync；测试也必须直接 import 本模块，
 * 不在测试里复制循环。
 */

import type { PlatformSlug } from "../types/search.js";
import { isAccountVerified } from "./accounts.js";
import { PLATFORM_SLUGS } from "./platformMeta.js";

/** 固定平台顺序（一键同步与测试共用）。 */
// 顺序的唯一来源见 lib/platformMeta.ts
export const BULK_SYNC_PLATFORM_ORDER: readonly PlatformSlug[] = PLATFORM_SLUGS;

/** 一键同步最大并发（Phase 4.3：两个账号操作可同时执行）。 */
export const BULK_SYNC_MAX_CONCURRENCY = 2;

/** 单平台同步结果（安全字段：不含 Cookie/ticket/header/响应体/traceback）。 */
export interface SyncAttemptOutcome {
  platform: PlatformSlug;
  kind: "verified" | "imported" | "verifying" | "unavailable" | "failed";
  success: boolean;
  verified: boolean;
  safeErrorCode?: string;
  safeMessage?: string;
  /** 该尝试暴露了全局阻断（如搜索进行中）：剩余队列应停止。 */
  blockQueue?: BulkSyncBlockReason;
}

/** 全局阻断类型：出现其一即停止剩余队列。 */
export type BulkSyncBlockReason =
  | "extension_not_connected"
  | "extension_outdated"
  | "api_unavailable"
  | "search_in_progress";

export interface BulkSyncProgress {
  /** 正在同步的平台（最多 2 个，保持固定启动顺序）。 */
  activePlatforms: PlatformSlug[];
  /** 已完成的平台数（0..4）。 */
  completedCount: number;
  totalCount: number;
}

export interface BulkSyncResult {
  outcomes: SyncAttemptOutcome[];
  /** 是否因全局阻断而提前停止。 */
  blocked: boolean;
  blockReason?: BulkSyncBlockReason;
  completedCount: number;
  totalCount: number;
}

export type SyncOneFn = (platform: PlatformSlug) => Promise<SyncAttemptOutcome>;
export type GlobalBlockCheckFn = () => BulkSyncBlockReason | null;
export type BulkProgressCallback = (progress: BulkSyncProgress) => void;

export interface BulkSyncOptions {
  syncOne: SyncOneFn;
  /** 每个平台开始前检查的全局阻断（扩展/API 状态等）。 */
  checkBlock?: GlobalBlockCheckFn;
  onProgress?: BulkProgressCallback;
  /**
   * 只同步这些平台（默认全部四个）。自动同步只针对"尚未确认登录"的平台，
   * 不必为了其中一个未登录平台重新同步另外三个。输出仍按固定平台顺序。
   */
  platforms?: readonly PlatformSlug[];
}

/** 解析实际同步目标：只保留固定顺序里的已知平台（默认全部四个）。 */
export function resolveBulkTargets(
  platforms?: readonly PlatformSlug[]
): PlatformSlug[] {
  if (!platforms) return [...BULK_SYNC_PLATFORM_ORDER];
  return BULK_SYNC_PLATFORM_ORDER.filter((p) => platforms.includes(p));
}

/**
 * 批量同步（Phase 4.3）：worker-pool 最大并发 2，固定启动顺序。
 * 单平台失败继续；全局阻断出现后不再启动新平台，已开始的平台安全结束。
 * outcomes 按固定平台顺序返回。
 */
export async function runBulkSync(options: BulkSyncOptions): Promise<BulkSyncResult> {
  const { syncOne, checkBlock, onProgress, platforms } = options;
  const totalCount = BULK_SYNC_PLATFORM_ORDER.length;
  const targets = resolveBulkTargets(platforms);
  const results = new Map<PlatformSlug, SyncAttemptOutcome>();
  const active = new Set<PlatformSlug>();
  let blocked = false;
  let blockReason: BulkSyncBlockReason | undefined;

  const runOne = async (platform: PlatformSlug): Promise<void> => {
    // 全局阻断（检查可能因并发在 runOne 内发生变化）。
    const blocker = checkBlock ? checkBlock() : null;
    if (blocker) {
      blocked = true;
      blockReason = blocker;
      return;
    }
    active.add(platform);
    onProgress?.({ activePlatforms: [...active], completedCount: results.size, totalCount });

    let outcome: SyncAttemptOutcome;
    try {
      outcome = await syncOne(platform);
    } catch {
      // 单平台异常：记录安全失败，绝不透出原始错误细节。
      outcome = {
        platform,
        kind: "failed",
        success: false,
        verified: false,
        safeErrorCode: "sync_attempt_error",
        safeMessage: "同步失败，请查看该平台卡片诊断",
      };
    }
    active.delete(platform);
    results.set(platform, outcome);
    onProgress?.({ activePlatforms: [...active], completedCount: results.size, totalCount });

    // 该平台暴露了全局阻断（例如同步票据返回 search_in_progress）→ 停止。
    if (outcome.blockQueue) {
      blocked = true;
      blockReason = outcome.blockQueue;
    }
  };

  const workers = new Set<Promise<void>>();
  const queue = [...targets];
  while (queue.length > 0) {
    // 填满空位（最多并发 2）；已阻断则不再启动新平台。
    while (queue.length > 0 && workers.size < BULK_SYNC_MAX_CONCURRENCY && !blocked) {
      const platform = queue.shift()!;
      const task = runOne(platform).finally(() => workers.delete(task));
      workers.add(task);
    }
    if (workers.size === 0) break; // 已阻断且无在途任务
    await Promise.race(workers); // 等任一完成腾出空位
  }
  await Promise.all(workers); // 已开始的平台允许安全结束

  const outcomes = BULK_SYNC_PLATFORM_ORDER
    .map((p) => results.get(p))
    .filter((o): o is SyncAttemptOutcome => o !== undefined);

  return {
    outcomes,
    blocked,
    blockReason,
    completedCount: outcomes.length,
    totalCount,
  };
}

// ── 结果汇总（纯函数，供组件与测试共用）───────────────────────────────

export interface BulkOutcomeCounts {
  verified: number;
  imported: number;
  verifying: number;
  unavailable: number;
  failed: number;
  total: number;
}

/** 按 kind 汇总四个平台的结果。 */
export function summarizeBulkOutcomes(
  outcomes: readonly SyncAttemptOutcome[]
): BulkOutcomeCounts {
  const counts: BulkOutcomeCounts = {
    verified: 0,
    imported: 0,
    verifying: 0,
    unavailable: 0,
    failed: 0,
    total: outcomes.length,
  };
  for (const o of outcomes) {
    if (o.kind === "verified") counts.verified += 1;
    else if (o.kind === "imported") counts.imported += 1;
    else if (o.kind === "verifying") counts.verifying += 1;
    else if (o.kind === "unavailable") counts.unavailable += 1;
    else counts.failed += 1;
  }
  return counts;
}

export interface BulkSummaryMessage {
  tone: "success" | "warning" | "info";
  title: string;
  description?: string;
}

/** 汇总 toast 文案（仅由数量与固定文案构成，绝不包含敏感内容）。 */
export function buildBulkSummaryMessage(counts: BulkOutcomeCounts): BulkSummaryMessage {
  const { verified, imported, verifying, unavailable, failed, total } = counts;
  const pending = imported + verifying;
  if (total > 0 && verified === total) {
    return { tone: "success", title: "四个平台同步并验证成功" };
  }
  const parts = [
    `${verified} 个已验证`,
    `${pending} 个已导入待确认`,
  ];
  if (unavailable > 0) parts.push(`${unavailable} 个暂不可用`);
  if (failed > 0) parts.push(`${failed} 个失败`);
  if (failed > 0 || unavailable > 0) {
    return {
      tone: "warning",
      title: `同步完成：${parts.join("，")}`,
      description: "请查看对应平台卡片诊断",
    };
  }
  return { tone: "info", title: `同步完成：${parts.join("，")}` };
}

/**
 * 自动同步是否值得提示用户。
 *
 * 用户没主动要求这次同步，所以只在真的同步到东西（或有会话待确认）时才提示：
 * 浏览器里本来就没有可同步的会话时保持安静，否则每次打开程序都会弹一条
 * "N 个失败"，而账号徽章与平台卡片已经说明了状态。
 */
export function shouldAnnounceAutoSync(counts: BulkOutcomeCounts): boolean {
  if (counts.total === 0) return false;
  return counts.verified + counts.imported + counts.verifying > 0;
}

/** 全局阻断提示文案（固定安全文案）。 */
export function buildBulkBlockedMessage(reason: BulkSyncBlockReason | undefined): string {
  switch (reason) {
    case "extension_not_connected":
      return "未检测到浏览器扩展，已停止后续同步。可以改用平台卡片上的「扫码登录」，或安装扩展并刷新本页后重试。";
    case "extension_outdated":
      return "扩展版本过旧，已停止后续同步。请在扩展管理页点击“重新加载”后刷新本页；也可以改用「扫码登录」。";
    case "api_unavailable":
      return "四野服务未连接，已停止后续同步。请重新启动应用后再试。";
    case "search_in_progress":
      return "搜索正在进行，暂时不能同步账号，已停止后续同步。请等待搜索完成后重试。";
    default:
      return "同步已中断，请重试。";
  }
}

// ── 双击 guard（纯状态规则；组件用 ref 持有）──────────────────────────

/**
 * 批量同步双击 guard：单线程布尔锁。异步 React state 在双击间隔内可能
 * 尚未更新，因此必须用同步的 ref 锁 —— 本函数提供可测试的纯规则。
 */
export function createBulkSyncGuard(): {
  tryStart(): boolean;
  finish(): void;
} {
  let running = false;
  return {
    tryStart(): boolean {
      if (running) return false; // 已有一队列在跑：拒绝第二次启动
      running = true;
      return true;
    },
    finish(): void {
      running = false;
    },
  };
}

// ── 自动同步决策（Round 18，纯函数；无 React 依赖）─────────────────────

/** 自动同步判定的账号最小字段集（与 GET /api/search/accounts 对齐）。 */
export interface AutoSyncAccount {
  platform: string;
  status: string;
  verified: boolean;
  /**
   * 本地是否已有该平台的 profile。只有 `true` 才值得"重新验证"：
   * 没有 profile 时验证只会得到"未登录"，那是扫码/同步该做的事。
   */
  profile_exists?: boolean;
}

export interface AutoSyncInput {
  accounts: readonly AutoSyncAccount[] | null;
  extensionState: "checking" | "connected" | "outdated" | "not-installed" | "unknown";
  apiRunning: boolean | null;
  /** 已有同步队列在跑（手动点击或上一次自动同步）。 */
  syncing: boolean;
  /**
   * 距上一次自动同步尝试的毫秒数；null 表示本次页面生命周期内还没试过。
   * 打开程序 / 回到页面 / 状态变化都会重新评估，但两次尝试之间有冷却，
   * 避免为一个始终无法同步的平台反复启动浏览器。
   */
  msSinceLastAttempt: number | null;
  /** 两次自动同步的最小间隔（默认 AUTO_SYNC_COOLDOWN_MS）。 */
  cooldownMs?: number;
}

/** 两次自动同步尝试之间的最小间隔。 */
export const AUTO_SYNC_COOLDOWN_MS = 30_000;

export type AutoSyncSkipReason =
  | "cooldown"
  | "already_syncing"
  | "accounts_loading"
  | "api_unavailable"
  | "extension_outdated"
  | "extension_not_connected"
  | "nothing_to_sync";

export interface AutoSyncDecision {
  run: boolean;
  /** 需要同步的平台（固定顺序 xhs → douyin → bilibili → zhihu）。 */
  platforms: PlatformSlug[];
  skipReason?: AutoSyncSkipReason;
}

/**
 * 需要同步的平台 = 尚未"已验证登录"（status==="connected" 且 verified）的平台。
 *
 * 注意这里**不**区分 unverified / expired / disconnected：只要后端不能确认
 * 这个平台已登录，就值得让扩展重新读一次当前浏览器的会话 —— 用户刚在浏览器
 * 里登录完正是这种状态。抖音同样参与：它虽然可以匿名搜索，但登录后能拿到
 * 更完整的结果，且同步失败不会阻断匿名搜索。
 */
export function platformsNeedingSync(
  accounts: readonly AutoSyncAccount[] | null
): PlatformSlug[] {
  if (!accounts) return [];
  const need = new Set(
    accounts.filter((a) => !isAccountVerified(a)).map((a) => a.platform)
  );
  return BULK_SYNC_PLATFORM_ORDER.filter((p) => need.has(p));
}

/**
 * 自动同步决策（打开程序、回到页面、账号状态变化时都会评估）：
 * - 冷却未到 → 不重复尝试（cooldown）；
 * - 已有队列在跑 → 不叠加（already_syncing）；
 * - API 不可用 / 扩展未连接 / 扩展过旧 → 不启动（这些需要用户先处理）；
 * - 四个平台全部已验证登录 → 什么都不做（nothing_to_sync）—— 平时打开程序
 *   不会产生任何额外请求或浏览器启动。
 */
export function decideAutoSync(input: AutoSyncInput): AutoSyncDecision {
  const {
    accounts, extensionState, apiRunning, syncing,
    msSinceLastAttempt, cooldownMs = AUTO_SYNC_COOLDOWN_MS,
  } = input;
  if (syncing) return { run: false, platforms: [], skipReason: "already_syncing" };
  if (msSinceLastAttempt !== null && msSinceLastAttempt < cooldownMs) {
    return { run: false, platforms: [], skipReason: "cooldown" };
  }
  if (apiRunning !== true) return { run: false, platforms: [], skipReason: "api_unavailable" };
  if (extensionState === "outdated") {
    return { run: false, platforms: [], skipReason: "extension_outdated" };
  }
  if (extensionState !== "connected") {
    return { run: false, platforms: [], skipReason: "extension_not_connected" };
  }
  if (!accounts) return { run: false, platforms: [], skipReason: "accounts_loading" };
  const platforms = platformsNeedingSync(accounts);
  if (platforms.length === 0) {
    return { run: false, platforms: [], skipReason: "nothing_to_sync" };
  }
  return { run: true, platforms };
}

// ── 启动后"重新验证已有登录状态"（方案 A：不依赖扩展）──────────────────
//
// 后端把"已验证"存在**内存**里，重启就没了 —— profile 还在，但状态退化成
// "已导入，未确认登录"（accounts.py 的 _state_of：profile 存在只代表
// unverified，connected 必须来自真实验证）。装了扩展时，Round 18 的自动同步
// 会顺带把状态验回来；没装扩展（扫码登录主路径）时没人做这件事，账号卡片就
// 一直停在"未确认"。这里负责补上：对"本地有 profile 但未确认"的平台，让后端
// 用 profile 起无头上下文跑一次 pong，把结论验回来。

/**
 * 需要"重新验证"的平台：本地有 profile，但后端未确认登录（固定顺序）。
 *
 * 刻意**不**包含没有 profile 的平台：那种情况验证只会得到"未登录"，
 * 用户要做的是扫码登录或从浏览器同步，不是验证。
 */
export function platformsNeedingVerify(
  accounts: readonly AutoSyncAccount[] | null
): PlatformSlug[] {
  if (!accounts) return [];
  const need = new Set(
    accounts
      .filter((a) => a.profile_exists === true && !isAccountVerified(a))
      .map((a) => a.platform)
  );
  return BULK_SYNC_PLATFORM_ORDER.filter((p) => need.has(p));
}

export type StartupVerifySkipReason =
  | "cooldown"
  | "already_syncing"
  | "accounts_loading"
  | "api_unavailable"
  | "nothing_to_verify"
  /** 扩展已连接：交给原来的扩展同步路径，别重复劳动。 */
  | "extension_connected";

export interface StartupVerifyInput {
  accounts: readonly AutoSyncAccount[] | null;
  apiRunning: boolean | null;
  /** 已有同步/验证队列在跑。 */
  syncing: boolean;
  /** 距上一次尝试的毫秒数（与自动同步共用同一冷却）。 */
  msSinceLastAttempt: number | null;
  /** 扩展状态：connected 时不重复验证（同步会带上验证）。 */
  extensionState?: "checking" | "connected" | "outdated" | "not-installed" | "unknown";
  cooldownMs?: number;
}

export interface StartupVerifyDecision {
  run: boolean;
  platforms: PlatformSlug[];
  skipReason?: StartupVerifySkipReason;
}

/**
 * 启动后是否需要"重新验证已有登录状态"。
 *
 * 与 decideAutoSync 共用同一套冷却时间戳（调用方传同一个 msSinceLastAttempt），
 * 所以两条路径不会在同一轮里重复启动浏览器。
 */
export function decideStartupVerify(input: StartupVerifyInput): StartupVerifyDecision {
  const {
    accounts, apiRunning, syncing, msSinceLastAttempt,
    extensionState, cooldownMs = AUTO_SYNC_COOLDOWN_MS,
  } = input;
  if (syncing) return { run: false, platforms: [], skipReason: "already_syncing" };
  if (msSinceLastAttempt !== null && msSinceLastAttempt < cooldownMs) {
    return { run: false, platforms: [], skipReason: "cooldown" };
  }
  if (apiRunning !== true) return { run: false, platforms: [], skipReason: "api_unavailable" };
  if (extensionState === "connected") {
    return { run: false, platforms: [], skipReason: "extension_connected" };
  }
  if (!accounts) return { run: false, platforms: [], skipReason: "accounts_loading" };
  const platforms = platformsNeedingVerify(accounts);
  if (platforms.length === 0) {
    return { run: false, platforms: [], skipReason: "nothing_to_verify" };
  }
  return { run: true, platforms };
}

/**
 * "重新验证"是否值得提示用户。
 *
 * 全部确认成功是**预期结果**（重开程序的正常路径）—— 每次都弹一条
 * "N 个平台验证成功"是纯噪音。只有出现需要处理的情况（过期/不可用/失败/
 * 仍未确认）才提示。
 */
export function shouldAnnounceStartupVerify(counts: BulkOutcomeCounts): boolean {
  if (counts.total === 0) return false;
  return counts.verified < counts.total;
}

/** "重新验证"的汇总文案（用"复核"措辞，不说"同步"——这次并没有导入 Cookie）。 */
export function buildVerifySummaryMessage(counts: BulkOutcomeCounts): BulkSummaryMessage {
  const pending = counts.imported + counts.verifying;
  const parts = [`${counts.verified} 个已确认`, `${pending} 个仍未确认`];
  if (counts.unavailable > 0) parts.push(`${counts.unavailable} 个暂不可用`);
  if (counts.failed > 0) parts.push(`${counts.failed} 个失败`);
  const problematic = counts.failed > 0 || counts.unavailable > 0 || pending > 0;
  return {
    tone: problematic ? "warning" : "info",
    title: `登录状态复核：${parts.join("，")}`,
    ...(problematic ? { description: "请查看对应平台卡片诊断" } : {}),
  };
}
