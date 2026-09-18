/**
 * 观看历史 hook（后端 /api/history）。
 *
 * 历史是自动记录的：用户点开结果链接即记一条（见 ResultCard）。
 * 这里只负责读取与「删除单条 / 清空」两类本地管理操作，数据只在本机。
 */

import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { clearViews as apiClearViews, deleteView as apiDeleteView, fetchViews, type HistoryView } from "@/lib/historyApi";

interface HistoryState {
  views: HistoryView[];
  loading: boolean;
  error: string | null;
}

const HISTORY_QUERY_KEY = ["watch-history"];
const EMPTY: HistoryState = { views: [], loading: true, error: null };

async function loadHistory(): Promise<HistoryState> {
  const views = await fetchViews();
  return { views, loading: false, error: null };
}

export function useHistory() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: HISTORY_QUERY_KEY, queryFn: loadHistory, retry: false });
  const state = query.data ?? EMPTY;

  const refresh = useCallback(async () => {
    await client.invalidateQueries({ queryKey: HISTORY_QUERY_KEY });
  }, [client]);

  const deleteView = useCallback(async (key: string) => {
    await apiDeleteView(key);
    await refresh();
  }, [refresh]);

  const clearViews = useCallback(async () => {
    await apiClearViews();
    await refresh();
  }, [refresh]);

  return {
    views: state.views,
    loading: query.isPending,
    error: query.error ? "观看历史暂时不可用，请重试" : null,
    refresh,
    deleteView,
    clearViews,
  };
}
