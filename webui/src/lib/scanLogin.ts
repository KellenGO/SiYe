/**
 * 应用自带扫码登录协议（方案 A：主路径，无 React 依赖）。
 *
 * 与浏览器扩展同步的区别（这是把扫码登录提为主路径的原因）：
 * - 不依赖任何浏览器扩展、不需要开「开发者模式」、不需要刷新页面；
 * - 后端在**用户自己的 Edge/Chrome** 里打开一个可见窗口，用手机 App 扫码；
 * - 扫码成功后会话直接写入搜索实际读取的那个 profile 目录
 *   （`browser_data/<platform>_user_data_dir`），并做一次真实验证
 *   （平台自己的 pong / check_login_state），只有验证通过才报 succeeded。
 *
 * 后端接口已存在，本模块只做协议与纯逻辑，不新增后端：
 * - `POST /api/search/login`            → { job_id, platform, status, message }
 * - `GET  /api/search/login/{job_id}`   → 同上 + created_at / completed_at
 *
 * 排他语义（后端 operation coordinator）：登录与搜索、账号同步互斥 ——
 * 已有搜索或登录在跑时，`POST` 返回 409，登录窗口不会启动。文案由
 * `scanLoginErrorMessage` 统一翻译为安全、可操作的中文提示。
 *
 * 本模块不持有任何凭据：请求体只有 platform，响应里只有 job_id 与状态文案。
 */

/** 搜索/登录接口前缀（与 api/routers/search.py 的 prefix 一致）。 */
export const SEARCH_API_BASE = "/api/search";

/** 扫码登录接口。 */
export const SCAN_LOGIN_API_BASE = `${SEARCH_API_BASE}/login`;

/**
 * 登录任务轮询间隔。后端登录窗口最长等 10 分钟（LOGIN_TOTAL_TIMEOUT=620s），
 * 1.5 秒一次既能及时反馈状态，也不会给本地服务造成压力。
 */
export const SCAN_LOGIN_POLL_INTERVAL_MS = 1500;

/** 与后端 LOGIN_TOTAL_TIMEOUT 对齐的超时说明（用于界面提示，不做前端计时）。 */
export const SCAN_LOGIN_TOTAL_TIMEOUT_MS = 620_000;

/**
 * 后端登录任务的状态集合（api/routers/search.py 的 status 字段）。
 * pending/running 为进行中，其余三个为终态。
 */
export type ScanLoginStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "timed_out";

/** 登录任务（POST 与 GET 的响应字段，GET 另有 created_at/completed_at，此处不需要）。 */
export interface ScanLoginJob {
  job_id: string;
  platform: string;
  status: string;
  message: string;
}

const ACTIVE_STATUSES: ReadonlySet<string> = new Set(["pending", "running"]);
const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "succeeded",
  "failed",
  "timed_out",
]);

/** 进行中（pending/running）—— 期间按钮必须禁用，避免重复申请排他租约。 */
export function isScanLoginActive(status: string | undefined | null): boolean {
  return typeof status === "string" && ACTIVE_STATUSES.has(status);
}

/** 终态（succeeded/failed/timed_out）—— 到达终态后不再轮询。 */
export function isScanLoginTerminal(status: string | undefined | null): boolean {
  return typeof status === "string" && TERMINAL_STATUSES.has(status);
}

/** 尚未提交成功的本地占位任务（POST 未返回时先让界面有反馈）。 */
export function pendingScanLoginJob(
  platform: string,
  message = "正在启动登录窗口…"
): ScanLoginJob {
  return { job_id: "", platform, status: "pending", message };
}

/** 阶段色调：idle 无状态、active 进行中、ok 成功、bad 失败。 */
export type ScanLoginTone = "idle" | "active" | "ok" | "bad";

export interface ScanLoginView {
  tone: ScanLoginTone;
  /** 展示给用户的单行状态文案（空串 = 还没有任何状态可展示）。 */
  text: string;
  /** 是否已到终态（用于按钮恢复可用）。 */
  done: boolean;
}

/**
 * 把登录任务映射为界面状态（纯函数）。文案优先使用后端 message ——
 * 后端已经是安全文案（不含 Cookie/traceback），且能反映真实阶段。
 */
export function scanLoginView(job: ScanLoginJob | undefined | null): ScanLoginView {
  if (!job) return { tone: "idle", text: "", done: true };
  if (job.status === "succeeded") {
    return {
      tone: "ok",
      text: job.message || "登录成功，会话已验证并保存。",
      done: true,
    };
  }
  if (job.status === "failed") {
    return {
      tone: "bad",
      text: job.message || "登录失败，请重试。",
      done: true,
    };
  }
  if (job.status === "timed_out") {
    return {
      tone: "bad",
      text: job.message || "登录超时（10 分钟）。可重新打开登录窗口。",
      done: true,
    };
  }
  if (job.status === "running") {
    return {
      tone: "active",
      text: job.message || "浏览器窗口已打开，请用手机扫码登录。",
      done: false,
    };
  }
  if (job.status === "pending") {
    return {
      tone: "active",
      text: job.message || "登录已排队…",
      done: false,
    };
  }
  return { tone: "idle", text: job.message || "", done: true };
}

/** 按钮文案：终态（或从未尝试）时是「扫码登录」，进行中提示等待。 */
export function scanLoginActionLabel(job: ScanLoginJob | undefined | null): string {
  if (isScanLoginActive(job?.status)) return "登录进行中…";
  if (job && job.job_id) return "重新扫码登录";
  return "扫码登录";
}

/**
 * 把 `POST /api/search/login` 的失败翻译成安全、可操作的中文提示。
 *
 * 关键场景是 409：登录与搜索/其他登录互斥（后端排他租约）。此时绝不显示
 * 原始 detail，而是说清楚"为什么不能登录、现在该做什么"。
 */
export function scanLoginErrorMessage(err: unknown): string {
  const resp = (err as {
    response?: {
      status?: number;
      data?: { safe_message?: string; detail?: string };
    };
  })?.response;
  const detail = resp?.data?.safe_message || resp?.data?.detail;
  if (!resp || resp.status === undefined) {
    return "无法连接四野，请重新启动应用并刷新页面后重试。";
  }
  if (resp.status === 409) {
    // 后端 409 的三种来源：搜索进行中 / 已有登录在进行 / 其他账号操作进行中。
    if (detail && /search/i.test(detail)) {
      return "有搜索正在进行，等这次搜索结束再扫码登录。";
    }
    if (detail && /login/i.test(detail)) {
      return "已有一个登录窗口在处理中，请先完成或关闭它再试。";
    }
    return detail || "当前有其他操作进行中，稍后再扫码登录。";
  }
  if (resp.status === 422) {
    return "该平台不支持扫码登录。";
  }
  return detail || `扫码登录请求失败（HTTP ${resp.status}），请稍后重试。`;
}

/** 一组账号里是否已有任意平台完成登录（决定首页是否提示"先登录"）。 */
export function hasAnyVerifiedAccount(
  accounts: readonly { status: string; verified: boolean }[] | null | undefined
): boolean {
  if (!accounts) return false;
  return accounts.some((a) => a.status === "connected" && a.verified === true);
}

// ── 进行中登录的进程级登记（供搜索侧避让）────────────────────────────
//
// 后端的排他租约对"搜索 vs 登录"是双向的：登录进行中提交搜索会拿到 409。
// 登录任务不写账号状态（只有扫码成功后才验证并写），所以搜索前的账号闸门
// （lib/accountGate.ts）看不到它 —— 这里维护一份模块级登记，让搜索在提交前
// 能像等待账号同步一样等待扫码登录结束。只存平台 slug，不含任何凭据。
//
// 每条登记带绝对过期时间（后端登录窗口最长 10 分钟），即使某条登记因为
// 意外路径没被清掉，也会自动失效，绝不会永久拖慢搜索。

/** 登记的有效期：后端登录总超时 + 余量。 */
export const SCAN_LOGIN_ACTIVE_TTL_MS = SCAN_LOGIN_TOTAL_TIMEOUT_MS + 30_000;

/** platform → 该登记的绝对过期时间戳（毫秒）。 */
const ACTIVE_SCAN_LOGINS: Map<string, number> = new Map();

/** 标记某平台正在扫码登录（发起请求时调用）。 */
export function markScanLoginActive(
  platform: string,
  now: number = Date.now(),
  ttlMs: number = SCAN_LOGIN_ACTIVE_TTL_MS
): void {
  ACTIVE_SCAN_LOGINS.set(platform, now + ttlMs);
}

/** 清除标记（任务到达终态、或请求失败时调用）。 */
export function clearScanLoginActive(platform: string): void {
  ACTIVE_SCAN_LOGINS.delete(platform);
}

/** 是否有任何平台正在扫码登录（搜索闸门用；过期登记自动失效）。 */
export function isAnyScanLoginActive(now: number = Date.now()): boolean {
  let active = false;
  for (const [platform, deadline] of ACTIVE_SCAN_LOGINS) {
    if (deadline <= now) {
      ACTIVE_SCAN_LOGINS.delete(platform);
      continue;
    }
    active = true;
  }
  return active;
}

/** 正在扫码登录的平台（离开账号页时清理登记、测试断言用）。 */
export function activeScanLoginPlatforms(): string[] {
  return [...ACTIVE_SCAN_LOGINS.keys()].sort();
}

/** 清空全部登记（离开账号页时调用，避免登记比轮询活得更久）。 */
export function clearAllScanLogins(): void {
  ACTIVE_SCAN_LOGINS.clear();
}

/** 仅测试用：复位模块级登记（生产代码不调用）。 */
export function resetScanLoginActiveForTests(): void {
  ACTIVE_SCAN_LOGINS.clear();
}
