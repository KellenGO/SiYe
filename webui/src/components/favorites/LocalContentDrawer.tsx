import { useEffect, useId, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Trash2, X } from "lucide-react";
import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { PLATFORM_LABELS } from "@/types/search";
import type { BookmarkLibrary } from "@/hooks/useBookmarks";
import { BookmarkControl, BookmarkNote, WatchLaterControl } from "@/components/search/ResultTools";
import { orderedMetrics, resultKey, resultSources, safeContentUrl } from "@/lib/resultTools";
import { recordView } from "@/lib/historyApi";
import { LocalContentCover, localContentType } from "./LocalContentCard";

export function LocalContentDrawer({ result, library, onClose, fallbackFocus, savedView = false, fetchedAt = {}, onDelete }: {
  result: UnifiedSearchResult;
  library?: BookmarkLibrary;
  savedView?: boolean;
  fetchedAt?: Partial<Record<PlatformSlug, string | null>>;
  onDelete?: () => void;
  onClose: () => void;
  fallbackFocus: RefObject<HTMLDivElement>;
}) {
  const id = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const dirtyNotes = useRef(new Map<string, boolean>());
  const pointerStartedOutside = useRef(false);
  const closeRequest = useRef<() => void>(() => {});
  const [saving, setSaving] = useState(false);
  const [noteError, setNoteError] = useState("");
  const keys = resultSources(result).map(resultKey);
  const items = keys.map((key) => library?.items.find((item) => item.key === key)).filter((item) => item !== undefined);
  const requestClose = () => {
    if (saving) return;
    if ([...dirtyNotes.current.values()].some(Boolean) && !window.confirm("备注尚未保存，确定放弃修改并关闭详情吗？")) return;
    onClose();
  };
  closeRequest.current = requestClose;

  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    const root = document.getElementById("root");
    const wasInert = root?.inert ?? false;
    const overflow = document.body.style.overflow;
    const padding = document.body.style.paddingRight;
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    const fallback = fallbackFocus.current;
    if (root) root.inert = true;
    document.body.style.overflow = "hidden";
    if (scrollbar > 0) document.body.style.paddingRight = `${scrollbar}px`;
    closeButton.current?.focus({ preventScroll: true });
    const escapeOutsidePanel = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented && !panel.current?.contains(event.target as Node)
        && !panel.current?.querySelector('.confirm-card, .membership-card, [aria-expanded="true"]')) closeRequest.current();
    };
    document.addEventListener("keydown", escapeOutsidePanel);
    return () => {
      document.removeEventListener("keydown", escapeOutsidePanel);
      if (root) root.inert = wasInert;
      document.body.style.overflow = overflow;
      document.body.style.paddingRight = padding;
      const target = trigger?.isConnected ? trigger : fallback;
      target?.focus({ preventScroll: true });
    };
  }, [fallbackFocus]);

  // 取消收藏后记录已不存在；仅移出当前夹时仍允许看完详情、保存备注。
  useEffect(() => { if (savedView && !items.length) onClose(); }, [savedView, items.length, onClose]);

  return createPortal(<div className="local-content-overlay"
    onPointerDown={(event) => { pointerStartedOutside.current = event.target === event.currentTarget; }}
    onClick={(event) => {
      if (event.target === event.currentTarget && pointerStartedOutside.current) requestClose();
      pointerStartedOutside.current = false;
    }}>
    <div className="local-content-drawer" ref={panel} role="dialog" aria-modal="true" aria-labelledby={id}
      onKeyDown={(event) => {
        const confirmation = panel.current?.querySelector<HTMLElement>('.confirm-card, .membership-card[role="dialog"]');
        if (event.key === "Escape") {
          // 嵌套确认和归属选择先处理 Escape，不能连抽屉一起退出。
          if (confirmation || panel.current?.querySelector('.membership-card, [aria-expanded="true"]')) return;
          event.preventDefault();
          event.stopPropagation();
          requestClose();
        }
        if (event.key !== "Tab") return;
        const scope = confirmation ?? panel.current;
        const controls = [...(scope?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled)') ?? [])]
          .filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!first || !last) { event.preventDefault(); return; }
        if (event.shiftKey && (document.activeElement === first || !scope?.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !scope?.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
      }}>
      <div className="local-content-drawer-head"><h2 id={id}>内容信息</h2><button ref={closeButton} type="button" className="btn small" aria-label="关闭内容详情" disabled={saving} onClick={requestClose}><X aria-hidden="true" />关闭</button></div>
      <div className="local-content-drawer-body">
        {resultSources(result).map((snapshot) => {
          const item = items.find((item) => item.key === resultKey(snapshot));
          const source = savedView && item ? item.result : snapshot;
          const url = safeContentUrl(source.url);
          const published = source.published_at ? new Date(source.published_at) : null;
          const metrics = orderedMetrics(source.metrics);
          return <section className="local-content-detail" key={resultKey(source)} aria-label={`${PLATFORM_LABELS[source.platform]}内容详情`}>
            <LocalContentCover result={source} />
            <p className="local-content-detail-source">{PLATFORM_LABELS[source.platform]} · {localContentType(source.content_type)}</p>
            <h3>{source.title || "无标题内容"}</h3>
            <p className="local-content-detail-meta">{source.author || "作者未提供"}{published && Number.isFinite(published.getTime()) ? ` · 发布于 ${published.toLocaleDateString("zh-CN")}` : ""}</p>
            {url && <a className="btn small local-content-original" href={url} target="_blank" rel="noreferrer" onClick={() => { void recordView(source); }}>在原平台打开<ArrowUpRight aria-hidden="true" /></a>}
            <h4>简介</h4><p className="local-content-snippet">{source.snippet || "暂无简介"}</p>
            {metrics.length ? <dl className="local-content-metrics">{metrics.map(({ key, label }) => <div key={key}><dt>{label}</dt><dd>{source.metrics_approximate?.includes(key) ? "约 " : ""}{source.metrics[key].toLocaleString("zh-CN")}</dd></div>)}</dl>
              : <p className="local-content-detail-meta">暂无互动数据</p>}
            {library && <div className="local-content-actions"><span><BookmarkControl result={source} library={library} fetchedAt={fetchedAt} />{item?.saved ? "已收藏" : "收藏"}</span><span><WatchLaterControl result={source} library={library} fetchedAt={fetchedAt} />{item?.watchLater ? "已加入稍后再看" : "稍后再看"}</span></div>}
            {item && library && <>
            <div className="local-content-folders">
              <h4>所在收藏夹</h4>
              {item.saved || item.inDefault || item.collections.length ? <ul aria-label="所在收藏夹">
                {item.saved && <li>全部收藏</li>}
                {item.inDefault && <li>默认收藏夹</li>}
                {item.collections.map((collection) => <li key={collection.id}>{["全部", "全部收藏", "默认收藏夹", "稍后再看"].includes(collection.name) ? `${collection.name}（自建）` : collection.name}</li>)}
              </ul> : <p className="local-content-detail-meta">尚未加入收藏夹</p>}
            </div>
            <BookmarkNote bookmark={item} library={library} membershipModal onDraftChange={(dirty) => { dirtyNotes.current.set(item.key, dirty); }} onSave={async (key, note) => {
              setSaving(true);
              setNoteError("");
              try {
                const saved = await library.saveNote(key, note);
                if (!saved) setNoteError("备注未保存，请重试。输入内容已保留。");
                return saved;
              } finally { setSaving(false); }
            }} />
            </>}
          </section>;
        })}
        {onDelete && <button type="button" className="btn danger" onClick={onDelete}><Trash2 aria-hidden="true" />从历史中移除</button>}
        {library?.error && <p className="local-content-error" role="alert">{library.error}</p>}
        {noteError && <p className="local-content-error" role="alert">{noteError}</p>}
      </div>
    </div>
  </div>, document.body);
}
