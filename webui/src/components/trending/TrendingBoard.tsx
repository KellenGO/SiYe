/**
 * 热搜卡片：顶部按平台切换，一次只展示一个平台的热搜词，点一条就去搜它。
 *
 * 为什么用标签切换而不是四个平台并排：四列在那个位置挤不下（每列只放得下一行短词），
 * 切换才留得住每个平台展示多少条。平台之间仍然各自独立 ——
 * 数据层就是分平台返回的，标签只是同一份数据的不同视图。
 *
 * 获取时机只有两个（用户要求）：进软件时一次（四个平台一起取），之后只有用户点「刷新」。
 */

import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useTrending } from "@/hooks/useTrending";
import { formatHeat, initialTab, topWords, trendingTabs } from "@/lib/trendingApi";
import type { PlatformSlug } from "@/types/search";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "@/types/search";

interface TrendingBoardProps {
  /** 点一条热搜：由调用方决定是只填词还是直接搜。 */
  onPick: (word: string) => void;
}

function formatUpdatedAt(activeTime: string | null, fetchedAt: string | null): string | null {
  if (activeTime) return activeTime;
  if (!fetchedAt) return null;
  try {
    return new Date(fetchedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return null;
  }
}

export function TrendingBoard({ onPick }: TrendingBoardProps) {
  const { t } = useTranslation();
  const { snapshot, loading, error, refresh } = useTrending();
  const tabs = useMemo(() => trendingTabs(snapshot), [snapshot]);
  const [active, setActive] = useState<PlatformSlug | null>(null);

  // 首次拿到数据后选中第一个有词的平台；之后切换完全由用户控制，不再自动跳。
  useEffect(() => {
    if (!snapshot) return;
    setActive((current) => current ?? initialTab(snapshot));
  }, [snapshot]);

  const activeTab = tabs.find((tab) => tab.platform === active) ?? tabs[0];
  const info = activeTab ? snapshot?.platforms[activeTab.platform] : undefined;
  const words = topWords(info?.words ?? []);
  const activeTime = snapshot
    ? Object.values(snapshot.platforms).find((item) => item?.activeTime)?.activeTime ?? null
    : null;
  const updatedAt = formatUpdatedAt(activeTime, snapshot?.fetchedAt ?? null);

  return (
    <section className="panel trending-panel" aria-label={t("trending.title")}>
      <div className="panel-heading">
        <h2>{t("trending.title")}</h2>
        <button type="button" className="text-link" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw className={loading ? "spinner" : undefined} />
          {t("trending.refresh")}
        </button>
      </div>

      <div className="trending-tabs" role="tablist" aria-label={t("trending.title")}>
        {tabs.map((tab) => (
          <button
            key={tab.platform}
            type="button"
            role="tab"
            aria-selected={activeTab?.platform === tab.platform}
            className={`trending-tab${activeTab?.platform === tab.platform ? " active" : ""}${
              tab.hasWords ? "" : " muted"
            }`}
            onClick={() => setActive(tab.platform)}
          >
            <i className="pd" style={{ backgroundColor: PLATFORM_COLORS[tab.platform] }} aria-hidden="true" />
            {PLATFORM_LABELS[tab.platform]}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="trending-panel-body">
        {loading && !snapshot && <p className="secondary text-[13px]">{t("trending.loading")}</p>}
        {!loading && error && !snapshot && <p className="secondary text-[13px]">{t("trending.failed")}</p>}
        {snapshot && words.length === 0 && (
          <p className="secondary text-[13px]">
            {info?.message || (info?.status === "failed" ? t("trending.failed") : t("trending.empty"))}
          </p>
        )}
        {words.length > 0 && (
          <ol className="trending-list">
            {words.map((item) => (
              <li key={`${activeTab?.platform}-${item.rank}-${item.word}`}>
                <button type="button" onClick={() => onPick(item.word)} title={item.word}>
                  <span className={`trending-rank${item.rank <= 3 ? " hot" : ""}`}>{item.rank}</span>
                  <span className="trending-word">{item.word}</span>
                  {formatHeat(item.hotValue) && <span className="trending-heat">{formatHeat(item.hotValue)}</span>}
                </button>
              </li>
            ))}
          </ol>
        )}
      </div>

      <p className="panel-footnote">
        {updatedAt ? t("trending.updatedAt", { time: updatedAt }) : t("trending.subtitle")}
        {words.length > 0 && `　·　${t("trending.pickHint")}`}
        {`　·　${t("trending.manualOnly")}`}
      </p>
    </section>
  );
}
