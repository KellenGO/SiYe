import { useState, useMemo, useEffect, useRef } from "react";
import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import type { SearchSortMode } from "@/lib/searchExperience";
import {
  resolveActiveTab,
  sortResults,
} from "@/lib/searchExperience";
import { ResultCard } from "./ResultCard";
import { BookmarkControl, BookmarkNote, ExportActions, WatchLaterControl } from "./ResultTools";
import type { BookmarkLibrary } from "@/hooks/useBookmarks";
import { DEFAULT_FILTERS, exportRows, filterResultGroups, groupKey, resultKey, type ResultFilters } from "@/lib/resultTools";

interface ResultTabsProps {
  results: UnifiedSearchResult[];
  keyword?: string;
  overall: string;
  jobId?: string;
  hydrationStatus?: "not_started" | "running" | "completed";
  platforms: PlatformSlug[];
  sortMode?: SearchSortMode;
  onSortModeChange?: (mode: SearchSortMode) => void;
  library?: BookmarkLibrary;
  savedView?: boolean;
  fetchedAt?: Partial<Record<PlatformSlug, string | null>>;
  /** 关闭内置排序、严格按传入顺序渲染（历史页按最近浏览倒序时需要）。 */
  disableSort?: boolean;
  /** 每条结果的可删除回调（历史页用来删除单条记录）。 */
  onDeleteItem?: (result: UnifiedSearchResult) => void;
  /** 勾选状态变化时回调（收藏夹页用同一套勾选做批量加入 / 移出）。 */
  onSelectionChange?: (keys: string[]) => void;
  /** 勾选工具按钮在收起状态的文案，默认“导出 / 复制”。 */
  selectionToolLabel?: string;
  selectionResetKey?: number;
}

type TabKey = "all" | PlatformSlug;

/** Round 14：平台结果标签固定为五个（全部 / 小红书 / 抖音 / B站 / 知乎）。 */
const ALL_TABS: { key: TabKey; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "xhs", label: "小红书" },
  { key: "douyin", label: "抖音" },
  { key: "bilibili", label: "B站" },
  { key: "zhihu", label: "知乎" },
];

const SORT_MODES: { key: SearchSortMode; label: string }[] = [
  { key: "default", label: "综合" },
  { key: "latest", label: "最新" },
  { key: "engagement", label: "互动最多" },
];

export function ResultTabs({
  results,
  keyword = "",
  overall,
  jobId,
  hydrationStatus = "not_started",
  sortMode = "default",
  onSortModeChange,
  library,
  savedView = false,
  fetchedAt = {},
  onSelectionChange,
  selectionToolLabel,
  selectionResetKey,
  disableSort = false,
  onDeleteItem,
}: ResultTabsProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [filters, setFilters] = useState<ResultFilters>({ ...DEFAULT_FILTERS });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exportOpen, setExportOpen] = useState(false);
  // 收藏成功后的一次性提示：让用户当场改归属，不用跑到收藏页去找。
  const [membershipPrompt, setMembershipPrompt] = useState<{ group: string; keys: string[] } | null>(null);
  const nowMs = useMemo(() => Date.now(), [results, filters]);

  // 把勾选结果同步给外部（收藏夹页据此做批量加入 / 移出）
  useEffect(() => {
    onSelectionChange?.([...selected]);
  }, [selected, onSelectionChange]);

  // 五个固定标签始终可见；合法性判断仍走生产纯函数 resolveActiveTab，
  // 当激活标签不在可见集合时回退到"全部"（lib 内已直接测试）。
  const visibleTabs = ALL_TABS;
  const effectiveTab = resolveActiveTab<TabKey>(
    activeTab,
    visibleTabs.map(t => t.key),
    "all"
  );

  // 【组件 state 接线，人工验证】真正重置 activeTab state（而非仅钳制显示）：
  // - 失效时通过 effect 在渲染后 setActiveTab("all")，避免 render 阶段 setState；
  // - state 已变为 "all" 后，平台重新出现时不会自动恢复失效的旧标签。
  useEffect(() => {
    if (effectiveTab !== activeTab) {
      setActiveTab("all");
    }
  }, [activeTab, effectiveTab]);

  const hydrationOrderRef = useRef<{ signature: string; keys: string[] } | null>(null);
  useEffect(() => setSelected(new Set()), [jobId, effectiveTab, filters, selectionResetKey]);
  useEffect(() => setExportOpen(false), [jobId]);

  // 先按当前标签筛选，再按所选模式排序（纯前端计算，不发任何请求）。
  const filteredResults = useMemo(() => {
    const scoped = filterResultGroups(results, filters, nowMs, effectiveTab);
    const sorted = disableSort ? scoped : sortResults(scoped, sortMode, keyword);
    const resultKey = (r: UnifiedSearchResult) => `${r.platform}|${r.content_id}`;
    const signature = [
      jobId ?? "",
      effectiveTab,
      sortMode,
      keyword,
      JSON.stringify(filters),
      scoped.map(resultKey).sort().join(","),
    ].join("\u0001");
    if (hydrationStatus === "not_started") {
      hydrationOrderRef.current = { signature, keys: sorted.map(resultKey) };
      return sorted;
    }
    if (hydrationOrderRef.current?.signature !== signature) {
      hydrationOrderRef.current = { signature, keys: sorted.map(resultKey) };
      return sorted;
    }
    const byKey = new Map(sorted.map((result) => [resultKey(result), result]));
    return hydrationOrderRef.current.keys
      .map((key) => byKey.get(key))
      .filter((result): result is UnifiedSearchResult => Boolean(result));
  }, [results, effectiveTab, sortMode, keyword, hydrationStatus, jobId, filters, nowMs]);

  const counts = useMemo(() => {
    const matching = filterResultGroups(results, filters, nowMs);
    const c: Record<string, number> = { all: matching.length };
    for (const r of matching) {
      const sources = r.grouped_sources && r.grouped_sources.length >= 2
        ? r.grouped_sources
        : [r];
      for (const source of sources) {
        c[source.platform] = (c[source.platform] || 0) + 1;
      }
    }
    return c;
  }, [results, filters, nowMs]);

  const selectedResults = filteredResults.filter((result) => selected.has(groupKey(result)));
  const bookmarks = new Map((library?.items || []).map((item) => [resultKey(item.result), item]));
  const rows = exportRows(selectedResults.length ? selectedResults : filteredResults, (source) => {
    const saved = bookmarks.get(resultKey(source));
    return { fetchedAt: savedView ? saved?.fetchedAt ?? null : fetchedAt[source.platform] ?? null,
      savedAt: saved?.savedAt ?? null, note: saved?.note ?? "" };
  });
  const allSelected = filteredResults.length > 0 && selectedResults.length === filteredResults.length;
  const selectClass = "field select";

  return (
    <div className="results-block">
      <div className="tabs" role="tablist" aria-label="结果平台">
          {visibleTabs.map((tab) => {
            const count = counts[tab.key] || 0;
            const active = effectiveTab === tab.key;
            return (
              <button
                key={tab.key}
                role="tab"
                aria-selected={active}
                onClick={() => setActiveTab(tab.key)}
                className={`tab ${active ? "active" : ""}`}
              >
                {tab.label}
                <span>{count}</span>
              </button>
            );
          })}
      </div>

      <div className="toolbar">
        <div className="toolbar-left">
          {onSortModeChange && <label><span className="sr-only">结果排序</span><select className={selectClass} aria-label="排序方式" value={sortMode} onChange={(event) => onSortModeChange(event.target.value as SearchSortMode)}>{SORT_MODES.map((mode) => <option key={mode.key} value={mode.key}>{mode.label === "综合" ? "综合排序" : mode.label}</option>)}</select></label>}
          <input aria-label="结果内关键词" className="field filter-input" maxLength={200} placeholder={`在${savedView ? "收藏" : "结果"}中查找…`} value={filters.query} onChange={(event) => setFilters({ ...filters, query: event.target.value })} />
          {filters.query && <button type="button" className="text-link" onClick={() => setFilters({ ...filters, query: "" })}>清除筛选</button>}
        </div>
        <button type="button" className="text-link" aria-expanded={exportOpen} onClick={() => { setExportOpen(!exportOpen); setSelected(new Set()); }}>{exportOpen ? (selectionToolLabel ? "收起" : "收起导出") : (selectionToolLabel ?? "导出 / 复制")}</button>
      </div>

      {/* 结果卡片 */}
      {exportOpen && <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-cyber-border-subtle bg-cyber-bg-secondary p-3">
        <div className="flex flex-wrap items-center gap-3 text-xs text-cyber-text-muted">
          <label className="flex items-center gap-1.5"><input type="checkbox" aria-label="选择当前全部结果" checked={allSelected}
            disabled={!filteredResults.length} onChange={() => setSelected(allSelected ? new Set() : new Set(filteredResults.map(groupKey)))} />全选当前结果</label>
          <span>{selectedResults.length ? `已选 ${selectedResults.length} 条` : "未勾选时导出当前全部结果"} · 导出 {rows.length} 个来源</span>
        </div>
        <ExportActions rows={rows} keyword={savedView ? "本地收藏" : keyword} />
      </div>}
      <div className="result-list flex flex-col">
        {filteredResults.map((result, index) => {
          const key = groupKey(result);
          const bookmark = bookmarks.get(resultKey(result));
          // 刚收藏完的一次性小框：和收藏页用同一套，省得跑到收藏页去改归属
          const promptItem = membershipPrompt?.group === key
            ? library?.items.find((item) => membershipPrompt.keys.includes(item.key))
            : undefined;
          return (
            <div key={key} className={savedView || promptItem ? "saved-result-item" : undefined}>
              {exportOpen && <div className="mb-1.5 flex items-center gap-2 px-1">
                <label className="flex min-w-0 items-center gap-1.5 text-xs text-cyber-text-muted">
                  <input type="checkbox" aria-label={`选择 ${result.title}`} checked={selected.has(key)} onChange={() => setSelected((previous) => {
                    const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next;
                  })} />选择
                </label>
              </div>}
              <ResultCard result={result} index={index} highlightQuery={filters.query || keyword}
                renderBookmark={library ? (source) => <>
                  <BookmarkControl result={source} library={library} fetchedAt={fetchedAt}
                    onToggled={(added, keys) => setMembershipPrompt(added ? { group: groupKey(source), keys } : null)} />
                  <WatchLaterControl result={source} library={library} fetchedAt={fetchedAt} />
                </> : undefined}
                onDelete={onDeleteItem ? () => onDeleteItem(result) : undefined} />
              {/* 收藏页每条本来就常驻这个信息条，只有搜索结果页才需要"刚收藏完"弹出一次 */}
              {!savedView && promptItem && library && <BookmarkNote bookmark={promptItem} onSave={library.saveNote} library={library} />}
              {savedView && bookmark && library && <BookmarkNote bookmark={bookmark} onSave={library.saveNote} library={library} />}
            </div>
          );
        })}
      </div>

      {filteredResults.length === 0 && (
        <p className="text-center py-10 text-sm text-cyber-text-muted">
          {overall === "running" && !savedView ? "正在搜索中…" : savedView && !results.length ? "还没有收藏。在搜索结果上点击“收藏”，就能在这里查看。" : "没有符合条件的结果，可清除筛选或切换平台。"}
        </p>
      )}
    </div>
  );
}
