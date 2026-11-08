import { useEffect, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import type { PlatformSlug, PlatformStatus as PStatus, SearchJobResponse } from "@/types/search";
import { PLATFORM_LABELS, PLATFORM_COLORS } from "@/types/search";
import { cooldownSeconds, freshnessLine, statusLine } from "@/lib/statusDisplay";
import { failureCodeFor, failureCodeInfo } from "@/lib/failureCodes";
import { PLATFORM_SLUGS } from "@/lib/platformMeta";

const PLATFORM_ORDER = PLATFORM_SLUGS;

interface PlatformStatusProps {
  response: SearchJobResponse | undefined;
  onRetry?: (platform: PlatformSlug) => void;
  retryingPlatform?: PlatformSlug | null;
  retryDisabled?: boolean;
}

const RETRYABLE_STATUSES: PStatus[] = ["failed", "timed_out", "rate_limited", "login_required"];

export function PlatformStatus({ response, onRetry, retryingPlatform, retryDisabled }: PlatformStatusProps) {
  const [nowMs, setNowMs] = useState(Date.now);
  const deadline = Math.max(0, ...Object.values(response?.platforms || {})
    .map((info) => Date.parse(info.cooldown_until || "") || 0));

  useEffect(() => {
    setNowMs(Date.now());
    if (deadline <= Date.now()) return;
    const timer = window.setInterval(() => {
      setNowMs(Date.now());
      if (deadline <= Date.now()) window.clearInterval(timer);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [deadline]);

  if (!response) return null;

  const rows = PLATFORM_ORDER.map((platform) => {
    const info = response.platforms[platform];
    if (!info) return null;
    const status = info.status;
    const remaining = cooldownSeconds(info.cooldown_until, nowMs);
    const isRetrying = retryingPlatform === platform;
    if (status === "succeeded" && response.overall !== "running" && remaining <= 0 && !isRetrying) return null;

    // 失败只显示「失败 · 错误码」：用户不需要读长原因，具体触发条件与排查
    // 方法在 `lib/failureCodes.ts` 里按码索引，主要给我们自己看。
    const code = failureCodeFor(status);
    const codeInfo = failureCodeInfo(code);
    const statusText = code ? `失败 · ${code}` : statusLine(status, info);
    const detail = [codeInfo?.short, codeInfo?.detail].filter(Boolean).join(" — ")
      || (status === "empty" && info.error_summary ? info.error_summary : statusText);
    const freshness = freshnessLine(info);
    const retryable = Boolean(onRetry && RETRYABLE_STATUSES.includes(status));

    return (
      <span key={platform} className="progress-item" title={[detail, freshness].filter(Boolean).join(" · ")}>
        <i className="pd" style={{ backgroundColor: PLATFORM_COLORS[platform] }} aria-hidden="true" />
        {PLATFORM_LABELS[platform]}
        <small>{remaining > 0 ? `冷却 ${remaining} 秒` : detail}</small>
        {isRetrying ? (
          <span className="text-link"><Loader2 className="spinner" />重试中</span>
        ) : retryable ? (
          <button
            type="button"
            disabled={retryDisabled || remaining > 0}
            onClick={() => onRetry?.(platform)}
            className="text-link"
          >
            <RotateCcw />重试
          </button>
        ) : null}
      </span>
    );
  });
  return rows.some(Boolean) ? <div className="progress-strip" aria-label="平台搜索进度">{rows}</div> : null;
}
