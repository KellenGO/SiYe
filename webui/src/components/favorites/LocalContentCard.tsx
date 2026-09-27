import { useState } from "react";
import { FileText, ImageOff, Play } from "lucide-react";
import type { UnifiedSearchResult } from "@/types/search";
import { PLATFORM_LABELS } from "@/types/search";

const CONTENT_TYPES: Record<string, string> = {
  video: "视频", short_video: "短视频", zvideo: "视频", note: "图文笔记",
  answer: "回答", article: "文章", post: "帖子",
};

export function localContentType(type: string): string {
  return CONTENT_TYPES[type] || "内容";
}

export function LocalContentCover({ result }: { result: UnifiedSearchResult }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const hasCover = !!result.cover_url && result.cover_url !== failedUrl;
  const video = ["video", "short_video", "zvideo"].includes(result.content_type);
  return <span className="local-content-cover">
    {hasCover ? <img src={result.cover_url!} alt="" loading="lazy" referrerPolicy="no-referrer"
      onError={() => setFailedUrl(result.cover_url)} />
      : <span className="local-content-placeholder">{result.cover_url ? <ImageOff aria-hidden="true" /> : <FileText aria-hidden="true" />}
        <span>{result.cover_url ? "封面暂不可用" : "暂无封面"}</span></span>}
    <span className="local-content-type">{video && <Play aria-hidden="true" />}{localContentType(result.content_type)}</span>
  </span>;
}

export function LocalContentCard({ result, onOpen }: { result: UnifiedSearchResult; onOpen: () => void }) {
  return <button type="button" className="local-content-open" aria-label={`查看内容信息：${result.title || "无标题内容"}`} onClick={onOpen}>
    <LocalContentCover result={result} />
    <span className="local-content-title">{result.title || "无标题内容"}</span>
    <span className="local-content-meta"><span>{PLATFORM_LABELS[result.platform]}</span><span>查看信息</span></span>
  </button>;
}
