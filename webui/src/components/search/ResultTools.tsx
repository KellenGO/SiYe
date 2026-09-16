import { useEffect, useId, useRef, useState } from "react";
import { Bookmark as BookmarkIcon, Check, Clock3, Copy, Download, FolderCog } from "lucide-react";
import { toast } from "sonner";
import type { BookmarkLibrary } from "@/hooks/useBookmarks";
import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { PLATFORM_LABELS } from "@/types/search";
import type { Bookmark } from "@/lib/bookmarks";
import { MAX_NOTE_LENGTH } from "@/lib/bookmarks";
import { resultKey, resultLinks, resultSources, resultsCsv, resultsMarkdown, type ExportRow } from "@/lib/resultTools";
import type { SystemCollectionKey } from "@/lib/libraryApi";

export const TOOL_BUTTON = "inline-flex items-center gap-1.5 rounded-lg border border-cyber-border-subtle px-2.5 py-1.5 text-xs text-cyber-text-secondary hover:text-brand-strong hover:border-brand/50 disabled:opacity-40 disabled:cursor-not-allowed";

export function ExportActions({ rows, keyword }: { rows: ExportRow[]; keyword: string }) {
  const download = (format: "csv" | "md") => {
    try {
      const contents = format === "csv" ? resultsCsv(rows) : resultsMarkdown(rows);
      const blob = new Blob([contents], { type: format === "csv" ? "text/csv;charset=utf-8" : "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const name = keyword.replace(/[^\p{L}\p{N}._-]+/gu, "_").slice(0, 48) || "搜索结果";
      anchor.download = `${name}-${new Date().toISOString().replace(/[:.]/g, "-")}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      toast.error("导出失败，请重试。");
    }
  };
  const copy = async () => {
    try {
      const links = resultLinks(rows);
      if (!links) { toast.error("当前结果没有可复制的原文链接。"); return; }
      await navigator.clipboard.writeText(links);
      toast.success("原文链接已复制");
    } catch { toast.error("复制失败，请允许浏览器访问剪贴板，或使用导出功能。"); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={TOOL_BUTTON} disabled={!rows.length} onClick={() => download("csv")}><Download className="w-3.5 h-3.5" />导出 CSV</button>
      <button type="button" className={TOOL_BUTTON} disabled={!rows.length} onClick={() => download("md")}>导出 Markdown</button>
      <button type="button" className={TOOL_BUTTON} disabled={!rows.length} onClick={() => void copy()}><Copy className="w-3.5 h-3.5" />复制链接</button>
    </div>
  );
}

function SystemCollectionButton({ result, library, fetchedAt, collection, compact = false }: {
  result: UnifiedSearchResult; library: BookmarkLibrary;
  fetchedAt: Partial<Record<PlatformSlug, string | null>>;
  collection: SystemCollectionKey;
  compact?: boolean;
}) {
  const membership = new Map(library.items.map((item) => [item.key, collection === "default" ? item.inDefault : item.watchLater]));
  const sources = resultSources(result);
  const saved = sources.every((source) => membership.get(resultKey(source)) === true);
  const baseLabel = collection === "default" ? "收藏" : "稍后再看";
  const label = saved ? `移出${baseLabel}` : sources.length > 1 ? `全部加入${baseLabel}` : baseLabel;
  const Icon = collection === "default" ? BookmarkIcon : Clock3;
  return (
    <button type="button" aria-label={`${label} ${result.title}`} aria-pressed={saved}
      title={label}
      className={compact ? `inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-brand/50 ${saved ? "text-brand-strong bg-brand-soft" : "text-cyber-text-muted hover:text-brand-strong hover:bg-brand-soft"}` : TOOL_BUTTON}
      onClick={() => library.toggleSystem(collection, result, fetchedAt)}>
      {saved ? <Check className="w-3.5 h-3.5 text-brand-strong" /> : <Icon className="w-3.5 h-3.5" />}
      {!compact && (saved ? `已加入${baseLabel}` : label)}
    </button>
  );
}

function SystemCollectionControl({ result, library, fetchedAt, collection }: {
  result: UnifiedSearchResult; library: BookmarkLibrary;
  fetchedAt: Partial<Record<PlatformSlug, string | null>>;
  collection: SystemCollectionKey;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();
  const sources = resultSources(result);
  const membership = new Map(library.items.map((item) => [item.key, collection === "default" ? item.inDefault : item.watchLater]));
  const savedCount = sources.filter((source) => membership.get(resultKey(source)) === true).length;
  const baseLabel = collection === "default" ? "收藏" : "稍后再看";
  const Icon = collection === "default" ? BookmarkIcon : Clock3;
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); root.current?.querySelector("button")?.focus(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  if (sources.length < 2) return <SystemCollectionButton result={result} library={library} fetchedAt={fetchedAt} collection={collection} compact />;
  return <div ref={root} className="relative shrink-0">
    <button type="button" aria-label={`选择${baseLabel}平台 ${result.title}`} aria-expanded={open} aria-controls={id}
      title={savedCount ? `${baseLabel} ${savedCount}/${sources.length} 个来源，点击管理` : `选择要加入${baseLabel}的平台`}
      className={`inline-flex items-center justify-center gap-1 h-8 min-w-8 rounded-full px-2 focus-visible:ring-2 focus-visible:ring-brand/50 ${savedCount ? "text-brand-strong bg-brand-soft" : "text-cyber-text-muted hover:text-brand-strong hover:bg-brand-soft"}`}
      onClick={() => setOpen(!open)}>
      <Icon className="w-3.5 h-3.5" fill={collection === "default" && savedCount ? "currentColor" : "none"} />
      {savedCount > 0 && <span className="text-[10px]">{savedCount}/{sources.length}</span>}
    </button>
    {open && <div id={id} role="group" aria-label={`选择${baseLabel}来源`} className="absolute right-0 top-10 z-20 w-56 rounded-xl border border-cyber-border-subtle bg-cyber-bg-primary p-3 shadow-lg">
      <p className="mb-2 text-xs font-medium text-cyber-text-secondary">选择要加入{baseLabel}的平台版本</p>
      {sources.map((source) => <div key={resultKey(source)} className="flex items-center justify-between gap-2 py-1 text-xs text-cyber-text-secondary">
        <span>{PLATFORM_LABELS[source.platform]}</span>
        <SystemCollectionButton result={source} library={library} fetchedAt={fetchedAt} collection={collection} compact />
      </div>)}
      <div className="mt-2 border-t border-cyber-border-subtle pt-2"><SystemCollectionButton result={result} library={library} fetchedAt={fetchedAt} collection={collection} /></div>
    </div>}
  </div>;
}

export function BookmarkControl(props: Omit<Parameters<typeof SystemCollectionControl>[0], "collection">) {
  return <SystemCollectionControl {...props} collection="default" />;
}

export function WatchLaterControl(props: Omit<Parameters<typeof SystemCollectionControl>[0], "collection">) {
  return <SystemCollectionControl {...props} collection="watch_later" />;
}

function customCollectionLabel(name: string): string {
  return ["全部", "全部收藏", "默认收藏夹", "稍后再看"].includes(name) ? `${name}（自建）` : name;
}

function MembershipEditor({ bookmark, library }: { bookmark: Bookmark & { key: string; inDefault: boolean; watchLater: boolean; collections: { id: number; name: string }[] }; library: BookmarkLibrary }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const updateSystem = async (collection: SystemCollectionKey, enabled: boolean) => {
    setBusy(true);
    await library.setSystemMembership([bookmark.key], collection, enabled);
    setBusy(false);
  };
  const updateCustom = async (id: number, enabled: boolean) => {
    setBusy(true);
    if (enabled) await library.addToCollection([bookmark.key], id);
    else await library.removeFromCollection([bookmark.key], id);
    setBusy(false);
  };
  const customIds = new Set(bookmark.collections.map((item) => item.id));
  return <div className="membership-editor">
    <button type="button" className="text-link" aria-expanded={open} onClick={() => setOpen(!open)}><FolderCog />编辑归属</button>
    {open && <div className="membership-card" role="group" aria-label={`编辑收藏夹归属 ${bookmark.result.title}`}>
      <div className="membership-card-head"><p>收藏夹归属</p><button type="button" className="text-link" onClick={() => setOpen(false)}>完成</button></div>
      <label><input type="checkbox" checked disabled /><span className="membership-folder-name" title="全部">全部</span></label>
      <label><input type="checkbox" checked={bookmark.inDefault} disabled={busy} onChange={(event) => void updateSystem("default", event.target.checked)} /><span className="membership-folder-name" title="默认收藏夹">默认收藏夹</span></label>
      <label><input type="checkbox" checked={bookmark.watchLater} disabled={busy} onChange={(event) => void updateSystem("watch_later", event.target.checked)} /><span className="membership-folder-name" title="稍后再看">稍后再看</span></label>
      {library.collections.map((collection) => {
        const label = customCollectionLabel(collection.name);
        return <label key={collection.id}>
          <input type="checkbox" checked={customIds.has(collection.id)} disabled={busy} onChange={(event) => void updateCustom(collection.id, event.target.checked)} />
          <span className="membership-folder-name" title={label}>{label}</span>
        </label>;
      })}
    </div>}
  </div>;
}

export function BookmarkNote({ bookmark, onSave, library }: {
  bookmark: Bookmark & { key: string; inDefault: boolean; watchLater: boolean; collections: { id: number; name: string }[] };
  onSave: (key: string, note: string) => boolean | Promise<boolean>;
  library: BookmarkLibrary;
}) {
  const [draft, setDraft] = useState(bookmark.note);
  const [editing, setEditing] = useState(false);
  const id = useId();
  useEffect(() => setDraft(bookmark.note), [bookmark.note]);
  return (
    <div className="bookmark-note px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-cyber-text-muted mb-2">
        <span>收藏于 {new Date(bookmark.savedAt).toLocaleString("zh-CN")}</span>
        <span className="bookmark-meta-actions">
          <MembershipEditor bookmark={bookmark} library={library} />
          {!editing && <button type="button" aria-label={`${bookmark.note ? "编辑备注" : "添加备注"} ${bookmark.result.title}`}
            className="rounded px-1 py-1 text-xs text-cyber-text-muted hover:text-brand-strong focus-visible:ring-2 focus-visible:ring-brand/50"
            onClick={() => { setDraft(bookmark.note); setEditing(true); }}>{bookmark.note ? "编辑备注" : "添加备注"}</button>}
        </span>
      </div>
      {!editing && bookmark.note && <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-cyber-text-secondary">{bookmark.note}</p>}
      {editing && <>
      <label htmlFor={id} className="mb-1 block text-xs text-cyber-text-muted">备注</label>
      <textarea id={id} aria-label={`备注 ${bookmark.result.title}`} value={draft} maxLength={MAX_NOTE_LENGTH}
        rows={2} autoFocus onChange={(event) => setDraft(event.target.value)}
        className="w-full rounded-lg border border-cyber-border-subtle bg-cyber-bg-primary p-2 text-sm text-cyber-text-primary focus:outline-none focus:ring-2 focus:ring-brand/40"
        placeholder="记录这条内容对你有什么用…" />
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-xs text-cyber-text-muted">{draft.length}/{MAX_NOTE_LENGTH}{draft !== bookmark.note ? " · 尚未保存" : ""}</span>
        <div className="flex gap-2">
          <button type="button" className={TOOL_BUTTON} onClick={() => { setDraft(bookmark.note); setEditing(false); }}>取消编辑</button>
          <button type="button" className={TOOL_BUTTON} disabled={draft === bookmark.note} onClick={() => {
            void (async () => {
              const saved = await onSave(resultKey(bookmark.result), draft);
              if (saved) { toast.success("备注已保存"); setEditing(false); }
            })();
          }}>保存备注</button>
        </div>
      </div>
      </>}
    </div>
  );
}
