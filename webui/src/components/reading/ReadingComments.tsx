import { useEffect, useState } from "react";
import axios from "axios";
import { Heart, CornerDownRight } from "lucide-react";
import type { UnifiedSearchResult } from "@/types/search";
import { decodeReadingComments, groupReadingComments, readingError, type ReadingComments as Comments } from "@/lib/reading";

function CommentEntry({ entry, replyTo }: { entry: Comments["entries"][number]; replyTo?: string }) {
  const author = entry.author || "用户";
  return <div className={`reader-comment ${entry.parent_id ? "is-reply" : ""}`}>
    <div className="reader-comment-meta">
      <span className="reader-comment-avatar" aria-hidden="true">{Array.from(author)[0]}</span>
      <strong>{author}</strong>
      {entry.like_count !== undefined && <span className="reader-comment-likes" aria-label={`${entry.like_count} 赞`}><Heart aria-hidden="true" />{entry.like_count}</span>}
    </div>
    {entry.parent_id && <div className="reader-reply-target"><CornerDownRight aria-hidden="true" />{replyTo ? `回复 ${replyTo}` : "回复 · 原评论未返回"}</div>}
    <p>{entry.text}</p>
  </div>;
}

export function ReadingComments({ source, bodyLoading, id, hidden = false }: { source: UnifiedSearchResult; bodyLoading: boolean; id?: string; hidden?: boolean }) {
  const [revision, setRevision] = useState(0);
  const [comments, setComments] = useState<Comments | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { platform, content_id, content_type, url } = source;
  useEffect(() => {
    if (!revision && !bodyLoading) setRevision(1);
  }, [revision, bodyLoading]);
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
  return <section id={id} hidden={hidden} className="reader-comments" aria-label="评论" aria-busy={loading}>
    <div className="reader-toolbar"><h4>评论{comments && <span>{comments.sort} · {comments.entries.length} 条</span>}</h4>
      <button className="btn small" type="button" disabled={loading || bodyLoading} onClick={() => setRevision(value => value + 1)}>
        {loading || !revision ? "正在读取评论…" : error ? "重试评论" : "刷新评论"}
      </button>
    </div>
    {error && <p className="reader-notice" role="alert">{error}</p>}
    {comments && <>
      {comments.notice && <p className="reader-notice">{comments.notice}</p>}
      {!comments.entries.length && !comments.notice && <p className="reader-status">平台暂未返回可展示的评论。</p>}
      <div className="reader-comment-list">{groupReadingComments(comments.entries).map(({ entry, replies }) => <article className="reader-comment-thread" key={entry.id} aria-label={`${entry.author || "用户"}的${entry.parent_id ? "回复" : "评论"}`}>
        <CommentEntry entry={entry} />
        {replies.length > 0 && <div className="reader-comment-replies" role="group" aria-label={`回复 ${entry.author || "用户"}`}>
          <div className="reader-replies-label">已加载 {replies.length} 条回复</div>
          {replies.map(reply => <CommentEntry key={reply.id} entry={reply} replyTo={entry.author || "用户"} />)}
        </div>}
      </article>)}</div>
      {comments.limited && <p className="reader-notice">仅展示部分评论及少量回复，完整讨论请在原平台查看。</p>}
    </>}
  </section>;
}
