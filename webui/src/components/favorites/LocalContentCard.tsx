import { useState, type ComponentType } from "react";
import { Eye, FileText, ImageOff, MessageCircle, Play, Share2, Star, ThumbsUp } from "lucide-react";
import { BilibiliCoinIcon } from "@/components/icons/BilibiliCoinIcon";
import type { UnifiedSearchResult } from "@/types/search";
import { highlightSegments, orderedMetrics } from "@/lib/resultTools";
import { xhsCoverFallback } from "@/lib/localContentCover";

const CONTENT_TYPES: Record<string, string> = {
  video: "视频", short_video: "短视频", zvideo: "视频", note: "图文笔记",
  answer: "回答", article: "文章", post: "帖子",
};

const METRIC_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  view_count: Eye, like_count: ThumbsUp, coin_count: BilibiliCoinIcon,
  comment_count: MessageCircle, collect_count: Star, share_count: Share2,
};

function compactCount(value: number): string {
  if (value >= 1000000000) return `${Number((value / 1000000000).toFixed(1))}B`;
  if (value >= 1000000) return `${Number((value / 1000000).toFixed(1))}M`;
  if (value >= 1000) return `${Number((value / 1000).toFixed(1))}k`;
  return value.toLocaleString("zh-CN");
}

function durationLabel(seconds: number | null | undefined): string | null {
  if (!Number.isSafeInteger(seconds) || !seconds || seconds < 0) return null;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${String(minutes).padStart(2, "0")}:${rest}`;
}

export function localContentType(type: string): string {
  return CONTENT_TYPES[type] || "内容";
}

export function ContentMetric({ result, metric, className }: {
  result: UnifiedSearchResult;
  metric: { key: string; label: string };
  className: string;
}) {
  const video = ["video", "short_video", "zvideo"].includes(result.content_type);
  const label = metric.key === "view_count" && !video ? "阅读" : metric.label;
  const approximate = result.metrics_approximate?.includes(metric.key);
  const Icon = METRIC_ICONS[metric.key];
  const value = result.metrics[metric.key];
  const description = `${label} ${approximate ? "约 " : ""}${value.toLocaleString("zh-CN")}`;
  if (!Icon) return null;
  return <span className={className} title={description} aria-label={description}>
    <span aria-hidden="true"><Icon /></span><span aria-hidden="true">{approximate ? "≈" : ""}{compactCount(value)}</span>
  </span>;
}

export function LocalContentCover({ result, showMetric = false }: { result: UnifiedSearchResult; showMetric?: boolean }) {
  const [failedUrls, setFailedUrls] = useState<string[]>([]);
  const fallback = xhsCoverFallback(result.cover_url, result.platform);
  const cover = [result.cover_url, fallback].find((url) => url && !failedUrls.includes(url));
  const video = ["video", "short_video", "zvideo"].includes(result.content_type);
  const duration = video ? durationLabel(result.duration_seconds) : null;
  const metric = showMetric ? orderedMetrics(result.metrics, 1)[0] : undefined;
  return <span className="local-content-cover">
    {cover ? <img src={cover} alt="" loading="lazy" referrerPolicy="no-referrer"
      onError={() => setFailedUrls((urls) => [...urls, cover])} />
      : result.platform === "zhihu" ? <span className="local-content-topic">
        <span className="local-content-topic-brand">知乎 · {localContentType(result.content_type)}</span>
        <strong>{result.title || "知乎内容"}</strong>
        <span className="local-content-topic-summary">{result.snippet || result.author || "查看问题与讨论"}</span>
      </span> : <span className="local-content-placeholder">{result.cover_url ? <ImageOff aria-hidden="true" /> : <FileText aria-hidden="true" />}
        <span>{result.cover_url ? "封面暂不可用" : "暂无封面"}</span></span>}
    <span className="local-content-badges">
      <span className="local-content-labels">
        <span className="local-content-type">{video && <Play aria-hidden="true" />}{localContentType(result.content_type)}</span>
        {metric && <ContentMetric result={result} metric={metric} className="local-content-primary-metric" />}
      </span>
      {duration && <span className="local-content-duration" aria-label={`播放时长 ${duration}`}>{duration}</span>}
    </span>
  </span>;
}

export function LocalContentCard({ result, onOpen, highlightQuery = "" }: { result: UnifiedSearchResult; onOpen: () => void; highlightQuery?: string }) {
  return <button type="button" className="local-content-open" aria-label={`查看内容信息：${result.title || "无标题内容"}`} onClick={onOpen}>
    <LocalContentCover result={result} showMetric />
    <span className="local-content-title">{highlightSegments(result.title || "无标题内容", highlightQuery).map((segment, index) => segment.matched ? <mark key={index} className="result-highlight">{segment.text}</mark> : segment.text)}</span>
  </button>;
}
