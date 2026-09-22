import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import {
  AlertTriangle, ArrowLeft, Bookmark, Check, Clock3, FolderHeart, FolderPlus, Grid2X2, List, Loader2, MoreHorizontal, Pencil, RefreshCw, Trash2, X,
} from "lucide-react";
import { ResultTabs } from "@/components/search/ResultTabs";
import { BookmarkBackup } from "@/components/search/BookmarkBackup";
import { useBookmarks } from "@/hooks/useBookmarks";
import { useFavorites } from "@/hooks/useFavorites";
import { parseGroupKey, resultSources } from "@/lib/resultTools";
import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import { PLATFORM_COLORS, PLATFORM_LABELS, STATUS_LABELS } from "@/types/search";
import { PLATFORM_SLUGS } from "@/lib/platformMeta";
import { readLocalFolderView, writeLocalFolderView, type LocalFolderView } from "@/lib/localFolderView";

const PLATFORMS = PLATFORM_SLUGS;
const RECOVERY_NOTICE_PREFIX = "siye_library_recovery_notice_";


function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : "收藏夹同步失败，请稍后重试。";
}

/** 本地收藏左侧选择：全部 / 内置分类 / 某个自建收藏夹。 */
type LibrarySelection = { kind: "all" } | { kind: "unclassified" } | { kind: "default" } | { kind: "watch_later" } | { kind: "collection"; id: number };
type RemoteFolder = { account: string; folder: string; platform: PlatformSlug; name: string; item_count: number; cover_url?: string | null; observed_state: "present" | "not_found"; last_content_complete_at?: string | null };
type RemoteFolderItems = { items: UnifiedSearchResult[]; total: number; offset: number; limit: number };

function customCollectionLabel(name: string): string {
  return ["全部", "全部收藏", "默认收藏夹", "稍后再看"].includes(name) ? `${name}（自建）` : name;
}

function stableTime(value: string | null | undefined): number {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

interface FavoritesPageProps {
  activeTab?: "local" | "remote";
  onTabChange?: (tab: "local" | "remote") => void;
  onNavigateAccounts: () => void;
}

export function FavoritesPage({ activeTab, onTabChange, onNavigateAccounts }: FavoritesPageProps) {
  const remote = useFavorites();
  const library = useBookmarks();
  const [localTab, setLocalTab] = useState<"local" | "remote">("local");
  const tab = activeTab ?? localTab;
  const setTab = (next: "local" | "remote") => {
    setLocalTab(next);
    onTabChange?.(next);
  };
  const [selected, setSelected] = useState<Set<PlatformSlug>>(() => new Set(PLATFORMS));

  const [selection, setSelection] = useState<LibrarySelection>({ kind: "all" });
  const [localFolderView, setLocalFolderView] = useState<LocalFolderView>(() => readLocalFolderView());
  const [localFolderRoot, setLocalFolderRoot] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [selectionResetKey, setSelectionResetKey] = useState(0);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [renaming, setRenaming] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [moving, setMoving] = useState(false);
  const [showRecoveryNotice, setShowRecoveryNotice] = useState(false);
  const [remoteFolders, setRemoteFolders] = useState<RemoteFolder[]>([]);
  const [remoteFolderView, setRemoteFolderView] = useState<RemoteFolder | null>(null);
  const [remoteFolderItems, setRemoteFolderItems] = useState<RemoteFolderItems | null>(null);
  const [remoteFolderLoading, setRemoteFolderLoading] = useState(false);
  const folderRequest = useRef(0);
  const gridScrollY = useRef(0);
  const localGridScrollY = useRef(0);

  const defaultCount = useMemo(
    () => library.items.filter((item) => item.inDefault).length,
    [library.items],
  );
  const watchLaterCount = useMemo(() => library.items.filter((item) => item.watchLater).length, [library.items]);
  const unclassifiedCount = useMemo(
    () => library.stats?.unclassified ?? library.items.filter((item) => item.saved && !item.inDefault && !item.collections.length).length,
    [library.stats, library.items],
  );
  // 「全部」= 已收藏的内容；只挂在稍后再看上的不算，它有自己的视图。
  const savedCount = useMemo(
    () => library.stats?.saved_count ?? library.items.filter((item) => item.saved).length,
    [library.stats, library.items],
  );
  const visibleItems = useMemo(() => {
    if (selection.kind === "all") return library.items.filter((item) => item.saved);
    if (selection.kind === "unclassified") return library.items.filter((item) => item.saved && !item.inDefault && !item.collections.length);
    if (selection.kind === "default") return library.items.filter((item) => item.inDefault);
    if (selection.kind === "watch_later") return library.items.filter((item) => item.watchLater);
    return library.items.filter((item) => item.collections.some((tag) => tag.id === selection.id));
  }, [library.items, selection]);
  const localResults = useMemo(() => visibleItems.map((item) => item.result), [visibleItems]);
  const activeCollection = selection.kind === "collection"
    ? library.collections.find((item) => item.id === selection.id) ?? null
    : null;
  const localCardCover = (matches: typeof library.items, collectionId?: number) => {
    const ordered = [...matches].sort((left, right) => {
      const leftTag = collectionId === undefined ? null : left.collections.find((tag) => tag.id === collectionId);
      const rightTag = collectionId === undefined ? null : right.collections.find((tag) => tag.id === collectionId);
      // 自建夹优先按「加入该夹」时间；内置分类没有独立归属时间时才退回收藏时间。
      return stableTime(rightTag?.addedAt ?? right.savedAt) - stableTime(leftTag?.addedAt ?? left.savedAt);
    });
    return ordered.find((item) => item.result.cover_url)?.result.cover_url ?? null;
  };
  const localFolderCards = useMemo(() => ({
    all: { count: savedCount, cover: localCardCover(library.items.filter((item) => item.saved)) },
    unclassified: { count: unclassifiedCount, cover: localCardCover(library.items.filter((item) => item.saved && !item.inDefault && !item.collections.length)) },
    default: { count: defaultCount, cover: localCardCover(library.items.filter((item) => item.inDefault)) },
    watchLater: { count: watchLaterCount, cover: localCardCover(library.items.filter((item) => item.watchLater)) },
    collections: library.collections.map((collection) => ({
      ...collection,
      cover: localCardCover(library.items.filter((item) => item.collections.some((tag) => tag.id === collection.id)), collection.id),
    })),
  }), [library.items, library.collections, savedCount, unclassifiedCount, defaultCount, watchLaterCount]);

  // 收藏夹被删除后回到「全部」
  useEffect(() => {
    if (selection.kind === "collection" && !library.collections.some((item) => item.id === selection.id)) {
      setSelection({ kind: "all" });
    }
  }, [library.collections, selection]);

  useEffect(() => {
    const migration = library.stats?.migration;
    if (!migration || !migration.source_count) return;
    const id = migration.id || `${migration.source_count}-${migration.local_items}-${migration.remote_items}`;
    const key = `${RECOVERY_NOTICE_PREFIX}${id}`;
    try {
      if (window.localStorage.getItem(key) === "1") return;
      window.localStorage.setItem(key, "1");
    } catch {
      // Storage permissions do not affect the recovered database.
    }
    setShowRecoveryNotice(true);
  }, [library.stats?.migration]);

  // 切换选择时清空勾选
  useEffect(() => {
    setSelectedKeys([]);
  }, [selection]);

  const data = remote.data;
  const retainedCounts = useMemo(() => Object.fromEntries(PLATFORMS.map((platform) => [
    platform,
    data?.counts?.[platform] ?? new Set((data?.results ?? []).flatMap(resultSources)
      .filter((result) => result.platform === platform)
      .map((result) => result.content_id)).size,
  ])) as Record<PlatformSlug, number>, [data]);
  const fetchedAt = useMemo(
    () => Object.fromEntries(PLATFORMS.map((platform) => [platform, data?.platforms[platform]?.synced_at ?? null])),
    [data],
  );

  useEffect(() => {
    if (tab !== "remote") return;
    let alive = true;
    axios.get<RemoteFolder[]>("/api/search/favorites/folders")
      .then(({ data: folders }) => { if (alive) setRemoteFolders(folders); })
      .catch(() => { if (alive) setRemoteFolders([]); });
    return () => { alive = false; };
  }, [tab, data?.data_version, remote.busy]);

  const loadRemoteFolder = async (folder: RemoteFolder, offset = 0) => {
    const request = ++folderRequest.current;
    setRemoteFolderLoading(true);
    try {
      const { data: page } = await axios.get<RemoteFolderItems>(
        `/api/search/favorites/folders/${encodeURIComponent(folder.account)}/${encodeURIComponent(folder.folder)}`,
        { params: { offset, limit: 50 } },
      );
      if (request !== folderRequest.current) return;
      setRemoteFolderItems((previous) => offset && previous
        ? { ...page, items: [...previous.items, ...page.items] }
        : page);
    } finally {
      if (request === folderRequest.current) setRemoteFolderLoading(false);
    }
  };

  const openRemoteFolder = (folder: RemoteFolder) => {
    gridScrollY.current = window.scrollY;
    setRemoteFolderView(folder);
    setRemoteFolderItems(null);
    void loadRemoteFolder(folder);
  };
  const closeRemoteFolder = () => {
    folderRequest.current += 1;
    setRemoteFolderView(null);
    setRemoteFolderItems(null);
    requestAnimationFrame(() => window.scrollTo({ top: gridScrollY.current }));
  };

  const togglePlatform = (platform: PlatformSlug) => setSelected((before) => {
    const next = new Set(before);
    if (next.has(platform) && next.size > 1) next.delete(platform);
    else next.add(platform);
    return next;
  });

  const submitNewCollection = async () => {
    const name = newName.trim();
    if (!name) return;
    setMoving(true);
    const ok = await library.createCollection(name);
    setMoving(false);
    if (ok) {
      setNewName("");
      setCreating(false);
    }
  };

  const submitRename = async (id: number) => {
    const name = renameValue.trim();
    if (!name) return;
    setMoving(true);
    const ok = await library.renameCollection(id, name);
    setMoving(false);
    if (ok) {
      setRenaming(null);
      setRenameValue("");
    }
  };

  /**
   * 结果列表按「分组」勾选（groupKey 是 JSON 数组串），而收藏库接口只认单条
   * `platform|content_id`，所以批量操作前统一转换并去重。
   */
  const selectedItemKeys = useMemo(
    () => [...new Set(selectedKeys.flatMap(parseGroupKey))],
    [selectedKeys],
  );

  const batchAdd = async (collectionId: number) => {
    if (!selectedItemKeys.length) return;
    setMoving(true);
    const ok = await library.addToCollection(selectedItemKeys, collectionId);
    setMoving(false);
    if (ok) {
      setSelectedKeys([]);
      setSelectionResetKey((value) => value + 1);
    }
  };

  const batchRemove = async (collectionId: number) => {
    if (!selectedItemKeys.length) return;
    setMoving(true);
    const ok = await library.removeFromCollection(selectedItemKeys, collectionId);
    setMoving(false);
    if (ok) {
      setSelectedKeys([]);
      setSelectionResetKey((value) => value + 1);
    }
  };

  const batchSystem = async (collection: "default" | "watch_later", enabled: boolean) => {
    if (!selectedItemKeys.length) return;
    setMoving(true);
    const ok = await library.setSystemMembership(selectedItemKeys, collection, enabled);
    setMoving(false);
    if (ok) {
      setSelectedKeys([]);
      setSelectionResetKey((value) => value + 1);
    }
  };

  const batchDelete = async () => {
    if (!selectedItemKeys.length || !window.confirm(`确定从本机彻底删除这 ${selectedItemKeys.length} 条收藏吗？此操作会同时移除所有收藏夹归属。`)) return;
    setMoving(true);
    const ok = await library.deleteItems(selectedItemKeys);
    setMoving(false);
    if (ok) {
      setSelectedKeys([]);
      setSelectionResetKey((value) => value + 1);
    }
  };

  const localHeading = activeCollection
    ? `「${customCollectionLabel(activeCollection.name)}」共 ${activeCollection.item_count} 条 · 收藏与备注保存在本机`
    : selection.kind === "default"
      ? `${defaultCount} 条默认收藏 · 点击收藏按钮可独立加入或移出`
      : selection.kind === "watch_later"
        ? `${watchLaterCount} 条稍后再看 · 不会修改平台原生收藏`
        : selection.kind === "unclassified"
          ? `${unclassifiedCount} 条未分类内容 · 尚未加入默认或自建收藏夹`
        : `${savedCount} 条已收藏 · 收藏与备注保存在本机数据库`;

  const setLocalView = (view: LocalFolderView) => {
    setLocalFolderView(view);
    writeLocalFolderView(view);
    if (view === "icon" && selection.kind === "all") setLocalFolderRoot(true);
    if (view === "list") setLocalFolderRoot(false);
  };
  const openLocalFolder = (next: LibrarySelection) => {
    localGridScrollY.current = window.scrollY;
    setSelection(next);
    setLocalFolderRoot(false);
  };
  const closeLocalFolder = () => {
    setLocalFolderRoot(true);
    requestAnimationFrame(() => window.scrollTo({ top: localGridScrollY.current }));
  };

  return (
    <div className="preview-container favorites-page">
      <div className="collection-heading">
        <div className="page-heading">
          <div>
            <p className="eyebrow">YOUR COLLECTION</p>
            <h1>留住值得再看的内容</h1>
            <p className="description">{tab === "local" ? "给有用的内容一个位置，也记下自己的想法。" : "把不同平台的收藏放在一起，慢慢阅读。"}</p>
          </div>
          {tab === "local" && <div className="local-heading-actions">
            <div className="local-view-switch" role="group" aria-label="本地收藏夹浏览方式">
              <button type="button" className={localFolderView === "list" ? "active" : ""} aria-pressed={localFolderView === "list"} onClick={() => setLocalView("list")}><List aria-hidden="true" />列表</button>
              <button type="button" className={localFolderView === "icon" ? "active" : ""} aria-pressed={localFolderView === "icon"} onClick={() => setLocalView("icon")}><Grid2X2 aria-hidden="true" />图标</button>
            </div>
            <details className="backup-details"><summary className="btn">备份管理</summary><BookmarkBackup library={library} /></details>
          </div>}
        </div>

        <nav className="tabs collection-switch" aria-label="收藏类型">
          <button type="button" className={`tab ${tab === "local" ? "active" : ""}`} onClick={() => setTab("local")}>本地收藏 <span>{library.items.length}</span></button>
          <button type="button" className={`tab ${tab === "remote" ? "active" : ""}`} onClick={() => setTab("remote")}>跨平台收藏</button>
        </nav>

        {tab === "local" ? (
          <p className="collection-count">{localHeading}</p>
        ) : (
          <>
            <div className="page-heading remote-heading">
              <div><h2>跨平台收藏</h2><p className="description">从已登录的平台读取收藏，可再保存到本地。</p></div>
              <button type="button" className="btn primary" disabled={remote.cancelling || (!remote.canCancel && (remote.busy || !selected.size))} onClick={() => remote.canCancel ? void remote.cancel() : void remote.sync([...selected])}>
                {remote.busy || remote.cancelling ? <Loader2 className="spinner" aria-hidden="true" /> : <RefreshCw />}
                {remote.cancelling ? "正在取消" : remote.canCancel ? "正在同步 · 取消" : remote.busy ? "正在启动同步" : "同步所选平台 / 继续"}
              </button>
            </div>
            <div className="scope collection-scope" aria-label="收藏同步平台">
              <span className="scope-label">同步范围</span>
              {PLATFORMS.map((platform) => <button key={platform} type="button" className="platform-choice" aria-pressed={selected.has(platform)} disabled={remote.busy} onClick={() => togglePlatform(platform)}>
                <i className="pd" style={{ backgroundColor: PLATFORM_COLORS[platform] }} />{PLATFORM_LABELS[platform]}<span className="check">{selected.has(platform) ? "✓" : ""}</span>
              </button>)}
            </div>
            <p className="collection-count">
              B站、知乎读取接口可返回的完整收藏分页（不限产品侧条数）；小红书仍为单一收藏笔记入口 · 同步只读取，不会修改平台收藏。
              <button type="button" className="text-link" disabled={!selected.size || remote.busy}
                onClick={() => void remote.sync([...selected], "full")}>完整重扫</button>
            </p>
          </>
        )}
      </div>

      {library.error && <div className="status-line" role="alert"><AlertTriangle />{library.error}</div>}
      {tab === "local" && showRecoveryNotice && (library.stats?.migration?.source_count ?? 0) > 0 && (
        <div className="status-line" role="status"><Check />
          已从 {library.stats?.migration?.source_count} 个旧版本数据目录恢复收藏：本地新增 {library.stats?.migration?.local_items ?? 0} 条，跨平台缓存新增 {library.stats?.migration?.remote_items ?? 0} 条。
        </div>
      )}
      {tab === "local" && (library.stats?.migration?.warnings?.length ?? 0) > 0 && (
        <div className="status-line" role="alert"><AlertTriangle />部分旧收藏库未能读取，原文件没有被修改。请从「备份管理」导入可用备份。</div>
      )}
      {tab === "remote" && remote.error && <div className="status-line" role="alert"><AlertTriangle />{errorMessage(remote.error)}<button type="button" className="text-link" onClick={onNavigateAccounts}>检查账号状态</button></div>}
      {tab === "remote" && data?.persistence_error && <div className="status-line" role="alert"><AlertTriangle />{data.persistence_error}</div>}

      {tab === "local" && library.migration && (
        <div className="status-line" role="alert">
          <AlertTriangle />
          <span className="status-message">
            检测到 {library.migration.count} 条旧版浏览器收藏（新版已改为保存在本机数据库）。迁移不会删除旧数据，备份文件仍可随时导出。
          </span>
          <button type="button" className="text-link" disabled={moving} onClick={() => void library.runMigration()}>迁移到本机收藏库</button>
          <button type="button" className="text-link" onClick={library.dismissMigration}>以后再说</button>
        </div>
      )}

      {tab === "local" && localFolderView === "icon" && localFolderRoot ? (
        <section className="local-folder-browser" aria-label="本地收藏夹图标模式">
          <div className="local-folder-intro">
            <div><span className="eyebrow">LOCAL FOLDERS</span><h2>本地收藏夹</h2><p>封面取最近加入该夹的已有缩略图；内置分类没有独立归属时间时按本机收藏时间排序。</p></div>
            {creating ? <span className="library-inline-form local-folder-create"><input className="field" aria-label="新收藏夹名称" placeholder="收藏夹名称" value={newName} autoFocus maxLength={60} onChange={(event) => setNewName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submitNewCollection(); if (event.key === "Escape") { setCreating(false); setNewName(""); } }} /><button type="button" className="icon-btn" aria-label="创建收藏夹" disabled={moving} onClick={() => void submitNewCollection()}><Check /></button><button type="button" className="icon-btn" aria-label="取消创建" onClick={() => { setCreating(false); setNewName(""); }}><X /></button></span> : <button type="button" className="btn small" onClick={() => setCreating(true)}><FolderPlus aria-hidden="true" />新建收藏夹</button>}
          </div>
          <section className="local-folder-section" aria-label="快捷分类"><h3>快捷分类</h3><div className="local-folder-grid">
            {([
              ["all", "全部收藏", Bookmark, localFolderCards.all],
              ["unclassified", "未分类", FolderHeart, localFolderCards.unclassified],
              ["default", "默认收藏夹", FolderHeart, localFolderCards.default],
              ["watch_later", "稍后再看", Clock3, localFolderCards.watchLater],
            ] as const).map(([kind, label, Icon, card]) => <article className="local-folder-card" key={kind}>
              <button type="button" className="local-folder-open" onClick={() => openLocalFolder({ kind })}><span className="local-folder-stack" aria-hidden="true"><span /><span /></span><span className="local-folder-cover"><Icon />{card.cover && <img src={card.cover} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />}</span><span className="local-folder-name" title={label}>{label}</span><span className="local-folder-meta"><span>本地分类</span><b>{card.count}</b></span></button>
            </article>)}
          </div></section>
          <section className="local-folder-section" aria-label="自建收藏夹"><h3>自建收藏夹</h3>
            {localFolderCards.collections.length ? <div className="local-folder-grid">{localFolderCards.collections.map((collection) => <article className="local-folder-card" key={collection.id}>
              <button type="button" className="local-folder-open" onClick={() => openLocalFolder({ kind: "collection", id: collection.id })}><span className="local-folder-stack" aria-hidden="true"><span /><span /></span><span className="local-folder-cover"><FolderHeart />{collection.cover && <img src={collection.cover} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />}</span><span className="local-folder-name" title={customCollectionLabel(collection.name)}>{customCollectionLabel(collection.name)}</span><span className="local-folder-meta"><span>本地收藏夹</span><b>{collection.item_count}</b></span></button>
              <details className="local-folder-actions" onClick={(event) => event.stopPropagation()}><summary aria-label={`更多操作：${collection.name}`}><MoreHorizontal /></summary>{renaming === collection.id ? <span className="local-card-rename"><input className="field" aria-label={`重命名收藏夹 ${collection.name}`} value={renameValue} autoFocus maxLength={60} onChange={(event) => setRenameValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submitRename(collection.id); if (event.key === "Escape") setRenaming(null); }} /><button type="button" onClick={() => void submitRename(collection.id)}><Check /></button><button type="button" onClick={() => setRenaming(null)}><X /></button></span> : <><button type="button" onClick={() => { setRenaming(collection.id); setRenameValue(collection.name); }}><Pencil />重命名</button><button type="button" className="danger" onClick={() => void library.deleteCollection(collection.id)}><Trash2 />删除</button></>}</details>
            </article>)}</div> : <p className="local-folder-empty">还没有自建收藏夹。新建后，可在内容列表中把同一条内容放进多个收藏夹。</p>}
          </section>
        </section>
      ) : tab === "local" ? (
        <div className="library-layout">
          <aside className="library-side" aria-label="收藏夹">
            <button
              type="button"
              className={`library-side-item ${selection.kind === "all" ? "active" : ""}`}
              onClick={() => setSelection({ kind: "all" })}
            >
              <Bookmark aria-hidden="true" />全部<span>{savedCount}</span>
            </button>
            <button
              type="button"
              className={`library-side-item ${selection.kind === "unclassified" ? "active" : ""}`}
              onClick={() => setSelection({ kind: "unclassified" })}
            >
              <FolderHeart aria-hidden="true" />未分类<span>{unclassifiedCount}</span>
            </button>
            <button
              type="button"
              className={`library-side-item ${selection.kind === "default" ? "active" : ""}`}
              onClick={() => setSelection({ kind: "default" })}
            >
              <FolderHeart aria-hidden="true" />默认收藏夹<span>{defaultCount}</span>
            </button>
            <button
              type="button"
              className={`library-side-item ${selection.kind === "watch_later" ? "active" : ""}`}
              onClick={() => setSelection({ kind: "watch_later" })}
            >
              <Clock3 aria-hidden="true" />稍后再看<span>{watchLaterCount}</span>
            </button>

            <p className="library-side-title">收藏夹</p>
            {library.collections.map((collection) => (
              <div key={collection.id} className="library-side-row">
                {renaming === collection.id ? (
                  <span className="library-inline-form">
                    <input
                      className="field"
                      aria-label={`重命名收藏夹 ${collection.name}`}
                      value={renameValue}
                      autoFocus
                      maxLength={60}
                      onChange={(event) => setRenameValue(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void submitRename(collection.id);
                        if (event.key === "Escape") setRenaming(null);
                      }}
                    />
                    <button type="button" className="icon-btn" aria-label="确认重命名" disabled={moving} onClick={() => void submitRename(collection.id)}><Check /></button>
                    <button type="button" className="icon-btn" aria-label="取消重命名" onClick={() => setRenaming(null)}><X /></button>
                  </span>
                ) : (
                  <>
                    <button
                      type="button"
                      className={`library-side-item ${selection.kind === "collection" && selection.id === collection.id ? "active" : ""}`}
                      onClick={() => setSelection({ kind: "collection", id: collection.id })}
                    >
                      <FolderHeart aria-hidden="true" /><span className="library-folder-name" title={collection.name}>{customCollectionLabel(collection.name)}</span><span className="library-folder-count">{collection.item_count}</span>
                    </button>
                    <button
                      type="button"
                      className="icon-btn library-side-action"
                      aria-label={`重命名收藏夹 ${collection.name}`}
                      onClick={() => { setRenaming(collection.id); setRenameValue(collection.name); }}
                    ><Pencil /></button>
                    <button
                      type="button"
                      className="icon-btn library-side-action"
                      aria-label={`删除收藏夹 ${collection.name}`}
                      onClick={() => void library.deleteCollection(collection.id)}
                    ><Trash2 /></button>
                  </>
                )}
              </div>
            ))}

            {creating ? (
              <span className="library-inline-form">
                <input
                  className="field"
                  aria-label="新收藏夹名称"
                  placeholder="收藏夹名称"
                  value={newName}
                  autoFocus
                  maxLength={60}
                  onChange={(event) => setNewName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void submitNewCollection();
                    if (event.key === "Escape") { setCreating(false); setNewName(""); }
                  }}
                />
                <button type="button" className="icon-btn" aria-label="创建收藏夹" disabled={moving} onClick={() => void submitNewCollection()}><Check /></button>
                <button type="button" className="icon-btn" aria-label="取消创建" onClick={() => { setCreating(false); setNewName(""); }}><X /></button>
              </span>
            ) : (
              <button type="button" className="library-new" onClick={() => setCreating(true)}><FolderPlus aria-hidden="true" />新建收藏夹</button>
            )}
          </aside>

          <div className="library-main">
            {localFolderView === "icon" && <button type="button" className="text-link remote-folder-back" onClick={closeLocalFolder}><ArrowLeft />返回收藏夹</button>}
            {selectedKeys.length > 0 && (
              <div className="library-batch" role="status">
                <span>已选 {selectedItemKeys.length} 条</span>
                <label>
                  <span className="sr-only">加入收藏夹</span>
                  <select
                    className="field select"
                    aria-label="加入收藏夹"
                    value=""
                    disabled={moving}
                    onChange={(event) => {
                      const value = event.target.value;
                      if (value === "system:default") void batchSystem("default", true);
                      else if (value === "system:watch_later") void batchSystem("watch_later", true);
                      else if (value.startsWith("custom:")) void batchAdd(Number(value.slice(7)));
                    }}
                  >
                    <option value="">加入收藏夹…</option>
                    <option value="system:default">默认收藏夹</option>
                    <option value="system:watch_later">稍后再看</option>
                    {library.collections.map((collection) => <option key={collection.id} value={`custom:${collection.id}`}>{customCollectionLabel(collection.name)}</option>)}
                  </select>
                </label>
                <label>
                  <span className="sr-only">移出收藏夹</span>
                  <select
                    className="field select"
                    aria-label="移出收藏夹"
                    value=""
                    disabled={moving}
                    onChange={(event) => {
                      const value = event.target.value;
                      if (value === "system:default") void batchSystem("default", false);
                      else if (value === "system:watch_later") void batchSystem("watch_later", false);
                      else if (value.startsWith("custom:")) void batchRemove(Number(value.slice(7)));
                    }}
                  >
                    <option value="">移出收藏夹…</option>
                    <option value="system:default">默认收藏夹</option>
                    <option value="system:watch_later">稍后再看</option>
                    {library.collections.map((collection) => <option key={collection.id} value={`custom:${collection.id}`}>{customCollectionLabel(collection.name)}</option>)}
                  </select>
                </label>
                <button type="button" className="btn danger" disabled={moving} onClick={() => void batchDelete()}>从本机彻底删除</button>
              </div>
            )}

            {library.loading && !library.items.length ? (
              <div className="empty" role="status"><div className="empty-symbol"><Loader2 className="spinner" /></div><h2>正在读取本机收藏</h2><p>收藏保存在本机数据库，清缓存或换浏览器都不会丢。</p></div>
            ) : localResults.length ? (
              /* key 里带上当前收藏夹：切换时强制重挂载，内部勾选/筛选/平台页签一并重置，
                 避免上一个收藏夹里勾选的内容泄漏到下一个视图的批量操作里 */
              <ResultTabs
                key={`${selection.kind}-${selection.kind === "collection" ? selection.id : "system"}`}
                results={localResults}
                overall="completed"
                platforms={PLATFORMS}
                library={library}
                savedView
                jobId="bookmarks"
                onSelectionChange={setSelectedKeys}
                selectionToolLabel="批量管理"
                selectionResetKey={selectionResetKey}
              />
            ) : (
              <div className="empty">
                <div className="empty-symbol"><Bookmark /></div>
                <h2>{activeCollection ? "这个收藏夹还是空的" : selection.kind === "default" ? "默认收藏夹还是空的" : selection.kind === "watch_later" ? "还没有稍后再看的内容" : "收藏夹还空着"}</h2>
                <p>{activeCollection ? "在「全部」里勾选内容，再选择「加入收藏夹」。同一条内容可以同时属于多个收藏夹。" : selection.kind === "watch_later" ? "在结果右侧点“稍后再看”，需要时再回来。" : "遇到值得再读的内容，点一下结果右侧的收藏图标。"}</p>
              </div>
            )}
          </div>
        </div>
      ) : remote.busy && !data?.results.length ? (
        <div className="empty" role="status"><div className="empty-symbol"><Loader2 className="spinner" /></div><h2>正在整理你的收藏</h2><p>先获取列表，再补充内容信息。</p></div>
      ) : data ? (
        <>
          <p className="collection-count">本机已保存 {data.results.length} 条 · 同步只读取并保存观察到的内容，未取到的旧内容保留；要彻底对齐可用「完整重扫」</p>
          <div className="progress-strip" aria-live="polite">
            {Object.entries(data.platforms).map(([platform, info]) => info && <span key={platform} className="progress-item">
              {PLATFORM_LABELS[platform as PlatformSlug]}：{info.status === "running" ? "同步中" : STATUS_LABELS[info.status]} · 本次读取并保存 {info.result_count} 条 · 本机保留 {retainedCounts[platform as PlatformSlug]} 条
              {info.synced_at && <small>同步于 {new Date(info.synced_at).toLocaleString("zh-CN")}</small>}
              {info.phase && <small>{info.phase}</small>}
            </span>)}
          </div>
          {!remote.busy && Object.values(data.platforms).some(info => info?.status === "cancelled") && <p className="collection-count" role="status">同步已取消，已获取的内容和历史收藏仍会保留。</p>}
          {Object.entries(data.platforms).some(([, info]) => info && !["succeeded", "empty", "running", "pending", "cancelled"].includes(info.status)) && <div className="status-line"><span className="status-message"><AlertTriangle />部分平台没有完成：{Object.entries(data.platforms).filter(([, info]) => info && !["succeeded", "empty", "running", "pending", "cancelled"].includes(info.status)).map(([platform, info]) => `${PLATFORM_LABELS[platform as PlatformSlug]} ${info?.error_summary || STATUS_LABELS[info!.status]}`).join("；")}</span><button type="button" className="text-link" onClick={onNavigateAccounts}>检查账号</button></div>}
          {remoteFolderView ? (
            <section className="remote-folder-detail" aria-label={`平台收藏夹 ${remoteFolderView.name}`}>
              <button type="button" className="text-link remote-folder-back" onClick={closeRemoteFolder}><ArrowLeft />返回收藏夹</button>
              <div className="remote-folder-title"><div><span className="remote-readonly">平台只读</span><h2>{remoteFolderView.name}</h2><p>{PLATFORM_LABELS[remoteFolderView.platform]} · 本机已同步 {remoteFolderView.item_count} 条</p></div></div>
              {remoteFolderLoading && !remoteFolderItems ? <div className="empty"><Loader2 className="spinner" /><p>正在读取本机镜像…</p></div> : remoteFolderItems?.items.length ? <>
                <p className="collection-count">当前已加载 {remoteFolderItems.items.length} / {remoteFolderItems.total} 条；搜索、排序和导出仅作用于当前已加载内容。</p>
                <ResultTabs key={`${remoteFolderView.account}-${remoteFolderView.folder}-${remoteFolderItems.items.length}`} results={remoteFolderItems.items} overall="completed" jobId="remote-folder" platforms={[remoteFolderView.platform]} library={library} pageSize={remoteFolderItems.items.length} />
                {remoteFolderItems.items.length < remoteFolderItems.total && <div className="library-batch-bar"><span>还有 {remoteFolderItems.total - remoteFolderItems.items.length} 条本机镜像</span><button type="button" className="btn small" disabled={remoteFolderLoading} onClick={() => void loadRemoteFolder(remoteFolderView, remoteFolderItems.items.length)}>{remoteFolderLoading ? "正在读取" : "显示更多"}</button></div>}
              </> : <div className="empty"><div className="empty-symbol"><FolderHeart /></div><h2>这个平台收藏夹还是空的</h2><p>{remoteFolderView.last_content_complete_at ? "已完成读取，平台当前没有可保存内容。" : "目录已同步，内容仍待下一次手动同步读取。"}</p></div>}
            </section>
          ) : <>
            {remoteFolders.length > 0 && <section className="remote-folder-section" aria-label="平台收藏夹">
              <div className="remote-folder-intro"><div><span className="eyebrow">PLATFORM FOLDERS</span><h2>平台收藏夹</h2><p>只读镜像；点开后可将内容另存到自己的本地收藏夹。</p></div><span className="remote-readonly">名称与归属来自平台</span></div>
              <div className="remote-folder-grid">
                {remoteFolders.map((folder) => <button type="button" className="remote-folder-card" key={`${folder.platform}:${folder.account}:${folder.folder}`} onClick={() => openRemoteFolder(folder)}>
                  <span className="remote-folder-stack" aria-hidden="true"><span /><span /></span>
                  <span className="remote-folder-cover"><FolderHeart />{folder.cover_url && <img src={folder.cover_url} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />}</span>
                  <span className="remote-folder-name" title={folder.name}>{folder.name}</span><span className="remote-folder-meta"><i className="pd" style={{ backgroundColor: PLATFORM_COLORS[folder.platform] }} />{PLATFORM_LABELS[folder.platform]} · {folder.observed_state === "not_found" ? "平台暂未找到" : "只读"}<b>{folder.item_count}</b></span>
                </button>)}
              </div>
            </section>}
            {/* 保留 V0.3 的平台页签和全部内容入口；它不冒充一个真实平台收藏夹。 */}
            <section className="remote-all-section"><h2>全部内容</h2><p className="collection-count">含未归属的旧镜像；这里的搜索、排序和导出作用于已加载的跨平台内容。</p><ResultTabs results={data.results} overall={data.overall} jobId={data.job_id} platforms={Object.keys(data.platforms) as PlatformSlug[]} library={library} fetchedAt={fetchedAt} pageSize={100} /></section>
          </>}
        </>
      ) : (
        <div className="empty"><div className="empty-symbol"><FolderHeart /></div><h2>把喜欢的内容，放到一起</h2><p>选择平台后点击同步，四处散落的收藏会汇到这里。</p><button type="button" className="btn" onClick={() => void remote.sync([...selected])}>同步收藏</button></div>
      )}
    </div>
  );
}
