import { PLATFORM_SLUGS } from "./platformMeta.js";

const STATUS = ["pending", "running", "succeeded", "empty", "login_required", "rate_limited", "timed_out", "failed", "cancelled", "cancelling", "completed", "partial"];
const ACCOUNT_STATUS = ["connected", "unverified", "expired", "failed", "unavailable", "verifying", "syncing", "disconnected"];
const ACCOUNT_ERRORS = ["login_required", "login_not_verified", "login_verification_failed", "login_verification_unavailable", "login_verification_rate_limited", "session_import_failed", "browser_not_found", "profile_in_use", "account_busy"];
const PROVIDERS = ["session_api", "light_api", "browser", "page_api", "public_search"];

interface DiagnosticRead {
  state: "available" | "empty" | "unavailable";
  http_status: number | null;
  data?: unknown;
}
export interface DiagnosticSources {
  health: DiagnosticRead;
  accounts: DiagnosticRead;
  search: DiagnosticRead;
  favorites: DiagnosticRead;
}

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function choice(value: unknown, allowed: readonly string[]): string | null {
  return typeof value === "string" && allowed.includes(value) ? value : null;
}
function flag(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}
function timestamp(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? new Date(time).toISOString() : null;
}
function version(value: unknown): string | null {
  return typeof value === "string" && /^\d{1,4}\.\d{1,4}\.\d{1,4}$/.test(value) ? value : null;
}
function usage(value: unknown) {
  const info = object(value);
  return { status: choice(info.status, STATUS), checked_at: timestamp(info.checked_at) };
}
function jobSummary(value: unknown) {
  if (value == null) return null;
  const job = object(value);
  const platforms = object(job.platforms);
  return {
    status: choice(job.overall, STATUS), created_at: timestamp(job.created_at),
    completed_at: timestamp(job.completed_at), total_ms: count(job.total_ms),
    persistence_error_reported: typeof job.persistence_error === "string" && job.persistence_error.length > 0,
    platforms: Object.fromEntries(PLATFORM_SLUGS.filter((platform) => Object.prototype.hasOwnProperty.call(platforms, platform)).map((platform) => {
      const info = object(platforms[platform]);
      const timing = object(info.timings);
      return [platform, {
        status: choice(info.status, STATUS), result_count: count(info.result_count),
        fetched_at: timestamp(info.fetched_at), synced_at: timestamp(info.synced_at),
        cache_hit: flag(info.cache_hit), cooldown_until: timestamp(info.cooldown_until),
        cooldown_skipped: flag(info.cooldown_skipped),
        first_result_ms: count(timing.first_result_ms), total_ms: count(timing.total_ms),
        browser_launch_ms: count(timing.browser_launch_ms), provider: choice(timing.provider_used, PROVIDERS),
      }];
    })),
  };
}

/** Rebuild the report from allowed fields; never serialize a response or error object. */
export function buildSupportReport(sources: DiagnosticSources, uiVersion: string, userAgent: string, now = new Date()): string {
  const health = object(sources.health.data);
  const accounts = object(sources.accounts.data).accounts;
  const list = Array.isArray(accounts) ? accounts : [];
  const report = {
    product: "SiYe", report_version: 1, captured_at: now.toISOString(), ui_version: version(uiVersion),
    browser: /Edg\//.test(userAgent) ? "Edge" : /Chrome\//.test(userAgent) ? "Chrome" : /Firefox\//.test(userAgent) ? "Firefox" : "other",
    os: /Windows/.test(userAgent) ? "Windows" : /Android/.test(userAgent) ? "Android" : /Macintosh/.test(userAgent) ? "macOS" : /Linux/.test(userAgent) ? "Linux" : "other",
    reads: Object.fromEntries((["health", "accounts", "search", "favorites"] as const).map((key) => [key, {
      state: choice(sources[key].state, ["available", "empty", "unavailable"]), http_status: count(sources[key].http_status),
    }])),
    environment: {
      status: choice(health.environment_status, ["ok", "degraded"]), api_version: version(health.api_version),
      web_version: version(health.web_version), version_match: flag(health.version_match),
      browser_available: flag(health.browser_available), browser_backend: choice(health.browser_backend, ["chrome", "msedge", "edge", "chromium", "custom"]),
      redis_required: flag(health.redis_required), redis_available: flag(health.redis_available),
    },
    accounts: Object.fromEntries(PLATFORM_SLUGS.map((platform) => {
      const account = object(list.find((item) => object(item).platform === platform));
      const verification = object(account.verification);
      const operations = object(account.usage);
      return [platform, {
        status: choice(account.status, ACCOUNT_STATUS), verified: flag(account.verified),
        profile_exists: flag(account.profile_exists), last_verified_at: timestamp(account.last_verified_at),
        error_code: account.safe_error_code == null ? null : choice(account.safe_error_code, ACCOUNT_ERRORS) ?? "unrecognized_error",
        verification_status: choice(verification.status, ["verified", "unverified", "unavailable", "rate_limited", "login_required", "failed", "connected"]),
        verification_checked_at: timestamp(verification.checked_at),
        last_search: usage(operations.search), last_favorites_sync: usage(operations.favorites),
      }];
    })),
    latest_search: jobSummary(sources.search.data), latest_favorites_sync: jobSummary(sources.favorites.data),
  };
  return JSON.stringify(report, null, 2);
}

/** Only local reads, bounded individually so one failing endpoint cannot block the report. */
export async function collectSupportDiagnostics(fetcher: typeof fetch = fetch, timeoutMs = 5000): Promise<DiagnosticSources> {
  const endpoints = {
    health: "/api/health", accounts: "/api/search/accounts",
    search: "/api/search/jobs/current", favorites: "/api/search/favorites/jobs/latest",
  } as const;
  const entries = await Promise.all(Object.entries(endpoints).map(async ([key, url]) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let result: DiagnosticRead;
    try {
      const response = await fetcher(url, { method: "GET", cache: "no-store", signal: controller.signal });
      if (response.status === 404 && key === "favorites") result = { state: "empty", http_status: 404 };
      else if (!response.ok) result = { state: "unavailable", http_status: response.status };
      else {
        const data: unknown = await response.json();
        result = { state: data == null ? "empty" : "available", http_status: response.status, data };
      }
    } catch { result = { state: "unavailable", http_status: null }; }
    finally { clearTimeout(timeout); }
    return [key, result];
  }));
  return Object.fromEntries(entries) as DiagnosticSources;
}
