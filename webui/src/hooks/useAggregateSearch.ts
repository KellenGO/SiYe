import { useCallback, useState, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import type {
  SearchJobRequest,
  SearchJobResponse,
  PlatformSlug,
} from "@/types/search";
import { waitForAccountOpsIdle } from "@/lib/accountGate";
import { isAnyScanLoginActive } from "@/lib/scanLogin";

const API_BASE = "/api/search";
const STORAGE_KEY = "aggregate_search_job_id";

async function createJob(req: SearchJobRequest): Promise<SearchJobResponse> {
  const { data } = await axios.post<SearchJobResponse>(`${API_BASE}/jobs`, req);
  return data;
}

async function getJob(
  jobId: string,
  signal?: AbortSignal
): Promise<SearchJobResponse> {
  const { data } = await axios.get<SearchJobResponse>(
    `${API_BASE}/jobs/${jobId}`,
    { signal }
  );
  return data;
}

async function getCurrentJob(
  signal?: AbortSignal
): Promise<SearchJobResponse | null> {
  try {
    const { data } = await axios.get<SearchJobResponse>(
      `${API_BASE}/jobs/current`,
      { signal }
    );
    return data;
  } catch {
    return null;
  }
}

async function cancelJob(jobId: string): Promise<void> {
  await axios.post(`${API_BASE}/jobs/${jobId}/cancel`);
}

// ── Pure, testable decision helpers ─────────────────────────────────────
//
// The mount-time recovery race: a /jobs/current response that arrives AFTER
// the user started a new search (or reset) must not overwrite the newer
// state. Both helpers are pure so they can be unit-tested without a
// frontend test framework.

/** A late recovery response applies only if no startSearch/reset happened
 *  after the request was issued (i.e. the generation is unchanged). */
export function shouldApplyRecoveredJob(
  recoveredJobId: string | null | undefined,
  generationAtRequest: number,
  currentGeneration: number
): boolean {
  if (generationAtRequest !== currentGeneration) return false;
  return typeof recoveredJobId === "string" && recoveredJobId.length > 0;
}

/** A 404 from GET /jobs/{id} clears the tracked job only when the failed
 *  id is the one currently tracked — a stale 404 must not reset a newer job. */
export function shouldClearJobOn404(
  failedJobId: string,
  currentJobId: string | null
): boolean {
  return currentJobId === failedJobId;
}

export function useAggregateSearch() {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(() => {
    return sessionStorage.getItem(STORAGE_KEY) || null;
  });
  const [searchedPlatforms, setSearchedPlatforms] = useState<PlatformSlug[] | null>(null);

  // Generation counter: bumped by startSearch/reset so a late /jobs/current
  // response can never overwrite newer state.
  const generationRef = useRef(0);
  const jobIdRef = useRef(jobId);
  useEffect(() => {
    jobIdRef.current = jobId;
  }, [jobId]);

  // Recover current job from backend on mount (with race protection)
  useEffect(() => {
    const ac = new AbortController();
    const genAtRequest = generationRef.current;
    getCurrentJob(ac.signal).then((resp) => {
      if (ac.signal.aborted) return;
      if (!shouldApplyRecoveredJob(resp?.job_id, genAtRequest, generationRef.current)) {
        return;
      }
      if (resp && resp.job_id) {
        setJobId(resp.job_id);
        sessionStorage.setItem(STORAGE_KEY, resp.job_id);
        const platforms = Object.keys(resp.platforms) as PlatformSlug[];
        setSearchedPlatforms(platforms.length > 0 ? platforms : null);
      }
    }).catch(() => {});
    return () => { ac.abort(); };
  }, []);

  const createMutation = useMutation({
    mutationFn: async (req: SearchJobRequest): Promise<SearchJobResponse> => {
      // Round 18: 账号同步/验证与搜索互斥（同一个 persistent profile）。
      // 打开程序时可能正在自动同步登录状态 —— 这里等它结束再提交，而不是
      // 让用户撞上 409"账号操作进行中"。等待期间按钮的 pending 状态即是反馈。
      // 超时后照常提交，后端 409 仍是兜底。
      // 方案 A：应用自带扫码登录同样占用排他租约（但不写账号状态），
      // 通过 extraBusy 一起等待 —— 否则用户刚点完扫码登录再搜索就会 409。
      await waitForAccountOpsIdle({
        fetchAccounts: async () => {
          const { data } = await axios.get("/api/search/accounts");
          return data.accounts as readonly { platform: string; status: string }[];
        },
        extraBusy: isAnyScanLoginActive,
      });
      return createJob(req);
    },
    onSuccess: (data) => {
      setJobId(data.job_id);
      sessionStorage.setItem(STORAGE_KEY, data.job_id);
      const platforms = Object.keys(data.platforms) as PlatformSlug[];
      setSearchedPlatforms(platforms);
    },
    onError: () => {
      setJobId(null);
      sessionStorage.removeItem(STORAGE_KEY);
    },
  });

  const cancelMutation = useMutation({
    mutationFn: async (): Promise<void> => {
      const id = jobIdRef.current;
      if (!id) return;
      await cancelJob(id);
    },
  });

  const pollQuery = useQuery<SearchJobResponse>({
    queryKey: ["search-job", jobId],
    queryFn: async ({ queryKey, signal }) => {
      const requestedId = queryKey[1] as string;
      try {
        return await getJob(requestedId, signal);
      } catch (err) {
        const status = (err as { response?: { status?: number } })?.response?.status;
        // 404 only clears the job that was requested — never a newer one.
        if (status === 404 && shouldClearJobOn404(requestedId, jobIdRef.current)) {
          setJobId(null);
          sessionStorage.removeItem(STORAGE_KEY);
        }
        throw err;
      }
    },
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 800;
      const terminal = ["completed", "partial", "failed", "cancelled"];
      if (terminal.includes(data.overall) && data.completed_at && data.hydration_status !== "running") return false;
      return 800;
    },
    staleTime: 500,
    retry: (count, err) => {
      const status = (err as { response?: { status?: number } })?.response?.status;
      // 404 handled in queryFn (clears only the matching job)
      if (status === 404) return false;
      return count < 1;
    },
  });

  // 返回 POST 的结果 Promise：调用方 await 成功后才写入历史（Round 12.1）。
  // Round 15: 支持按平台独立数量 platform_limits（可选）；缺失时后端回退
  // limit_per_platform（默认 20），旧调用方不受影响。
  const startSearch = useCallback(
    (
      keyword: string,
      platforms: PlatformSlug[],
      limitPerPlatform?: number,
      platformLimits?: Partial<Record<PlatformSlug, number>>,
      bypassCache = false,
      continueFrom?: string,
      replacePlatforms = false
    ): Promise<SearchJobResponse> => {
      generationRef.current += 1; // invalidate in-flight recovery responses
      setJobId(null);
      sessionStorage.removeItem(STORAGE_KEY);
      createMutation.reset();
      cancelMutation.reset();
      queryClient.removeQueries({ queryKey: ["search-job"] });

      const req: SearchJobRequest = {
        keyword,
        platforms,
        limit_per_platform: limitPerPlatform ?? 20,
        ...(platformLimits && Object.keys(platformLimits).length > 0
          ? { platform_limits: platformLimits }
          : {}),
        bypass_cache: bypassCache,
        ...(continueFrom ? { continue_from: continueFrom } : {}),
        // 单平台重搜：结果替换当前批次里该平台的内容，不新增批次。
        ...(replacePlatforms ? { replace_platforms: true } : {}),
      };
      return createMutation.mutateAsync(req);
    },
    [createMutation, cancelMutation, queryClient]
  );

  const cancel = useCallback(async (): Promise<void> => {
    // Round 13: mutateAsync —— 调用方可感知取消请求失败（不裸 500 文案）。
    await cancelMutation.mutateAsync();
  }, [cancelMutation]);

  const resetCancel = useCallback(() => {
    // 清除取消请求错误/挂起状态（新搜索/reset 也会自动调用）。
    cancelMutation.reset();
  }, [cancelMutation]);

  const reset = useCallback(() => {
    generationRef.current += 1; // invalidate in-flight recovery responses
    setJobId(null);
    sessionStorage.removeItem(STORAGE_KEY);
    setSearchedPlatforms(null);
    createMutation.reset();
    cancelMutation.reset();
    queryClient.removeQueries({ queryKey: ["search-job"] });
  }, [createMutation, cancelMutation, queryClient]);

  // 有效取消态：取消 POST 挂起，或后端 job 自身处于 cancelling
  // （Round 13 —— 后端清理期间 UI 必须保持锁定并显示"正在取消"）。
  const effectiveCancelling =
    cancelMutation.isPending || pollQuery.data?.overall === "cancelling";

  const overall = !jobId
    ? "idle"
    : effectiveCancelling
      ? "cancelling"
      : (pollQuery.data?.overall ?? "running");

  return {
    startSearch,
    cancel,
    reset,
    resetCancel,
    isCreating: createMutation.isPending,
    isCancelling: effectiveCancelling,
    createError: createMutation.error,
    // Round 13: 取消请求失败错误（与 createError/pollError 分离，不混用）。
    cancelError: cancelMutation.error,
    jobResponse: pollQuery.data,
    isPolling: pollQuery.isFetching,
    overall,
    pollError: pollQuery.error,
    searchedPlatforms,
    jobId,
  };
}
