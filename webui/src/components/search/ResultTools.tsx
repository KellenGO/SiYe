import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Bookmark as BookmarkIcon, Check, Clock3, Copy, Download, FolderCog } from "lucide-react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { BookmarkLibrary } from "@/hooks/useBookmarks";
import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { PLATFORM_LABELS } from "@/types/search";
import type { Bookmark } from "@/lib/bookmarks";
import { MAX_NOTE_LENGTH } from "@/lib/bookmarks";
import { resultKey, resultLinks, resultSources, resultsCsv, resultsMarkdown, type ExportRow } from "@/lib/resultTools";
import { collectionMembership, setUnsaveConfirm, unsaveConfirmEnabled, type LibraryItem, type MembershipState, type SystemCollectionKey } from "@/lib/libraryApi";

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

/**
 * 收藏 / 稍后再看按钮。
 * `onToggled` 在写库结束后回调，`added` 表示这次是"加入"还是"移出"
 * （写失败算移出，调用方据此决定要不要弹"编辑归属"）。
 */
function SystemCollectionButton({ result, library, fetchedAt, collection, compact = false, onToggled }: {
  result: UnifiedSearchResult; library: BookmarkLibrary;
  fetchedAt: Partial<Record<PlatformSlug, string | null>>;
  collection: SystemCollectionKey;
  compact?: boolean;
  onToggled?: (added: boolean, keys: string[]) => void;
}) {
  // 收藏按钮看的是「已收藏」（saved），不是「是否在默认收藏夹」——
  // 一条内容可以不在任何收藏夹里，只要收藏过就仍算收藏着。
  const [confirming, setConfirming] = useState(false);
  const [remember, setRemember] = useState(false);
  const membership = new Map(library.items.map((item) => [item.key, collection === "default" ? item.saved : item.watchLater]));
  const sources = resultSources(result);
  const saved = sources.every((source) => membership.get(resultKey(source)) === true);
  const baseLabel = collection === "default" ? "收藏" : "稍后再看";
  const label = saved ? `取消${baseLabel}` : sources.length > 1 ? `全部加入${baseLabel}` : baseLabel;
  const Icon = collection === "default" ? BookmarkIcon : Clock3;
  const run = async () => {
    const ok = await library.toggleSystem(collection, result, fetchedAt);
    onToggled?.(ok && !saved, sources.map(resultKey));
  };
  return <>
    <button type="button" aria-label={`${label} ${result.title}`} aria-pressed={saved}
      title={label}
      className={compact ? `inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:ring-brand/50 ${saved ? "text-brand-strong bg-brand-soft" : "text-cyber-text-muted hover:text-brand-strong hover:bg-brand-soft"}` : TOOL_BUTTON}
      onClick={() => {
        // 取消收藏会把这条内容连同备注、所有归属一起删掉，先问一次
        if (saved && collection === "default" && unsaveConfirmEnabled()) { setConfirming(true); return; }
        void run();
      }}>
      {saved ? <Check className="w-3.5 h-3.5 text-brand-strong" /> : <Icon className="w-3.5 h-3.5" />}
      {!compact && (saved ? `已${baseLabel}` : label)}
    </button>
    <ConfirmDialog open={confirming} title="是否要取消收藏" danger confirmLabel="取消收藏"
      description="取消后这条内容将从本机收藏库移除，备注所有收藏夹归属一并消失。"
      hint="如若只想改变内容所在的收藏夹位置，点击下方的「编辑归属」按钮。"
      rememberLabel="下次不再提示" rememberChecked={remember}
      onRememberChange={(checked) => { setRemember(checked); setUnsaveConfirm(!checked); }}
      onCancel={() => setConfirming(false)}
      onConfirm={() => { setConfirming(false); void run(); }} />
  </>;
}

function SystemCollectionControl({ result, library, fetchedAt, collection, onToggled }: {
  result: UnifiedSearchResult; library: BookmarkLibrary;
  fetchedAt: Partial<Record<PlatformSlug, string | null>>;
  collection: SystemCollectionKey;
  onToggled?: (added: boolean, keys: string[]) => void;
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
  if (sources.length < 2) return <SystemCollectionButton result={result} library={library} fetchedAt={fetchedAt} collection={collection} compact onToggled={onToggled} />;
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
        <SystemCollectionButton result={source} library={library} fetchedAt={fetchedAt} collection={collection} compact onToggled={onToggled} />
      </div>)}
      <div className="mt-2 border-t border-cyber-border-subtle pt-2"><SystemCollectionButton result={result} library={library} fetchedAt={fetchedAt} collection={collection} onToggled={onToggled} /></div>
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

function MembershipCheckbox({ state, disabled, onChange, label }: {
  state: MembershipState; disabled: boolean;
  onChange: (enabled: boolean) => void; label: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (input.current) input.current.indeterminate = state === null;
  }, [state]);
  return <label>
    <input ref={input} type="checkbox" checked={state === true} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
    <span className="membership-folder-name" title={label}>{label}</span>
  </label>;
}

/**
 * 归属编辑面板：一条或一批收藏条目共用。
 * 聚合卡片一次收藏了多个平台版本时，`keys` 会有多个，勾选框按"部分命中"显示半选。
 * `onClose` 传了表示这是一次性提示（关闭后整个入口收起），不传则保留「编辑归属」按钮可反复打开。
 */
export function MembershipEditor({ keys, library, subject, label = "编辑归属", onClose }: {
  keys: string[]; library: BookmarkLibrary; subject?: string; label?: string; onClose?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => { setOpen(false); onClose?.(); }, [onClose]);
  const byKey = new Map(library.items.map((item) => [item.key, item]));
  const items = keys
    .map((key) => byKey.get(key))
    .filter((item): item is LibraryItem => Boolean(item));
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) close(); };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { close(); root.current?.querySelector("button")?.focus(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open, close]);
  if (!items.length) return null;
  const membership = collectionMembership(items);
  const updateSystem = async (collection: SystemCollectionKey, enabled: boolean) => {
    setBusy(true);
    await library.setSystemMembership(keys, collection, enabled);
    setBusy(false);
  };
  const updateCustom = async (id: number, enabled: boolean) => {
    setBusy(true);
    if (enabled) await library.addToCollection(keys, id);
    else await library.removeFromCollection(keys, id);
    setBusy(false);
  };
  return <div className="membership-editor" ref={root}>
    <button type="button" className="text-link" aria-expanded={open} onClick={() => setOpen(!open)}><FolderCog />{label}</button>
    {open && <div className="membership-card" role="group" aria-label={subject ? `编辑收藏夹归属 ${subject}` : "编辑收藏夹归属"}>
      <div className="membership-card-head"><p>收藏夹归属</p><button type="button" className="text-link" onClick={close}>完成</button></div>
      {/* 「稍后再看」不在这里：它由卡片上独立的稍后再看按钮控制，和收藏体系分开 */}
      <label><input type="checkbox" checked disabled /><span className="membership-folder-name" title="全部">全部</span></label>
      <MembershipCheckbox state={membership.inDefault} disabled={busy} onChange={(enabled) => void updateSystem("default", enabled)} label="默认收藏夹" />
      {library.collections.map((collection) => {
        const name = customCollectionLabel(collection.name);
        return <MembershipCheckbox key={collection.id} state={membership.custom[collection.id] ?? false} disabled={busy} onChange={(enabled) => void updateCustom(collection.id, enabled)} label={name} />;
      })}
    </div>}
  </div>;
}

export function BookmarkNote({ bookmark, onSave, library }: {
  bookmark: Bookmark & { key: string; inDefault: boolean; saved: boolean; watchLater: boolean; collections: { id: number; name: string }[] };
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
          <MembershipEditor keys={[bookmark.key]} library={library} subject={bookmark.result.title} />
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
