import { Clock, History, X, Trash2, TrendingUp } from "lucide-react";
import type { SearchHistoryItem } from "@/lib/searchExperience";
import { RECOMMENDED_SEARCHES } from "@/lib/searchPopover";
import { PLATFORM_LABELS } from "@/types/search";
import { useTranslation } from "react-i18next";

interface SearchPopoverProps {
  history: SearchHistoryItem[];
  disabled: boolean; // 搜索/取消/重试进行中（浮层内交互禁用）
  onItemClick: (item: SearchHistoryItem) => void;
  onRemove: (index: number) => void;
  onClear: () => void;
  onRecommend: (keyword: string) => void;
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const diff = Date.now() - d.getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes}分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}小时前`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}天前`;
    return d.toLocaleDateString("zh-CN");
  } catch {
    return "";
  }
}

function platformInitials(platforms: string[]): string {
  return platforms
    .map((p) => PLATFORM_LABELS[p as keyof typeof PLATFORM_LABELS]?.charAt(0) || p)
    .join("·");
}

/**
 * 搜索下拉浮层：最近搜索 + 推荐搜索。
 * - 点击历史项只填入关键词，旧平台组合仅作为历史信息展示；
 * - 点击推荐词同样只填入关键词；
 * - 关闭规则由 lib/searchPopover 的 reducer 驱动（focus_left / search_started / picked）。
 */
export function SearchPopover({
  history,
  disabled,
  onItemClick,
  onRemove,
  onClear,
  onRecommend,
}: SearchPopoverProps) {
  const { t } = useTranslation();
  return (
    <div className="absolute left-[-11px] right-[-11px] top-[calc(100%+8px)] z-20 border border-cyber-border-subtle rounded-b-[20px] bg-cyber-bg-secondary shadow-[0_28px_58px_rgba(35,55,46,0.16)] p-5 animate-dsh-drop">
      {history.length > 0 && (
        <>
          <div className="flex items-center justify-between mb-1.5">
            <strong className="text-[12px] text-cyber-text-primary flex items-center gap-1.5">
              <History className="w-3.5 h-3.5 text-cyber-text-muted" />
              {t("search.recent")}
            </strong>
            <button
              type="button"
              onClick={onClear}
              disabled={disabled}
              className="flex items-center gap-1 text-[11px] text-cyber-text-muted hover:text-warn transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Trash2 className="w-3 h-3" />{t("search.clear")}
            </button>
          </div>
          <ul className="max-h-[240px] overflow-y-auto">
            {history.map((item, index) => {
              const time = formatTime(item.searchedAt);
              return (
                <li key={`${item.keyword}-${item.searchedAt}-${index}`} className="flex items-center">
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onItemClick(item)}
                    title={t("search.historyItemTitle", {
                      keyword: item.keyword,
                      platforms: item.platforms.map((p) => PLATFORM_LABELS[p as keyof typeof PLATFORM_LABELS] || p).join("、"),
                    })}
                    className="flex-1 flex items-center gap-3 rounded-[10px] px-2 py-2.5 text-left hover:bg-brand-soft transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <Clock className="w-[14px] h-[14px] text-cyber-text-muted flex-shrink-0" />
                    <b className="text-[13px] font-semibold text-cyber-text-primary truncate">{item.keyword}</b>
                    <small className="ml-auto flex-shrink-0 text-[10.5px] text-cyber-text-muted">
                      {t("search.historyPlatforms", { platforms: platformInitials(item.platforms) })}
                      {time && ` · ${time}`}
                    </small>
                  </button>
                  <button
                    type="button"
                    aria-label={t("search.deleteHistory", { keyword: item.keyword })}
                    onClick={() => onRemove(index)}
                    disabled={disabled}
                    className="ml-0.5 flex items-center px-1.5 py-1 text-cyber-text-muted/60 hover:text-warn transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="h-px bg-cyber-border-subtle my-2.5" />
        </>
      )}

      <div className="mb-1.5">
        <strong className="text-[12px] text-cyber-text-primary flex items-center gap-1.5">
          <TrendingUp className="w-3.5 h-3.5 text-cyber-text-muted" />
          {t("search.recommended")}
        </strong>
      </div>
      <div className="flex flex-wrap gap-2">
        {RECOMMENDED_SEARCHES.map((word) => (
          <button
            key={word}
            type="button"
            disabled={disabled}
            onClick={() => onRecommend(word)}
            className="rounded-full border border-cyber-border-subtle px-3 py-1.5 text-[11.5px] text-cyber-text-secondary hover:border-brand hover:text-brand-strong transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {word}
          </button>
        ))}
      </div>
    </div>
  );
}
