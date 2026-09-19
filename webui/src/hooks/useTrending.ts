/**
 * 热搜词 hook（后端 /api/trending）。
 *
 * **获取时机只有两个**（用户明确要求）：刚进软件时一次（四个平台一起取回来）、
 * 以及用户手动刷新（`refresh()` 跳过后端缓存读取）。
 * 所以这里把所有自动重新获取都关掉，不轮询、不在切页面/切平台时重取 ——
 * 后端那边也是取一次就缓存住，不再自动更新。
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
    // 进软件取一次就够：不给失效窗口，也不在重新挂载 / 窗口聚焦 / 断线重连时再取。
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
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
