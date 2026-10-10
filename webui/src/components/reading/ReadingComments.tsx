import { useEffect, useState } from "react";
import axios from "axios";
import type { UnifiedSearchResult } from "@/types/search";
import { decodeReadingComments, readingError, type ReadingComments as Comments } from "@/lib/reading";

export function ReadingComments({ source, bodyLoading }: { source: UnifiedSearchResult; bodyLoading: boolean }) {
  const [revision, setRevision] = useState(0);
  const [comments, setComments] = useState<Comments | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { platform, content_id, content_type, url } = source;
  useEffect(() => {
    if (!revision) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void axios.post("/api/reading/comments", { platform, content_id, content_type, url },
      { signal: controller.signal, timeout: 80000 }).then(({ data }) => {
      if (!controller.signal.aborted) setComments(decodeReadingComments(data, { platform, content_id, content_type }));
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(readingError(failure).replace(/正文/g, "评论"));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [platform, content_id, content_type, url, revision]);
  return <section className="reader-comments" aria-label="评论" aria-busy={loading}>
    <div className="reader-toolbar"><h4>评论{comments && <span>{comments.sort} · {comments.entries.length} 条</span>}</h4>
      <button className="btn small" type="button" disabled={loading || bodyLoading} onClick={() => setRevision(value => value + 1)}>
        {loading ? "正在读取评论…" : error ? "重试评论" : comments ? "刷新评论" : "查看评论"}
      </button>
    </div>
    {error && <p className="reader-notice" role="alert">{error}</p>}
    {comments && <>
      {comments.notice && <p className="reader-notice">{comments.notice}</p>}
      {!comments.entries.length && !comments.notice && <p className="reader-status">平台暂未返回可展示的评论。</p>}
      {comments.entries.map(entry => <article className={`reader-comment ${entry.parent_id ? "is-reply" : ""}`} key={entry.id}>
        <div className="reader-comment-meta"><strong>{entry.author || "用户"}</strong>{entry.parent_id && <span>回复</span>}{entry.like_count !== undefined && <span>{entry.like_count} 赞</span>}</div>
        <p>{entry.text}</p>
      </article>)}
      {comments.limited && <p className="reader-notice">仅展示部分评论及少量回复，完整讨论请在原平台查看。</p>}
    </>}
  </section>;
}
