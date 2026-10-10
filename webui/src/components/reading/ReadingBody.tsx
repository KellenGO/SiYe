import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import axios from "axios";
import { Minus, Plus, RotateCw } from "lucide-react";
import type { UnifiedSearchResult } from "@/types/search";
import { PLATFORM_LABELS } from "@/types/search";
import { decodeReadingDetail, readingError, type ReadingDetail } from "@/lib/reading";
import { recordView } from "@/lib/historyApi";
import { ReadingComments } from "./ReadingComments";

function ReadingImage({ url, alt }: { url: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  return failed ? <p className="reader-notice">图片加载失败，可打开原文查看。</p>
    : <figure className="reader-image"><img src={url} alt={alt || "原文图片"} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} />{alt && <figcaption>{alt}</figcaption>}</figure>;
}

function ReadingVideo({ detail, onPlay }: { detail: ReadingDetail; onPlay: () => void }) {
  const [segment, setSegment] = useState(0);
  const [failed, setFailed] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const mediaUrl = detail.media[segment].url;
  useEffect(() => {
    const video = player.current;
    if (video) video.src = mediaUrl;
    return () => { if (video) { video.pause(); video.removeAttribute("src"); video.load(); } };
  }, [mediaUrl]);
  const choose = (index: number) => { setSegment(index); setFailed(false); };
  return <div className={`reader-video ${detail.portrait ? "is-portrait" : ""}`}>
    <video ref={player} key={mediaUrl} poster={detail.poster} controls playsInline preload="none"
      aria-label="站内视频播放器" onPlaying={onPlay} onError={() => setFailed(true)}
      onEnded={() => { if (segment + 1 < detail.media.length) choose(segment + 1); }} />
    {detail.media.length > 1 && <div className="reader-segments" aria-label="视频段落">{detail.media.map((part, index) =>
      <button className="btn small" type="button" key={part.url} aria-pressed={index === segment} onClick={() => choose(index)}>{part.label}</button>)}</div>}
    {failed && <p className="reader-notice" role="alert">视频加载失败或格式暂不支持，可重新读取内容或打开原文观看。</p>}
  </div>;
}

export function ReadingBody({ source, header, actions }: { source: UnifiedSearchResult; header?: ReactNode; actions?: ReactNode }) {
  const [detail, setDetail] = useState<ReadingDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [fontSize, setFontSize] = useState(18);
  const viewed = useRef(false);
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const { platform, content_id, content_type, url } = source;
  const platformLabel = PLATFORM_LABELS[platform];
  const markViewed = () => {
    if (!viewed.current) { viewed.current = true; void recordView(sourceRef.current); }
  };

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void axios.post("/api/reading/detail", { platform, content_id, content_type, url, refresh: revision > 0 },
      { signal: controller.signal, timeout: 80000 }).then(({ data }) => {
      if (controller.signal.aborted) return;
      const next = decodeReadingDetail(data, { platform, content_id, content_type });
      setDetail(next);
      if (!viewed.current && next.blocks.some((block) => block.type !== "unsupported")) {
        viewed.current = true;
        void recordView(sourceRef.current);
      }
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(readingError(failure));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [platform, content_id, content_type, url, revision]);

  return <div className="reading-layout"><div className="reading-content-column">
    {header}
    <section className="reader" aria-label={`${platformLabel}内容`} aria-busy={loading} style={{ "--reader-font-size": `${fontSize}px` } as CSSProperties}>
    <div className="reader-toolbar">
      <h4>内容<span>站内阅读</span></h4>
      <div className="reader-tools">
        <button type="button" className="btn small" aria-label="减小正文字号" disabled={fontSize <= 16} onClick={() => setFontSize((value) => value - 1)}><Minus aria-hidden="true" /></button>
        <span aria-label={`正文字号 ${fontSize}`}>{fontSize}</span>
        <button type="button" className="btn small" aria-label="增大正文字号" disabled={fontSize >= 24} onClick={() => setFontSize((value) => value + 1)}><Plus aria-hidden="true" /></button>
        <button type="button" className="btn small" disabled={loading} onClick={() => setRevision((value) => value + 1)} aria-label="重新读取内容"><RotateCw aria-hidden="true" /></button>
      </div>
    </div>
    {loading && <p className="reader-status" role="status">{detail ? "正在重新读取，已有内容保留…" : `正在读取${platformLabel}内容…`}</p>}
    {error && <div className="reader-notice" role="alert"><p>{error}</p><button type="button" className="text-link" disabled={loading} onClick={() => setRevision((value) => value + 1)}>重试读取</button></div>}
    {detail && <>
      {detail.media.length > 0 && <ReadingVideo key={detail.media.map((part) => part.url).join(":")} detail={detail} onPlay={markViewed} />}
      {detail.limited && <p className="reader-notice">当前正文可能不完整，请结合原文查看。</p>}
      {detail.notices.map((notice, index) => <p className="reader-notice" key={index}>{notice}</p>)}
      <div className="reader-body">{detail.blocks.map((block, index) => {
        if (block.type === "image" && block.url) return <ReadingImage key={`${index}:${block.url}`} url={block.url} alt={block.text} />;
        if (block.type === "heading") return <h5 className={`reader-heading reader-heading-${block.level ?? 2}`} key={index}>{block.text}</h5>;
        if (block.type === "quote") return <blockquote key={index}>{block.text}</blockquote>;
        if (block.type === "code") return <pre key={index}><code>{block.text}</code></pre>;
        if (block.type === "unsupported") return <p className="reader-notice" key={index}>{block.text}</p>;
        return <p className={block.type === "list-item" ? "reader-list-item" : undefined} key={index}>{block.text}</p>;
      })}</div>
    </>}
    </section>
    {actions}
  </div><ReadingComments key={`${platform}:${content_type}:${content_id}`} source={source} bodyLoading={loading} /></div>;
}
