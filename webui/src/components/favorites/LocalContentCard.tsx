import { useState } from "react";
import { FileText, ImageOff, Play } from "lucide-react";
import type { UnifiedSearchResult } from "@/types/search";
import { highlightSegments, orderedMetrics } from "@/lib/resultTools";
import { xhsCoverFallback } from "@/lib/localContentCover";

const CONTENT_TYPES: Record<string, string> = {
  video: "视频", short_video: "短视频", zvideo: "视频", note: "图文笔记",
  answer: "回答", article: "文章", post: "帖子",
};

export function localContentType(type: string): string {
  return CONTENT_TYPES[type] || "内容";
}

export function LocalContentCover({ result, showMetric = false }: { result: UnifiedSearchResult; showMetric?: boolean }) {
  const [failedUrls, setFailedUrls] = useState<string[]>([]);
  const fallback = xhsCoverFallback(result.cover_url, result.platform);
  const cover = [result.cover_url, fallback].find((url) => url && !failedUrls.includes(url));
  const video = ["video", "short_video", "zvideo"].includes(result.content_type);
  const metric = showMetric ? orderedMetrics(result.metrics, 1)[0] : undefined;
  const metricLabel = metric?.key === "view_count" && !video ? "阅读" : metric?.label;
  const approximate = metric && result.metrics_approximate?.includes(metric.key) ? "约 " : "";
  return <span className="local-content-cover">
    {cover ? <img src={cover} alt="" loading="lazy" referrerPolicy="no-referrer"
      onError={() => setFailedUrls((urls) => [...urls, cover])} />
      : result.platform === "zhihu" ? <span className="local-content-topic">
        <span className="local-content-topic-brand">知乎 · {localContentType(result.content_type)}</span>
        <strong>{result.title || "知乎内容"}</strong>
        <span className="local-content-topic-summary">{result.snippet || result.author || "查看问题与讨论"}</span>
      </span> : <span className="local-content-placeholder">{result.cover_url ? <ImageOff aria-hidden="true" /> : <FileText aria-hidden="true" />}
        <span>{result.cover_url ? "封面暂不可用" : "暂无封面"}</span></span>}
    <span className="local-content-type">{video && <Play aria-hidden="true" />}{localContentType(result.content_type)}</span>
    {metric && <span className="local-content-primary-metric" title={`${metricLabel} ${approximate}${result.metrics[metric.key].toLocaleString("zh-CN")}`}>
      {metricLabel} {approximate}{new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(result.metrics[metric.key])}
    </span>}
  </span>;
}

export function LocalContentCard({ result, onOpen, highlightQuery = "" }: { result: UnifiedSearchResult; onOpen: () => void; highlightQuery?: string }) {
  return <button type="button" className="local-content-open" aria-label={`查看内容信息：${result.title || "无标题内容"}`} onClick={onOpen}>
    <LocalContentCover result={result} showMetric />
    <span className="local-content-title">{highlightSegments(result.title || "无标题内容", highlightQuery).map((segment, index) => segment.matched ? <mark key={index} className="result-highlight">{segment.text}</mark> : segment.text)}</span>
  </button>;
}
