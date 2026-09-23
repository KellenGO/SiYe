/**
 * 应用自带扫码登录（方案 A）测试 —— 直接 import 编译后的生产模块
 * （webui/src/lib/scanLogin.ts），不复制任何生产逻辑。
 *
 * 覆盖：
 * - 接口路径与后端 api/routers/search.py 的 prefix 对齐；
 * - pending/running 为进行中，succeeded/failed/timed_out 为终态（轮询停止条件）；
 * - 状态 → 界面文案/色调/是否结束 的映射（含未知状态与空任务的兜底）；
 * - 按钮文案：进行中禁止重复提交、终态后允许重新登录；
 * - POST 失败的文案翻译：409（搜索进行中 / 已有登录 / 其他账号操作）、422、无响应、其他；
 * - 所有文案不含 Cookie/ticket/header/响应体等敏感内容。
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  SCAN_LOGIN_ACTIVE_TTL_MS,
  SCAN_LOGIN_API_BASE,
  SCAN_LOGIN_POLL_INTERVAL_MS,
  SCAN_LOGIN_TOTAL_TIMEOUT_MS,
  activeScanLoginPlatforms,
  clearAllScanLogins,
  clearScanLoginActive,
  hasAnyVerifiedAccount,
  isAnyScanLoginActive,
  isScanLoginActive,
  isScanLoginTerminal,
  markScanLoginActive,
  pendingScanLoginJob,
  resetScanLoginActiveForTests,
  scanLoginActionLabel,
  scanLoginErrorMessage,
  scanLoginView,
  type ScanLoginJob,
} from "../src/lib/scanLogin.js";

const SENSITIVE = ["cookie", "ticket", "authorization", "set-cookie", "traceback", "response"];

const job = (over: Partial<ScanLoginJob> = {}): ScanLoginJob => ({
  job_id: "abc123",
  platform: "xhs",
  status: "running",
  message: "",
  ...over,
});

/** 构造一个 axios 风格错误（只看 response.status / data）。 */
const httpError = (status: number, data?: { safe_message?: string; detail?: string }) => ({
  response: { status, data },
});

// ── 协议常量 ───────────────────────────────────────────────────────────

test("登录接口路径与后端 router prefix 一致", () => {
  assert.equal(SCAN_LOGIN_API_BASE, "/api/search/login");
});

test("轮询间隔为正且在后端超时窗口内", () => {
  assert.ok(SCAN_LOGIN_POLL_INTERVAL_MS > 0);
  assert.ok(SCAN_LOGIN_POLL_INTERVAL_MS <= 5000);
});

// ── 状态判定 ───────────────────────────────────────────────────────────

test("isScanLoginActive：只有 pending/running 是进行中", () => {
  for (const s of ["pending", "running"]) assert.equal(isScanLoginActive(s), true, s);
  for (const s of ["succeeded", "failed", "timed_out", "", "unknown"]) {
    assert.equal(isScanLoginActive(s), false, s);
  }
  assert.equal(isScanLoginActive(undefined), false);
  assert.equal(isScanLoginActive(null), false);
});

test("isScanLoginTerminal：succeeded/failed/timed_out 为终态", () => {
  for (const s of ["succeeded", "failed", "timed_out"]) {
    assert.equal(isScanLoginTerminal(s), true, s);
  }
  for (const s of ["pending", "running", "", "unknown"]) {
    assert.equal(isScanLoginTerminal(s), false, s);
  }
  assert.equal(isScanLoginTerminal(undefined), false);
});

test("进行中与终态互斥，且覆盖全部已知状态", () => {
  for (const s of ["pending", "running", "succeeded", "failed", "timed_out"]) {
    assert.notEqual(isScanLoginActive(s), isScanLoginTerminal(s), s);
  }
});

// ── 状态 → 界面视图 ────────────────────────────────────────────────────

test("scanLoginView：无任务时为空状态", () => {
  const v = scanLoginView(null);
  assert.equal(v.tone, "idle");
  assert.equal(v.text, "");
  assert.equal(v.done, true);
  assert.deepEqual(scanLoginView(undefined), v);
});

test("scanLoginView：进行中为 active 且未结束", () => {
  const pending = scanLoginView(job({ status: "pending", message: "" }));
  assert.equal(pending.tone, "active");
  assert.equal(pending.done, false);
  assert.ok(pending.text.length > 0);

  const running = scanLoginView(job({ status: "running", message: "浏览器窗口已打开，请扫码登录。" }));
  assert.equal(running.tone, "active");
  assert.equal(running.done, false);
  assert.equal(running.text, "浏览器窗口已打开，请扫码登录。");
});

test("scanLoginView：成功为 ok 且结束（优先使用后端文案）", () => {
  const v = scanLoginView(job({ status: "succeeded", message: "登录成功，会话已验证并保存。" }));
  assert.equal(v.tone, "ok");
  assert.equal(v.done, true);
  assert.equal(v.text, "登录成功，会话已验证并保存。");
});

test("scanLoginView：失败/超时为 bad 且结束", () => {
  const failed = scanLoginView(job({ status: "failed", message: "登录验证失败，请重试" }));
  assert.equal(failed.tone, "bad");
  assert.equal(failed.done, true);
  assert.equal(failed.text, "登录验证失败，请重试");

  const timeout = scanLoginView(job({ status: "timed_out", message: "" }));
  assert.equal(timeout.tone, "bad");
  assert.equal(timeout.done, true);
  assert.ok(timeout.text.includes("超时"));
});

test("scanLoginView：后端未给 message 时仍有兜底文案", () => {
  for (const s of ["pending", "running", "succeeded", "failed", "timed_out"]) {
    const v = scanLoginView(job({ status: s, message: "" }));
    assert.ok(v.text.length > 0, `${s} 必须有兜底文案`);
    assert.equal(v.done, s !== "pending" && s !== "running");
    for (const sensitive of SENSITIVE) {
      assert.ok(!v.text.toLowerCase().includes(sensitive), `${s} 文案不得包含 ${sensitive}`);
    }
  }
});

test("scanLoginView：未知状态按结束处理，不假装还在进行中", () => {
  const v = scanLoginView(job({ status: "something_new", message: "后端新状态" }));
  assert.equal(v.tone, "idle");
  assert.equal(v.done, true);
});

// ── 按钮文案 ───────────────────────────────────────────────────────────

test("scanLoginActionLabel：未尝试过为「扫码登录」", () => {
  assert.equal(scanLoginActionLabel(undefined), "扫码登录");
  assert.equal(scanLoginActionLabel(null), "扫码登录");
});

test("scanLoginActionLabel：进行中禁止重复提交，终态后允许重新登录", () => {
  assert.equal(scanLoginActionLabel(job({ status: "pending" })), "登录进行中…");
  assert.equal(scanLoginActionLabel(job({ status: "running" })), "登录进行中…");
  assert.equal(scanLoginActionLabel(job({ status: "succeeded" })), "重新扫码登录");
  assert.equal(scanLoginActionLabel(job({ status: "failed" })), "重新扫码登录");
  assert.equal(scanLoginActionLabel(job({ status: "timed_out" })), "重新扫码登录");
});

test("pendingScanLoginJob：本地占位任务处于进行中且还没有 job_id", () => {
  const p = pendingScanLoginJob("douyin");
  assert.equal(p.platform, "douyin");
  assert.equal(p.status, "pending");
  assert.equal(p.job_id, "");
  assert.equal(isScanLoginActive(p.status), true);
  assert.equal(isScanLoginTerminal(p.status), false);
  assert.ok(p.message.length > 0);
});

// ── POST 失败的文案翻译 ────────────────────────────────────────────────

test("scanLoginErrorMessage：搜索进行中（409）引导等待搜索结束", () => {
  const msg = scanLoginErrorMessage(httpError(409, {
    detail: "A search job is running. Wait for it to complete before logging in.",
  }));
  assert.ok(msg.includes("搜索"));
  assert.ok(!msg.includes("A search job is running"), "不得回显后端原始 detail");
  for (const s of SENSITIVE) assert.ok(!msg.toLowerCase().includes(s));
});

test("scanLoginErrorMessage：已有登录在进行（409）提示先完成或关闭", () => {
  const msg = scanLoginErrorMessage(httpError(409, {
    detail: "A login session is already active.",
  }));
  assert.ok(msg.includes("登录窗口"));
  assert.ok(!msg.includes("already active"), "不得回显后端原始 detail");
});

test("scanLoginErrorMessage：其他账号操作进行中（409，中文 detail）透出安全文案", () => {
  const msg = scanLoginErrorMessage(httpError(409, {
    detail: "账号操作进行中，请等待完成后再登录。",
  }));
  assert.ok(msg.includes("账号操作进行中"));
});

test("scanLoginErrorMessage：409 无 detail 时也有兜底文案", () => {
  const msg = scanLoginErrorMessage(httpError(409));
  assert.ok(msg.length > 0);
});

test("scanLoginErrorMessage：优先使用 safe_message", () => {
  const msg = scanLoginErrorMessage(httpError(400, {
    safe_message: "平台参数不被接受",
    detail: "should not be used",
  }));
  assert.equal(msg, "平台参数不被接受");
});

test("scanLoginErrorMessage：422 提示平台不支持", () => {
  const msg = scanLoginErrorMessage(httpError(422, { detail: "Invalid platform: foo" }));
  assert.ok(msg.includes("不支持"));
  assert.ok(!msg.includes("Invalid platform"));
});

test("scanLoginErrorMessage：没有 response（网络/应用未启动）给可操作提示", () => {
  const msg = scanLoginErrorMessage(new Error("Network Error"));
  assert.ok(msg.includes("重新启动应用"));
});

test("scanLoginErrorMessage：其他 HTTP 状态带状态码但不含敏感内容", () => {
  const msg = scanLoginErrorMessage(httpError(500));
  assert.ok(msg.includes("500"));
  for (const s of SENSITIVE) assert.ok(!msg.toLowerCase().includes(s));
});

test("scanLoginErrorMessage：异常对象形状不确定时也不抛错", () => {
  for (const err of [null, undefined, {}, "boom", { response: {} }]) {
    const msg = scanLoginErrorMessage(err);
    assert.equal(typeof msg, "string");
    assert.ok(msg.length > 0);
  }
});

// ── 首次使用提示判定 ───────────────────────────────────────────────────

test("hasAnyVerifiedAccount：只有 connected + verified 才算已登录", () => {
  assert.equal(hasAnyVerifiedAccount(null), false);
  assert.equal(hasAnyVerifiedAccount([]), false);
  assert.equal(hasAnyVerifiedAccount([
    { status: "unverified", verified: false },
    { status: "connected", verified: false },
    { status: "expired", verified: false },
  ]), false);
  assert.equal(hasAnyVerifiedAccount([
    { status: "connected", verified: true },
    { status: "unverified", verified: false },
  ]), true);
});

// ── 进行中登录的登记（搜索侧避让）──────────────────────────────────────

test("登录登记：mark 后可被搜索侧看到，clear 后消失", () => {
  resetScanLoginActiveForTests();
  assert.equal(isAnyScanLoginActive(0), false);
  markScanLoginActive("xhs", 1000);
  assert.equal(isAnyScanLoginActive(1000), true);
  assert.deepEqual(activeScanLoginPlatforms(), ["xhs"]);
  markScanLoginActive("douyin", 1000);
  assert.deepEqual(activeScanLoginPlatforms(), ["douyin", "xhs"]);
  clearScanLoginActive("xhs");
  assert.equal(isAnyScanLoginActive(1000), true);
  assert.deepEqual(activeScanLoginPlatforms(), ["douyin"]);
  clearScanLoginActive("douyin");
  assert.equal(isAnyScanLoginActive(1000), false);
  resetScanLoginActiveForTests();
});

test("登录登记：过期自动失效，不会永久拖慢搜索", () => {
  resetScanLoginActiveForTests();
  markScanLoginActive("zhihu", 1000);
  const justBefore = 1000 + SCAN_LOGIN_ACTIVE_TTL_MS - 1;
  assert.equal(isAnyScanLoginActive(justBefore), true);
  const afterExpiry = 1000 + SCAN_LOGIN_ACTIVE_TTL_MS + 1;
  assert.equal(isAnyScanLoginActive(afterExpiry), false);
  assert.deepEqual(activeScanLoginPlatforms(), [], "过期条目应被顺带清理");
  resetScanLoginActiveForTests();
});

test("登录登记：TTL 必须长于后端登录窗口（不能比任务先过期）", () => {
  assert.ok(SCAN_LOGIN_ACTIVE_TTL_MS > SCAN_LOGIN_TOTAL_TIMEOUT_MS);
});

test("登录登记：clearAllScanLogins 清空全部（离开账号页的场景）", () => {
  resetScanLoginActiveForTests();
  markScanLoginActive("xhs", 0);
  markScanLoginActive("zhihu", 0);
  assert.equal(isAnyScanLoginActive(0), true);
  clearAllScanLogins();
  assert.equal(isAnyScanLoginActive(0), false);
  assert.deepEqual(activeScanLoginPlatforms(), []);
});

test("登录登记：多个平台互不影响，清除一个不影响另一个", () => {
  resetScanLoginActiveForTests();
  markScanLoginActive("xhs", 0);
  markScanLoginActive("bilibili", 0);
  clearScanLoginActive("xhs");
  assert.deepEqual(activeScanLoginPlatforms(), ["bilibili"]);
  resetScanLoginActiveForTests();
});
