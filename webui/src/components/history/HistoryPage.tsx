import { useState } from "react";
import { History as HistoryIcon, Loader2, Trash2 } from "lucide-react";
import { ResultTabs } from "@/components/search/ResultTabs";
import { useHistory } from "@/hooks/useHistory";
import { PLATFORM_SLUGS } from "@/lib/platformMeta";
import { resultKey } from "@/lib/resultTools";

const PLATFORMS = PLATFORM_SLUGS;

export function HistoryPage() {
  const history = useHistory();
  const [busy, setBusy] = useState(false);

  const results = history.views.map((view) => view.result);

  const handleClear = async () => {
    if (!history.views.length) return;
    if (!window.confirm(`确定清空全部 ${history.views.length} 条观看历史吗？此操作不可撤销。`)) return;
    setBusy(true);
    try {
      await history.clearViews();
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (key: string) => {
    setBusy(true);
    try {
      await history.deleteView(key);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="preview-container history-page">
      <div className="collection-heading">
        <div className="page-heading">
          <div>
            <p className="eyebrow">WATCH HISTORY</p>
            <h1>看过的，都在这儿</h1>
            <p className="description">点开过的内容会自动记下来，方便你回头再找。只保留最近 1000 条。</p>
          </div>
        </div>
        <p className="collection-count">
          {history.loading && !history.views.length
            ? "正在读取本机历史…"
            : `共 ${history.views.length} 条 · 按最近浏览倒序`}
        </p>
      </div>

      {history.error && (
        <div className="status-line" role="alert"><HistoryIcon />{history.error}</div>
      )}

      {history.views.length > 0 && (
        <div className="library-batch" role="status" style={{ marginBottom: "12px" }}>
          <span>已记录 {history.views.length} 条观看</span>
          <button type="button" className="btn danger" disabled={busy} onClick={() => void handleClear()}>
            <Trash2 />清空历史
          </button>
        </div>
      )}

      {history.loading && !history.views.length ? (
        <div className="empty" role="status">
          <div className="empty-symbol"><Loader2 className="spinner" /></div>
          <h2>正在读取本机历史</h2>
          <p>观看历史保存在本机，清理浏览器缓存也不会丢。</p>
        </div>
      ) : results.length ? (
        <ResultTabs
          results={results}
          overall="completed"
          platforms={PLATFORMS}
          disableSort
          pageSize={100}
          onDeleteItem={(result) => void handleDelete(resultKey(result))}
        />
      ) : (
        <div className="empty">
          <div className="empty-symbol"><HistoryIcon /></div>
          <h2>还没有观看历史</h2>
          <p>在搜索结果里点开任意一条内容，它会自动出现在「历史」里。</p>
        </div>
      )}
    </div>
  );
}
