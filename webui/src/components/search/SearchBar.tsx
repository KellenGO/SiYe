import { useCallback, useEffect, useReducer, useRef, useState, FormEvent, Dispatch, SetStateAction } from "react";
import { ArrowRight, Search, Loader2, X, RotateCcw } from "lucide-react";
import type { PlatformSlug } from "@/types/search";
import { PLATFORM_LABELS, PLATFORM_COLORS } from "@/types/search";
import type { SearchHistoryItem } from "@/lib/searchExperience";
import { INITIAL_POPOVER_STATE, searchPopoverReducer } from "@/lib/searchPopover";
import type { PlatformLimitMap } from "@/lib/platformLimits";
import { SearchPopover } from "./SearchPopover";
import { PLATFORM_SLUGS } from "@/lib/platformMeta";
import { useTranslation } from "react-i18next";

const ALL_PLATFORMS = PLATFORM_SLUGS;


interface SearchBarProps {
  home?: boolean;
  keyword: string;
  onKeywordChange: Dispatch<SetStateAction<string>>;
  selectedPlatforms: Set<PlatformSlug>;
  onPlatformsChange: (platforms: PlatformSlug[]) => void;
  onSearch: (keyword: string, platforms: PlatformSlug[]) => void;
  isSearching: boolean;
  onCancel?: () => void;
  isCancelling?: boolean;
  onReset: () => void;
  // 下拉浮层：最近搜索 + 推荐搜索
  history: SearchHistoryItem[];
  onKeywordPick: (keyword: string) => void;
  keywordPickRequest: number;
  onHistoryRemove: (index: number) => void;
  onHistoryClear: () => void;
  // 每个平台独立搜索数量（仅展示）。
  limits: PlatformLimitMap;
  /**
   * 单独获取某个平台的结果（不重跑其它平台）。仅当已有本轮搜索时可用 ——
   * 它复用的是当前任务的关键词，平台是否处于勾选状态不影响。
   */
  onPlatformFetch?: (platform: PlatformSlug) => void;
  /** 正在单独获取的平台（显示转圈）。 */
  fetchingPlatform?: PlatformSlug | null;
}

export function SearchBar({
  home = false,
  keyword,
  onKeywordChange,
  selectedPlatforms,
  onPlatformsChange,
  onSearch,
  isSearching,
  onCancel,
  isCancelling,
  onReset,
  history,
  onKeywordPick,
  keywordPickRequest,
  onHistoryRemove,
  onHistoryClear,
  limits,
  onPlatformFetch,
  fetchingPlatform,
}: SearchBarProps) {
  const { t } = useTranslation();
  // 零勾选提交时的提示：不禁用按钮，仅提示先选平台；用户开始勾选即清除。
  const [platformPrompt, setPlatformPrompt] = useState(false);
  // 浮层开/关由生产 reducer 驱动（lib/searchPopover，node:test 已覆盖规则）。
  // 注意：reducer 状态是字符串 "open"/"closed"，两者都 truthy，
  // 因此 JSX 必须用 === "open" 判断，不能用 {popoverOpen && ...}。
  const [popoverOpen, dispatchPopover] = useReducer(searchPopoverReducer, INITIAL_POPOVER_STATE);

  // 整个搜索面板（form）的 ref：判断点击目标是否位于面板内部。
  const searchPanelRef = useRef<HTMLFormElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const suppressPopoverOnFocusRef = useRef(false);
  const previousKeywordPickRequestRef = useRef(keywordPickRequest);

  // 历史记录和推荐词只负责填词。父组件递增请求后，在关键词已经渲染进
  // 输入框的这一帧关闭浮层并交还焦点；程序化 focus 不应再次打开浮层。
  useEffect(() => {
    if (keywordPickRequest === previousKeywordPickRequestRef.current) return;
    previousKeywordPickRequestRef.current = keywordPickRequest;
    dispatchPopover({ type: "picked" });
    suppressPopoverOnFocusRef.current = true;
    searchInputRef.current?.focus();
    suppressPopoverOnFocusRef.current = false;
  }, [keywordPickRequest]);

  // 浮层打开时监听 document 的 pointerdown 与 Escape。
  // - pointerdown 且目标位于整个搜索 form 之外 → outside_pointer 关闭；
  // - 目标在 form 内部（输入框/清空按钮/浮层内按钮/平台选择/面板空白）→ 不关闭，
  //   因此内部按钮的 click 事件照常触发（不 preventDefault/stopPropagation）。
  // - 只在 popoverOpen === "open" 时注册；cleanup 移除同一个 listener，
  //   多次打开/关闭不会残留或重复注册。
  useEffect(() => {
    if (popoverOpen !== "open") return;
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && searchPanelRef.current && searchPanelRef.current.contains(target)) {
        return; // 点击面板内部：保持打开
      }
      dispatchPopover({ type: "outside_pointer" });
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        dispatchPopover({ type: "escape" });
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [popoverOpen]);

  const togglePlatform = useCallback(
    (p: PlatformSlug) => {
      const next = new Set(selectedPlatforms);
      if (next.has(p)) {
        next.delete(p); // 允许取消到零：提交时统一提示"先选平台"，不在勾选层拦
      } else {
        next.add(p);
      }
      onPlatformsChange(Array.from(next) as PlatformSlug[]);
      setPlatformPrompt(false); // 用户已开始勾选 → 清除"先选平台"提示
    },
    [selectedPlatforms, onPlatformsChange]
  );

  const handleSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      const trimmed = keyword.trim();
      if (!trimmed) return;
      // 零勾选：不禁用按钮，而是给出"先勾选至少一个平台"的提示，等待用户选择。
      if (selectedPlatforms.size === 0) {
        setPlatformPrompt(true);
        return;
      }
      dispatchPopover({ type: "search_started" }); // 开始搜索后关闭浮层
      onSearch(trimmed, Array.from(selectedPlatforms) as PlatformSlug[]);
    },
    [keyword, selectedPlatforms, onSearch]
  );

  // reset：只清空关键词与任务；平台选择与偏好保留（不恢复四平台全选）。
  const handleReset = useCallback(() => {
    onKeywordChange("");
    onReset();
  }, [onKeywordChange, onReset]);

  // 历史项点击：只填入关键词；历史平台仅用于展示，不覆盖当前勾选。
  const handleHistoryItemClick = useCallback(
    (item: SearchHistoryItem) => {
      onKeywordPick(item.keyword);
    },
    [onKeywordPick]
  );

  // 推荐词与历史项一致：只填入，不自动搜索。
  const handleRecommend = useCallback(
    (word: string) => {
      onKeywordPick(word);
    },
    [onKeywordPick]
  );

  return (
    <form
      ref={searchPanelRef}
      onSubmit={handleSubmit}
      className={`search-area search-panel relative rounded-[22px] border-0 bg-transparent p-0 shadow-none transition-[border-radius] ${
        popoverOpen === "open" ? "rounded-b-none" : ""
      }`}
    >
      {/* 搜索行：输入 + 按钮 */}
      <div className="search-box search-row flex items-stretch gap-2.5">
        <div className="search-leading" aria-hidden={!keyword || isSearching}>
          {keyword && !isSearching ? (
            <button
              type="button"
              onClick={() => onKeywordChange("")}
              aria-label={t("search.clearKeyword")}
            >
              <X />
            </button>
          ) : (
            <Search aria-hidden="true" />
          )}
        </div>
        <div className="search-input-wrap">
          <input
            ref={searchInputRef}
            type="text"
            value={keyword}
            onChange={(e) => onKeywordChange(e.target.value)}
            placeholder={home ? t("search.placeholderHome") : t("search.placeholder")}
            maxLength={200}
            disabled={isSearching}
            // 只有输入框聚焦打开浮层；关闭由 document pointerdown
            // 外部点击 / Escape / 提交搜索驱动，不再依赖 blur。
            onFocus={() => {
              if (!suppressPopoverOnFocusRef.current) {
                dispatchPopover({ type: "focus_within" });
              }
            }}
            // 不给 input 加圆角：胶囊形状由外层 .search-box 负责。input 自己带圆角时，
            // 光标停在最左边（圆角收缩区）会被裁成"上下窄中间宽"的一段弧线。
            className="w-full h-[58px] rounded-none border-0 bg-transparent text-[16px] text-cyber-text-primary placeholder:text-cyber-text-muted focus:outline-none disabled:opacity-50"
          />

          {/* 聚焦浮层：最近搜索 + 推荐搜索（必须 === "open"，"closed" 也是 truthy 字符串） */}
          {popoverOpen === "open" && (
            <SearchPopover
              history={history}
              disabled={isSearching}
              onItemClick={handleHistoryItemClick}
              onRemove={onHistoryRemove}
              onClear={onHistoryClear}
              onRecommend={handleRecommend}
            />
          )}
        </div>

        {isSearching ? (
          <button
            type="button"
            onClick={() => (onCancel ? onCancel() : handleReset())}
            disabled={isCancelling}
            className="search-cancel h-[44px] min-w-[104px] self-center flex items-center justify-center gap-2 rounded-full border border-warn/40 bg-warn-soft text-warn font-semibold text-[13px] hover:bg-warn-soft/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isCancelling ? <Loader2 className="w-4 h-4 animate-spin" /> : <X className="w-4 h-4" />}
            {t("search.cancel")}
          </button>
        ) : (
          <button
            type="submit"
            disabled={!keyword.trim()}
            className="search-submit h-[44px] w-[44px] self-center flex items-center justify-center rounded-full bg-brand text-white hover:bg-brand-strong hover:-translate-y-px transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:translate-y-0"
          >
            <ArrowRight className="w-[17px] h-[17px]" />
            <span className="sr-only">{t("search.submit")}</span>
          </button>
        )}
      </div>

      {/* 平台选择：浅色胶囊。每颗胶囊右边的 ⟳ 是"单独获取这个平台"，
          用于只补/只更新某一个平台的结果，不重跑其它平台（见 SearchPage.handleRetry）。 */}
      {/* data-tour 是教程高亮框的锚点，被 lib/onboarding.ts 的 GUIDE_STEPS 引用；
          教程改指向别的元素时，记得两边一起改。 */}
      <div className="scope search-scope" data-tour="search-scope">
        <span className="scope-label">{t("search.scope")}</span>
        {ALL_PLATFORMS.map((p) => {
          const isSelected = selectedPlatforms.has(p);
          const color = PLATFORM_COLORS[p];
          const isFetching = fetchingPlatform === p;
          return (
            <span key={p} className="platform-choice-group">
              <button
                type="button"
                disabled={isSearching}
                onClick={() => togglePlatform(p)}
                className="platform-choice"
                aria-pressed={isSelected}
              >
                <i
                  className="pd"
                  style={{ backgroundColor: color, opacity: isSelected ? 1 : 0.45 }}
                />
                {PLATFORM_LABELS[p]}
                <span className="check" aria-hidden="true">{isSelected ? "✓" : ""}</span>
              </button>
              {onPlatformFetch && (
                <button
                  type="button"
                  className="platform-fetch"
                  disabled={isSearching || isFetching}
                  onClick={() => onPlatformFetch(p)}
                  title={t("search.fetchPlatformTitle", { platform: PLATFORM_LABELS[p] })}
                  aria-label={t("search.fetchPlatformTitle", { platform: PLATFORM_LABELS[p] })}
                >
                  {isFetching ? <Loader2 className="spinner" /> : <RotateCcw />}
                </button>
              )}
            </span>
          );
        })}
        <span className="sr-only">{t("search.perPlatformHint", { count: Math.max(...Object.values(limits)) })}</span>
      </div>
      {platformPrompt && (
        <p className="mt-1 text-xs text-warn" role="status">
          {t("onboarding.emptyPlatformPrompt")}
        </p>
      )}
    </form>
  );
}
