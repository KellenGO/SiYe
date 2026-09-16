import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSupportReport, collectSupportDiagnostics, type DiagnosticSources } from "../src/lib/supportDiagnostics.js";

function sources(): DiagnosticSources {
  return {
    health: { state: "available", http_status: 200, data: { api_version: "0.2.1", browser_available: true, environment_status: "ok" } },
    accounts: { state: "available", http_status: 200, data: { accounts: [] } },
    search: { state: "empty", http_status: 200, data: null },
    favorites: { state: "empty", http_status: 404 },
  };
}

test("报告仅保留已知字段，剔除所有响应中的个人内容与任意错误原文", () => {
  const input = sources();
  const secret = "PRIVATE_COOKIE_AND_QUERY";
  input.health.data = { api_version: "0.2.1", browser_backend: `C:/Users/${secret}/chrome.exe`, secret };
  input.accounts.data = { accounts: [{ platform: "xhs", status: "connected", verified: true,
    display_name: secret, cookies: secret, safe_message: secret, safe_error_code: secret,
    last_verified_at: "2026-09-15T15:00:00+08:00", diagnostic: { user_message: secret },
    usage: { search: { status: "succeeded", checked_at: "2026-09-15T08:00:00Z", secret } } }] };
  input.search = { state: "available", http_status: 200, data: { job_id: secret, keyword: secret,
    overall: "partial", error: secret, results: [{ title: secret, url: secret }],
    platforms: { xhs: { status: "rate_limited", error_summary: secret, result_count: 0,
      timings: { total_ms: 1500, first_result_ms: null, provider_used: secret, secret } },
      [secret]: { status: secret } } } };
  input.favorites = { state: "available", http_status: 200, data: { results: [{ title: secret }], persistence_error: secret } };
  const text = buildSupportReport(input, "0.2.1", `Windows Edg/140.0 ${secret}`);
  assert.ok(!text.includes(secret));
  const report = JSON.parse(text);
  assert.equal(report.accounts.xhs.verified, true);
  assert.equal(report.accounts.xhs.last_verified_at, "2026-09-15T07:00:00.000Z");
  assert.equal(report.accounts.xhs.error_code, "unrecognized_error");
  assert.equal(report.latest_search.platforms.xhs.total_ms, 1500);
  assert.equal(report.latest_search.platforms.xhs.provider, null);
  assert.equal(report.latest_favorites_sync.persistence_error_reported, true);
  assert.equal(report.environment.browser_backend, null);
});

test("缺失、类型错误和非法数字显示未知，空任务不冒充成功", () => {
  const input = sources();
  input.health.data = { api_version: "secret", browser_available: "true" };
  input.accounts.data = { accounts: "invalid" };
  input.search.data = { created_at: "not a date", platforms: { xhs: { result_count: -1, timings: { total_ms: Infinity, first_result_ms: true } } } };
  const report = JSON.parse(buildSupportReport(input, "0.2.1", ""));
  assert.equal(report.accounts.xhs.verified, null);
  assert.equal(report.environment.browser_available, null);
  assert.equal(report.latest_search.platforms.xhs.total_ms, null);
  assert.equal(report.latest_search.platforms.xhs.first_result_ms, null);
  assert.equal(report.latest_search.platforms.xhs.result_count, null);
  assert.equal(report.latest_favorites_sync, null);
});

test("只请求四个本机只读端点，单项失败仍生成其余报告", async () => {
  const requests: string[] = [];
  const fetcher: typeof fetch = async (url, options) => {
    const path = String(url);
    requests.push(path);
    assert.equal(options?.method, "GET");
    assert.ok(path.startsWith("/api/"));
    if (path.endsWith("accounts")) throw new Error("PRIVATE_NETWORK_ERROR");
    if (path.endsWith("latest")) return new Response("PRIVATE_ERROR_BODY", { status: 404 });
    return Response.json(path.endsWith("current") ? null : { api_version: "0.2.1" });
  };
  const result = await collectSupportDiagnostics(fetcher);
  assert.equal(requests.length, 4);
  assert.equal(result.health.state, "available");
  assert.equal(result.accounts.state, "unavailable");
  assert.equal(result.search.state, "empty");
  assert.equal(result.favorites.state, "empty");
  assert.ok(!JSON.stringify(result).includes("PRIVATE"));
});

test("后端离线时，报告仍包含前端版本和浏览器类型", async () => {
  const fetcher: typeof fetch = async () => { throw new Error("offline"); };
  const report = JSON.parse(buildSupportReport(await collectSupportDiagnostics(fetcher), "0.2.1", "Windows Chrome/140.0"));
  assert.equal(report.ui_version, "0.2.1");
  assert.equal(report.browser, "Chrome");
  assert.equal(report.reads.health.state, "unavailable");
  assert.equal(report.environment.api_version, null);
});

test("挂起请求有超时，响应体错误不进入报告", async () => {
  const fetcher: typeof fetch = async (_url, options) => new Promise((_resolve, reject) => {
    options?.signal?.addEventListener("abort", () => reject(new Error("PRIVATE_TIMEOUT")), { once: true });
  });
  const result = await collectSupportDiagnostics(fetcher, 10);
  assert.ok(Object.values(result).every((entry) => entry.state === "unavailable"));
  const invalid: typeof fetch = async () => new Response("PRIVATE_INVALID_JSON", { status: 200 });
  assert.ok(!JSON.stringify(await collectSupportDiagnostics(invalid)).includes("PRIVATE"));
});
