/**
 * Phase 2 渐进结果展示测试 —— 直接 import 生产模块
 * （webui/src/lib/searchExperience.ts 的 applySearchTransition /
 * selectSearchPresentation），不复制任何生产判断逻辑。
 *
 * 覆盖：旧快照→无新结果、首条结果立即切换、结果逐轮增长、旧 job 迟到、
 * reset 后迟到、retry 渐进替换、cancel 有/无部分结果、POST 未接受拒绝。
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  applySearchTransition,
  createInitialExperienceState,
  selectSearchPresentation,
  type ExperienceEvent,
  type ExperienceState,
  type SearchHistoryItem,
} from "../src/lib/searchExperience.js";
import type {
  PlatformSlug,
  SearchJobResponse,
  UnifiedSearchResult,
} from "../src/types/search.js";

// ── Test helpers（仅构造数据，不含生产逻辑） ────────────────────────────

const ALL: PlatformSlug[] = ["xhs", "douyin", "bilibili", "zhihu"];

function makeResult(platform: PlatformSlug, contentId: string): UnifiedSearchResult {
  return {
    platform,
    content_id: contentId,
    content_type: "note",
    title: `title-${contentId}`,
    author: null,
    url: `https://${platform}.com/${contentId}`,
    published_at: null,
    cover_url: null,
    metrics: {},
    rank: 0,
  };
}

function makeJob(
  jobId: string,
  overall: SearchJobResponse["overall"],
  keyword: string,
  platforms: Record<PlatformSlug, { status: string; count: number }>,
  results: UnifiedSearchResult[],
  overrides: Partial<SearchJobResponse> = {}
): SearchJobResponse {
  return {
    job_id: jobId,
    overall,
    keyword,
    created_at: "2026-08-01T00:00:00.000Z",
    completed_at: "2026-08-01T00:01:00.000Z",
    platforms: Object.fromEntries(
      Object.entries(platforms).map(([p, info]) => [
        p,
        { status: info.status, result_count: info.count, error_summary: null },
      ])
    ) as SearchJobResponse["platforms"],
    results,
    ...overrides,
  };
}

const STATUS_RUNNING: Record<PlatformSlug, { status: string; count: number }> = {
  xhs: { status: "running", count: 0 },
  douyin: { status: "running", count: 0 },
  bilibili: { status: "running", count: 0 },
  zhihu: { status: "running", count: 0 },
};

function initState(history: SearchHistoryItem[] = []): ExperienceState {
  return createInitialExperienceState(history, [...ALL]);
}

function step(state: ExperienceState, ...events: ExperienceEvent[]): ExperienceState {
  return events.reduce((s, e) => applySearchTransition(s, e), state);
}

function startFull(state: ExperienceState, keyword: string, jobId: string): ExperienceState {
  return step(
    state,
    { type: "search_start", keyword, platforms: ALL },
    { type: "search_accepted", jobId, keyword, platforms: ALL, nowIso: "2026-08-03T00:00:00.000Z" }
  );
}

// ── 1. 旧快照 → 无新结果：继续显示上次结果 + 提示 ──────────────────────

test("渐进: 无新结果时保留旧快照并提示", () => {
  // 先完成 job-B 作为旧快照
  let state = startFull(initState(), "词", "job-B");
  state = step(state, {
    type: "job_terminal",
    job: makeJob("job-B", "completed", "词", {
      xhs: { status: "succeeded", count: 1 },
      douyin: { status: "succeeded", count: 1 },
      bilibili: { status: "empty", count: 0 },
      zhihu: { status: "empty", count: 0 },
    }, [makeResult("xhs", "old-1"), makeResult("douyin", "old-2")]),
  });
  // 新任务 job-C 运行中，尚无任何结果
  state = startFull(state, "新词", "job-C");
  state = step(state, {
    type: "job_progress",
    job: makeJob("job-C", "running", "新词", STATUS_RUNNING, []),
  });

  const p = selectSearchPresentation(state);
  assert.equal(p.showingStaleSnapshot, true);
  assert.equal(p.liveHint, "正在搜索，暂时显示上次结果");
  // 结果仍是旧快照；状态卡片来自 live（当前任务）
  assert.deepEqual(
    p.jobResponse!.results.map((r) => r.content_id),
    ["old-1", "old-2"]
  );
  assert.equal(p.jobResponse!.platforms.xhs.status, "running");
});

// ── 2. 第一条新结果立即切换为实时结果 ───────────────────────────────────

test("渐进: 首条新结果到达后立即切换为实时结果", () => {
  let state = startFull(initState(), "新词", "job-B");
  state = step(state, {
    type: "job_progress",
    job: makeJob("job-B", "running", "新词", {
      xhs: { status: "succeeded", count: 1 },
      douyin: { status: "running", count: 0 },
      bilibili: { status: "running", count: 0 },
      zhihu: { status: "running", count: 0 },
    }, [makeResult("xhs", "new-1")]),
  });

  const p = selectSearchPresentation(state);
  assert.equal(p.showingStaleSnapshot, false);
  assert.equal(p.liveHint, "已返回 1 条，仍在搜索 3 个平台");
  assert.deepEqual(p.jobResponse!.results.map((r) => r.content_id), ["new-1"]);
});

// ── 3. 结果逐轮增长 ─────────────────────────────────────────────────────

test("渐进: 结果随轮询逐轮增长", () => {
  let state = startFull(initState(), "词", "job-B");
  const progress = (results: UnifiedSearchResult[]) =>
    step(state, {
      type: "job_progress",
      job: makeJob("job-B", "running", "词", STATUS_RUNNING, results),
    });

  state = progress([makeResult("xhs", "n1")]);
  assert.equal(selectSearchPresentation(state).jobResponse!.results.length, 1);
  state = progress([makeResult("xhs", "n1"), makeResult("douyin", "n2")]);
  assert.equal(selectSearchPresentation(state).jobResponse!.results.length, 2);
  state = progress([makeResult("xhs", "n1"), makeResult("douyin", "n2"), makeResult("bilibili", "n3")]);
  assert.equal(selectSearchPresentation(state).jobResponse!.results.length, 3);
});

// ── 4. 旧 job 迟到响应被拒绝 ────────────────────────────────────────────

test("渐进: 旧 job 的迟到进度被拒绝", () => {
  let state = startFull(initState(), "词", "job-B");
  state = step(state, {
    type: "job_progress",
    job: makeJob("job-B", "running", "词", STATUS_RUNNING, [makeResult("xhs", "b1")]),
  });
  const before = state.display.liveResponse;

  const after = step(state, {
    type: "job_progress",
    job: makeJob("job-A", "running", "旧词", STATUS_RUNNING, [makeResult("xhs", "a1")]),
  });
  assert.equal(after.display.liveResponse, before); // 拒绝：原引用返回
});

// ── 5. POST 尚未 accepted / reset 后迟到被拒绝 ─────────────────────────

test("渐进: POST 未被接受时拒绝进度", () => {
  const state = step(initState(), { type: "search_start", keyword: "词", platforms: ALL });
  const after = step(state, {
    type: "job_progress",
    job: makeJob("job-B", "running", "词", STATUS_RUNNING, [makeResult("xhs", "x")]),
  });
  assert.equal(after.display.liveResponse, null);
});

test("渐进: reset 后迟到进度被拒绝", () => {
  let state = startFull(initState(), "词", "job-B");
  state = step(state, {
    type: "job_progress",
    job: makeJob("job-B", "running", "词", STATUS_RUNNING, [makeResult("xhs", "b1")]),
  });
  state = step(state, { type: "reset" });
  const after = step(state, {
    type: "job_progress",
    job: makeJob("job-B", "running", "词", STATUS_RUNNING, [makeResult("xhs", "late")]),
  });
  assert.equal(after.display.liveResponse, null);
  assert.equal(after.display.activeJobId, null);
});

// ── 6. retry 只渐进替换目标平台 ─────────────────────────────────────────

test("渐进: 单平台首条立即显示且保留其他平台", () => {
  const committed = makeJob("job-A", "partial", "词", {
    xhs: { status: "failed", count: 0 },
    douyin: { status: "succeeded", count: 1 },
    bilibili: { status: "empty", count: 0 },
    zhihu: { status: "empty", count: 0 },
  }, [makeResult("douyin", "d1")]);

  let state = startFull(initState(), "词", "job-A");
  state = step(state, { type: "job_terminal", job: committed });
  state = step(state, { type: "retry_start", platform: "xhs" }, { type: "retry_accepted", jobId: "job-R" });

  // 正式快照保持不动，展示层先合并新条目。
  const after = step(state, {
    type: "job_progress",
    job: makeJob("job-R", "running", "词", STATUS_RUNNING, [makeResult("xhs", "r1")]),
  });
  assert.equal(after.display.liveResponse?.job_id, "job-R");
  assert.equal(after.display.jobResponse, committed);
  const p = selectSearchPresentation(after);
  assert.deepEqual(p.jobResponse!.results.map((r) => r.content_id), ["r1", "d1"]);
  assert.equal(p.jobResponse!.overall, "running");
  const failed = step(after, { type: "job_terminal", job: makeJob(
    "job-R", "failed", "词", { ...STATUS_RUNNING, xhs: { status: "failed", count: 1 } },
    [makeResult("xhs", "r1")],
  ) });
  assert.deepEqual(failed.display.jobResponse!.results.map((r) => r.content_id), ["r1", "d1"]);
});

test("渐进: 平台已经终态但清理未完时仍显示首条", () => {
  const state = startFull(initState(), "词", "job-A");
  const after = step(state, { type: "job_progress", job: makeJob(
    "job-A", "completed", "词", STATUS_RUNNING, [makeResult("xhs", "first")],
    { completed_at: null },
  ) });
  assert.equal(selectSearchPresentation(after).jobResponse!.results[0].content_id, "first");
  assert.equal(after.display.appliedJobIds.size, 0);
});

// ── 7/8. 取消语义 ───────────────────────────────────────────────────────

test("渐进: 取消时已有部分结果 → 保留这些部分结果", () => {
  // 先完成一个任务（job-B completed）作为旧快照
  let state = startFull(initState(), "词", "job-B");
  state = step(state, {
    type: "job_terminal",
    job: makeJob("job-B", "completed", "词", STATUS_RUNNING, [makeResult("xhs", "old-1")]),
  });
  // 新任务 job-C 运行中，已有部分结果
  state = startFull(state, "新词", "job-C");
  state = step(state, {
    type: "job_progress",
    job: makeJob("job-C", "running", "新词", STATUS_RUNNING, [makeResult("xhs", "new-1")]),
  });
  // 真实 cancelled 终态带回部分结果
  const cancelled = makeJob("job-C", "cancelled", "新词", STATUS_RUNNING, [makeResult("xhs", "new-1")]);
  state = step(state, { type: "job_terminal", job: cancelled });

  assert.equal(state.display.cancelledNotice, true);
  assert.deepEqual(
    state.display.jobResponse!.results.map((r) => r.content_id),
    ["new-1"] // 保留当前任务的部分结果，而不是旧快照
  );
});

test("渐进: 取消时一条结果也没有 → 保留上次快照", () => {
  let state = startFull(initState(), "词", "job-B");
  state = step(state, {
    type: "job_terminal",
    job: makeJob("job-B", "completed", "词", STATUS_RUNNING, [makeResult("xhs", "old-1")]),
  });
  state = startFull(state, "新词", "job-C");
  state = step(state, {
    type: "job_progress",
    job: makeJob("job-C", "running", "新词", STATUS_RUNNING, []),
  });
  const cancelled = makeJob("job-C", "cancelled", "新词", STATUS_RUNNING, []);
  state = step(state, { type: "job_terminal", job: cancelled });

  assert.equal(state.display.cancelledNotice, true);
  assert.deepEqual(
    state.display.jobResponse!.results.map((r) => r.content_id),
    ["old-1"] // 无新结果 → 保留旧快照
  );
});

// ── 终态后迟到进度作废（已应用终态不重复） ─────────────────────────────

test("渐进: 已应用终态后迟到的进度被拒绝", () => {
  let state = startFull(initState(), "词", "job-B");
  state = step(state, {
    type: "job_terminal",
    job: makeJob("job-B", "completed", "词", {
      xhs: { status: "succeeded", count: 1 },
      douyin: { status: "empty", count: 0 },
      bilibili: { status: "empty", count: 0 },
      zhihu: { status: "empty", count: 0 },
    }, [makeResult("xhs", "final")]),
  });
  assert.equal(state.display.liveResponse, null);
  const after = step(state, {
    type: "job_progress",
    job: makeJob("job-B", "running", "词", STATUS_RUNNING, [makeResult("xhs", "late")]),
  });
  assert.equal(after.display.liveResponse, null); // 终态已应用 → 进度作废
});
