/**
 * 平台失败错误码表。
 *
 * 设计依据（2026-11-05 用户反馈）：**用户只需要知道"哪个平台失败了"，不需要读
 * 一大段原因**。界面上显示「失败 · S04」，具体原因按错误码查这张表 ——
 * 表主要是给开发和排障用的。
 *
 * 为什么不用后端自由文本：`error_summary` 是各平台随手写的一句话，长度、
 * 语气、信息量都不一致，还会随版本漂移；把它直接摊在界面上，既不好看也不好比。
 * 错误码是稳定的、可断言的，出问题时用户报一个码就能定位。
 *
 * 编码规则：`S` + 两位数字。**只增不改**，旧码不复用。
 */

import type { PlatformStatus } from "@/types/search";

export interface FailureCode {
  code: string;
  /** 用户可见的一句话（短）。 */
  short: string;
  /** 给我们自己看的解释：什么情况会触发、下一步怎么查。 */
  detail: string;
}

/** 平台状态 → 错误码。成功类状态不进表。 */
const CODE_BY_STATUS: Partial<Record<PlatformStatus | "partial", string>> = {
  failed: "S01",
  timed_out: "S02",
  rate_limited: "S03",
  login_required: "S04",
  empty: "S05",
  cancelled: "S06",
  partial: "S07",
};

export const FAILURE_CODES: Record<string, FailureCode> = {
  S01: {
    code: "S01",
    short: "平台没有返回内容",
    detail:
      "搜取过程报错后被判定为失败。常见于平台接口结构变化、响应被风控拦截、" +
      "或 worker 子进程异常退出。排查：看后端日志里该平台 worker 的尾部输出。",
  },
  S02: {
    code: "S02",
    short: "平台响应超时",
    detail:
      "单平台请求超过了等待上限（默认 100 秒），worker 被强制终止。" +
      "常见于平台限速、网络抖动或浏览器启动过慢。排查：看 timings 里的 navigation_ms / search_api_ms。",
  },
  S03: {
    code: "S03",
    short: "平台要求稍后再试",
    detail:
      "平台返回了限流或验证码信号，进入冷却期（基准 60 秒、重复触发翻倍、封顶 300 秒）。" +
      "冷却期内该平台会被直接跳过，不消耗请求。排查：看 search_metrics 里该平台的冷却记录。",
  },
  S04: {
    code: "S04",
    short: "需要重新登录",
    detail:
      "预检发现该平台的登录态缺失或已失效，因此没有启动浏览器（省掉十几秒白等）。" +
      "排查：账号页确认 profile_exists 与实际登录状态；平台侧会话可能自然过期。",
  },
  S05: {
    code: "S05",
    short: "这个平台没搜到",
    detail:
      "请求成功但有效结果为 0。可能是关键词确实没有内容，也可能是平台返回空列表、" +
      "分页参数失效导致全被判重（此时后端会附加 error_summary 说明）。" +
      "排查：搜一个宽泛关键词复现；看该平台的 page_requests 与 duplicate_count。",
  },
  S06: {
    code: "S06",
    short: "已取消",
    detail: "用户主动取消，已收集到的部分结果会保留。不是错误。",
  },
  S07: {
    code: "S07",
    short: "只拿到部分内容",
    detail:
      "平台报告 partial：拿到了部分结果就停止了（例如分页中途失败但已有内容）。" +
      "已有结果照常展示，整轮结果标记为部分完成。",
  },
};

/** 平台失败状态 → 错误码；成功类状态返回 null。 */
export function failureCodeFor(status: PlatformStatus | string): string | null {
  return CODE_BY_STATUS[status as PlatformStatus] ?? null;
}

/** 按错误码取条目；未知码返回 null（界面上只显示码本身）。 */
export function failureCodeInfo(code: string | null | undefined): FailureCode | null {
  if (!code) return null;
  return FAILURE_CODES[code] ?? null;
}

/** 界面上显示的紧凑标签：`失败 · S01`。 */
export function failureLabel(status: PlatformStatus | string): string | null {
  const code = failureCodeFor(status);
  const info = failureCodeInfo(code);
  if (!code || !info) return null;
  return `失败 · ${code}`;
}
