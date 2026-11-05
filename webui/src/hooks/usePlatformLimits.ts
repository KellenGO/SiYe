/**
 * 按平台独立搜索数量 hook（Round 15）。
 *
 * 以 localStorage 为唯一持久化来源。**必须跨组件共享同一份状态** ——
 * 设置页（`AccountsPage` → `SearchSettings`）和搜索页（`useSearchExperience`）
 * 是同时挂载的：以前各持一份 `useState`，设置里改完，搜索页仍旧用挂载时读到
 * 的旧值发请求，而界面文案写的是"修改后从下一次搜索开始生效"，用户不改端口
 * 重启永远看不到效果。
 *
 * 现在用模块级「单例 + 订阅」广播：任何一处 `setLimit` 都会同步到所有消费者。
 * 额外监听 `storage` 事件，多标签页之间也能跟着变。
 */

import { useCallback, useSyncExternalStore } from "react";
import {
  readPlatformLimits,
  resetPlatformLimits,
  updatePlatformLimit,
  writePlatformLimits,
  PLATFORM_LIMITS_STORAGE_KEY,
  type PlatformLimitMap,
} from "@/lib/platformLimits";
import type { PlatformSlug } from "@/types/search";

export interface UsePlatformLimitsResult {
  limits: PlatformLimitMap;
  /** 更新单个平台数量（非法值忽略；立即写回 localStorage 并广播）。 */
  setLimit: (platform: PlatformSlug, raw: unknown) => void;
  /** 恢复四个平台为默认值（不自动发起搜索）。 */
  resetAll: () => void;
}

const listeners = new Set<() => void>();
let current: PlatformLimitMap | null = null;

function snapshot(): PlatformLimitMap {
  if (current === null) current = readPlatformLimits(localStorage);
  return current;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function publish(next: PlatformLimitMap): void {
  current = next;
  writePlatformLimits(localStorage, next);
  for (const listener of [...listeners]) listener();
}

// 多标签页：别的标签改了 localStorage，这里跟着刷新快照。
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== PLATFORM_LIMITS_STORAGE_KEY && event.key !== null) return;
    current = readPlatformLimits(localStorage);
    for (const listener of [...listeners]) listener();
  });
}

export function usePlatformLimits(): UsePlatformLimitsResult {
  const limits = useSyncExternalStore(subscribe, snapshot, snapshot);

  const setLimit = useCallback((platform: PlatformSlug, raw: unknown) => {
    publish(updatePlatformLimit(snapshot(), platform, raw));
  }, []);

  const resetAll = useCallback(() => {
    publish(resetPlatformLimits());
  }, []);

  return { limits, setLimit, resetAll };
}

/** 仅供测试：丢弃模块级缓存，让下一次读取重新落回 localStorage。 */
export function __resetPlatformLimitsCacheForTests(): void {
  current = null;
  listeners.clear();
}
