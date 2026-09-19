/**
 * 热搜卡片：按平台并列展示各平台热搜词，点一条就去搜它。
 *
 * 为什么按平台分开摆：这个卡片的价值是"同一条热搜，各平台反应不同"，
 * 把四个平台混成一锅就看不出差异了。数据层也是分平台返回的
 * （聚合不可逆，分平台数据随时能合，反过来不行）。
 *
 * 只展示拿到词的平台；没接入或这次取不到的平台不占位、不报错。
 */

import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useTrending } from "@/hooks/useTrending";
import { formatHeat, topWords, visiblePlatforms } from "@/lib/trendingApi";
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
  const platforms = visiblePlatforms(snapshot);
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

      {platforms.length === 0 && (
        <p className="secondary text-[13px]">
          {loading ? t("trending.loading") : error ? t("trending.failed") : t("trending.empty")}
        </p>
      )}

      {platforms.length > 0 && (
        <div className="trending-columns">
          {platforms.map((platform) => {
            const info = snapshot?.platforms[platform];
            if (!info) return null;
            return (
              <div className="trending-column" key={platform}>
                <div className="trending-platform">
                  <i className="pd" style={{ backgroundColor: PLATFORM_COLORS[platform] }} aria-hidden="true" />
                  {PLATFORM_LABELS[platform]}
                </div>
                <ol className="trending-list">
                  {topWords(info.words).map((item) => (
                    <li key={`${platform}-${item.rank}-${item.word}`}>
                      <button type="button" onClick={() => onPick(item.word)} title={item.word}>
                        <span className={`trending-rank${item.rank <= 3 ? " hot" : ""}`}>{item.rank}</span>
                        <span className="trending-word">{item.word}</span>
                        {formatHeat(item.hotValue) && <span className="trending-heat">{formatHeat(item.hotValue)}</span>}
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            );
          })}
        </div>
      )}

      <p className="panel-footnote">
        {updatedAt ? t("trending.updatedAt", { time: updatedAt }) : t("trending.subtitle")}
        {platforms.length > 0 && `　·　${t("trending.pickHint")}`}
      </p>
    </section>
  );
}
