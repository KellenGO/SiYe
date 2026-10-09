import { useEffect, useRef, useState, type CSSProperties } from "react";
import axios from "axios";
import { Minus, Plus, RotateCw } from "lucide-react";
import type { UnifiedSearchResult } from "@/types/search";
import { decodeReadingDetail, readingError, type ReadingDetail } from "@/lib/reading";
import { recordView } from "@/lib/historyApi";

function ReadingImage({ url, alt }: { url: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  return failed ? <p className="reader-notice">图片加载失败，可打开原文查看。</p>
    : <figure className="reader-image"><img src={url} alt={alt || "原文图片"} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} />{alt && <figcaption>{alt}</figcaption>}</figure>;
}

export function ReadingBody({ source }: { source: UnifiedSearchResult }) {
  const [detail, setDetail] = useState<ReadingDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [fontSize, setFontSize] = useState(18);
  const viewed = useRef(false);
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const { content_id, content_type, url } = source;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void axios.post("/api/reading/detail", { platform: "zhihu", content_id, content_type, url, refresh: revision > 0 },
      { signal: controller.signal, timeout: 50000 }).then(({ data }) => {
      if (controller.signal.aborted) return;
      const next = decodeReadingDetail(data, { content_id, content_type });
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
  }, [content_id, content_type, url, revision]);

  return <section className="reader" aria-label="知乎正文" aria-busy={loading} style={{ "--reader-font-size": `${fontSize}px` } as CSSProperties}>
    <div className="reader-toolbar">
      <h4>正文<span>站内阅读</span></h4>
      <div className="reader-tools">
        <button type="button" className="btn small" aria-label="减小正文字号" disabled={fontSize <= 16} onClick={() => setFontSize((value) => value - 1)}><Minus aria-hidden="true" /></button>
        <span aria-label={`正文字号 ${fontSize}`}>{fontSize}</span>
        <button type="button" className="btn small" aria-label="增大正文字号" disabled={fontSize >= 24} onClick={() => setFontSize((value) => value + 1)}><Plus aria-hidden="true" /></button>
        <button type="button" className="btn small" disabled={loading} onClick={() => setRevision((value) => value + 1)} aria-label="重新读取正文"><RotateCw aria-hidden="true" /></button>
      </div>
    </div>
    {loading && <p className="reader-status" role="status">{detail ? "正在重新读取，已有正文保留…" : "正在读取知乎正文…"}</p>}
    {error && <div className="reader-notice" role="alert"><p>{error}</p><button type="button" className="text-link" disabled={loading} onClick={() => setRevision((value) => value + 1)}>重试读取</button></div>}
    {detail && <>
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
  </section>;
}
