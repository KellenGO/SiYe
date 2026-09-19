/**
 * 热搜词 hook（后端 /api/trending）。
 *
 * 只读：拉一次各平台热搜词，另外提供手动刷新（用户主动刷新时跳过后端缓存读取）。
 * 后端自己有 5 分钟缓存，这里不再重复轮询 —— 热搜榜是分钟级变化，没必要盯着拉。
 */

import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchTrending, type TrendingSnapshot } from "@/lib/trendingApi";

const TRENDING_QUERY_KEY = ["trending"];

export function useTrending() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: TRENDING_QUERY_KEY,
    queryFn: () => fetchTrending(false),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const refresh = useCallback(async () => {
    const fresh = await fetchTrending(true);
    client.setQueryData(TRENDING_QUERY_KEY, fresh);
  }, [client]);

  return {
    snapshot: query.data as TrendingSnapshot | undefined,
    loading: query.isPending,
    error: query.error ? "热搜暂时取不到，稍后再试" : null,
    refresh,
  };
}
