/**
 * 热搜词 hook（后端 /api/trending）。
 *
 * **获取时机只有两个**（用户明确要求）：刚进软件时一次（三个平台一起取回来）、
 * 以及用户手动刷新（`refresh()` 跳过后端缓存读取）。
 * 所以这里把所有自动重新获取都关掉，不轮询、不在切页面/切平台时重取 ——
 * 后端那边也是取一次就缓存住，不再自动更新。
 */

import { useCallback, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchTrending, type TrendingSnapshot } from "@/lib/trendingApi";

const TRENDING_QUERY_KEY = ["trending"];

export function useTrending() {
  const client = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  // 连点「刷新」不再叠请求：一轮没回来之前直接忽略后续点击。
  const inFlight = useRef(false);
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
    if (inFlight.current) return;
    inFlight.current = true;
    setRefreshing(true);
    setRefreshFailed(false);
    try {
      const fresh = await fetchTrending(true);
      client.setQueryData(TRENDING_QUERY_KEY, fresh);
    } catch {
      // 失败要能被界面看见（TrendingBoard 用 error 是否为空决定显示提示）。
      setRefreshFailed(true);
    } finally {
      inFlight.current = false;
      setRefreshing(false);
    }
  }, [client]);

  return {
    snapshot: query.data as TrendingSnapshot | undefined,
    loading: query.isPending || refreshing,
    error: query.error || refreshFailed ? "热搜暂时取不到，稍后再试" : null,
    refresh,
  };
}
