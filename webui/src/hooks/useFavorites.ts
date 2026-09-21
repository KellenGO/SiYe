import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import type { FavoritesJobResponse, PlatformSlug, RemoteArchivePage } from "@/types/search";

function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 404;
}

export function useFavorites() {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const adopted = useRef(false);
  const [page, setPage] = useState(0);
  const [platform, setPlatform] = useState<PlatformSlug | "">("");
  // 默认只看"平台上确实存在"的条目。历史归档（账号未确认）与已归档的缺失项
  // 数量可能很大，全铺出来会把页面淹没 —— 它们只在需要时用筛选器查看。
  const [state, setState] = useState("present");
  const [account, setAccount] = useState("");

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
          "/api/search/favorites/jobs/latest?summary=true");
        return data;
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
    },
    retry: false,
  });

  const create = useMutation({
    mutationFn: async ({ platforms, mode }: { platforms: PlatformSlug[]; mode: "auto" | "full" }) => {
      const { data } = await axios.post<FavoritesJobResponse>("/api/search/favorites/jobs?summary=true", {
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
    queryFn: async () => (await axios.get<FavoritesJobResponse>(
      `/api/search/favorites/jobs/${jobId}?summary=true`)).data,
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
      `/api/search/favorites/jobs/${id}/cancel?summary=true`)).data,
    onSuccess: async (snapshot) => {
      await queryClient.cancelQueries({ queryKey: ["favorites-job", snapshot.job_id] });
      queryClient.setQueryData(["favorites-job", snapshot.job_id], snapshot);
      queryClient.setQueryData(["favorites-latest"], snapshot);
    },
  });

  const refresh = useCallback(async (platforms: PlatformSlug[], mode: "auto" | "full" = "auto") => {
    cancel.reset();
    create.reset();
    // 创建失败时保留上一批内容；错误由页面展示，避免未处理 Promise rejection。
    try {
      return await create.mutateAsync({ platforms, mode });
    } catch {
      return null;
    }
  }, [create, cancel]);

  const summary = poll.data ?? create.data ?? latest.data ?? null;
  const archive = useQuery({
    queryKey: ["favorites-archive", page, platform, state, account],
    queryFn: async () => (await axios.get<RemoteArchivePage>("/api/search/favorites/archive", {
      params: { offset: page * 50, limit: 50, platform: platform || undefined, state: state || undefined, account: account || undefined },
    })).data,
  });
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ["favorites-archive"] });
  }, [summary?.data_version, queryClient]);
  useEffect(() => {
    if (archive.data && page > 0 && page * 50 >= archive.data.total) setPage(Math.max(0, Math.ceil(archive.data.total / 50) - 1));
  }, [archive.data, page]);
  const data = summary ? { ...summary, results: archive.data?.items.map(item => item.result) ?? [] } : null;
  const purgeLegacy = useMutation({
    mutationFn: async () =>
      (await axios.post<{ removed: number }>("/api/search/favorites/archive/purge-legacy")).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["favorites-archive"] }),
        queryClient.invalidateQueries({ queryKey: ["favorites-latest"] }),
      ]);
    },
  });
  const resolve = useMutation({
    mutationFn: async (decisions: { id: number; missing_batch: string; action: "keep" | "remove" }[]) =>
      (await axios.post<{ applied: number; stale: number }>("/api/search/favorites/missing/resolve", { decisions })).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["favorites-archive"] }),
        queryClient.invalidateQueries({ queryKey: ["favorites-latest"] }),
        queryClient.invalidateQueries({ queryKey: ["favorites-job"] }),
      ]);
    },
  });

  return {
    archive: archive.data, loadingArchive: archive.isLoading,
    page, setPage, platform, state, account,
    setPlatform: (value: PlatformSlug | "") => { setPlatform(value); setPage(0); },
    setState: (value: string) => { setState(value); setPage(0); },
    setAccount: (value: string) => { setAccount(value); setPage(0); },
    resolve,
    purgeLegacy,
    sync: refresh,
    cancel: async () => {
      if (!data || cancel.isPending) return;
      try { await cancel.mutateAsync(data.job_id); } catch { /* Show the error and allow retry. */ }
    },
    canCancel: data?.overall === "running" && !create.isPending,
    cancelling: cancel.isPending,
    data,
    busy: create.isPending || cancel.isPending || (data?.overall === "running" && !poll.error),
    error: resolve.error || archive.error || cancel.error || create.error || (jobId ? poll.error : null) || latest.error,
  };
}
