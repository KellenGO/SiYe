import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import type { FavoritesJobResponse, PlatformSlug } from "@/types/search";

/**
 * 一键同步走 `auto`（增量：读到本机已有的内容就停，不用每次重拉全部）；
 * 「完整重扫」走 `full`（从头翻到尾，并重建收藏夹归属）。
 */
export type FavoritesSyncMode = "auto" | "full";

function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 404;
}

export function useFavorites() {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const adopted = useRef(false);

  // Re-entering the page restores the last snapshot from the backend. This is a
  // local, in-memory read: it never contacts a platform. Only an explicit sync
  // does. The query is deliberately left at the default staleTime so every
  // mount refetches — otherwise a "no result yet" answer cached on the first
  // visit would keep hiding a snapshot produced later in the session.
  const latest = useQuery({
    queryKey: ["favorites-latest"],
    queryFn: async () => {
      try {
        const { data } = await axios.get<FavoritesJobResponse>(
          "/api/search/favorites/jobs/latest");
        return data;
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
    },
    retry: false,
  });

  const create = useMutation({
    mutationFn: async ({ platforms, mode }: { platforms: PlatformSlug[]; mode: FavoritesSyncMode }) => {
      // limit_per_platform 只对还没接分页的平台生效；B站按 sync_mode 决定增量还是完整重扫。
      const { data } = await axios.post<FavoritesJobResponse>("/api/search/favorites/jobs", {
        platforms, limit_per_platform: 100, sync_mode: mode,
      });
      return data;
    },
    onSuccess: (data) => {
      adopted.current = true;
      queryClient.setQueryData(["favorites-job", data.job_id], data);
      setJobId(data.job_id);
    },
  });

  const poll = useQuery({
    queryKey: ["favorites-job", jobId],
    queryFn: async () => {
      // Running jobs can have a large local archive. Poll only status/summary;
      // fetch the full snapshot once when the task reaches a terminal state.
      const { data: summary } = await axios.get<FavoritesJobResponse>(
        `/api/search/favorites/jobs/${jobId}`, { params: { summary: true } });
      if (summary.overall === "running") return summary;
      return (await axios.get<FavoritesJobResponse>(`/api/search/favorites/jobs/${jobId}`)).data;
    },
    enabled: !!jobId,
    refetchInterval: (query) => query.state.data?.overall === "running" ? 1500 : false,
    retry: 1,
  });

  // Leaving the page while a sync is still running and coming back should keep
  // following that job rather than freezing on the restored "running" status.
  // The job already exists in the backend, so this starts no new platform work.
  useEffect(() => {
    if (adopted.current) return;
    const restored = latest.data;
    if (!restored) return;
    adopted.current = true;
    if (restored.overall === "running") setJobId(restored.job_id);
  }, [latest.data]);

  const cancel = useMutation({
    mutationFn: async (id: string) => (await axios.post<FavoritesJobResponse>(
      `/api/search/favorites/jobs/${id}/cancel`)).data,
    onSuccess: async (snapshot) => {
      await queryClient.cancelQueries({ queryKey: ["favorites-job", snapshot.job_id] });
      queryClient.setQueryData(["favorites-job", snapshot.job_id], snapshot);
      queryClient.setQueryData(["favorites-latest"], snapshot);
    },
  });

  const refresh = useCallback(async (platforms: PlatformSlug[], mode: FavoritesSyncMode = "auto") => {
    cancel.reset();
    create.reset();
    // 创建失败时保留上一批内容；错误由页面展示，避免未处理 Promise rejection。
    try {
      return await create.mutateAsync({ platforms, mode });
    } catch {
      return null;
    }
  }, [create, cancel]);

  const data = poll.data ?? create.data ?? latest.data ?? null;

  return {
    sync: refresh,
    cancel: async () => {
      if (!data || cancel.isPending) return;
      try { await cancel.mutateAsync(data.job_id); } catch { /* Show the error and allow retry. */ }
    },
    canCancel: data?.overall === "running" && !create.isPending,
    cancelling: cancel.isPending,
    data,
    busy: create.isPending || cancel.isPending || (data?.overall === "running" && !poll.error),
    error: cancel.error || create.error || (jobId ? poll.error : null) || latest.error,
  };
}
