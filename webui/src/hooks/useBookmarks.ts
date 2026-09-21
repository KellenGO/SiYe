/**
 * 收藏库 hook（后端 /api/library）。
 *
 * 与旧实现的区别：收藏存本机 SQLite 而不是 localStorage，因此
 * 清缓存/换浏览器不会丢，并且支持收藏夹（一条内容可属于多个收藏夹）。
 *
 * 交互原则：
 * - 写操作先乐观更新界面，失败回滚并提示，避免"点了没反应"；
 * - 涉及 id/归属关系的操作（收藏、建夹）完成后重新拉取，保证界面与库里一致；
 * - 旧 localStorage 收藏只提示迁移、不自动删除，迁移成功后写标记不再打扰。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import type { PlatformSlug, UnifiedSearchResult } from "@/types/search";
import {
  addItemsToSystemCollection as apiAddToSystemCollection,
  addItemsToCollection as apiAddToCollection,
  createCollection as apiCreateCollection,
  decideMigration,
  deleteCollection as apiDeleteCollection,
  describeImport,
  exportPayload,
  fetchCollections,
  fetchItems,
  fetchStats,
  importPayload,
  LEGACY_BOOKMARKS_KEY,
  MIGRATION_DISMISS_KEY,
  MIGRATION_MARKER_KEY,
  parseBackupFile,
  removeItems,
  removeItemsFromSystemCollection as apiRemoveFromSystemCollection,
  removeItemsFromCollection as apiRemoveFromCollection,
  renameCollection as apiRenameCollection,
  toAddPayload,
  updateNote,
  type LibraryCollection,
  type LibraryItem,
  type LibraryStats,
  type SystemCollectionKey,
} from "@/lib/libraryApi";
import { resultKey } from "@/lib/resultTools";

export interface MigrationPrompt {
  /** 旧浏览器收藏里的条目数。 */
  count: number;
}

interface LibraryState {
  items: LibraryItem[];
  collections: LibraryCollection[];
  stats: LibraryStats | null;
  loading: boolean;
  error: string | null;
}

function errorText(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

const LIBRARY_QUERY_KEY = ["local-library"];
const EMPTY: LibraryState = { items: [], collections: [], stats: null, loading: true, error: null };

async function loadLibrary(): Promise<LibraryState> {
  const [items, collections, stats] = await Promise.all([fetchItems(), fetchCollections(), fetchStats()]);
  return { items, collections, stats, loading: false, error: null };
}

export function useBookmarks() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: LIBRARY_QUERY_KEY, queryFn: loadLibrary, retry: false });
  const state = query.data ?? EMPTY;
  const pending = useRef(new Set<string>());
  const [migration, setMigration] = useState<MigrationPrompt | null>(null);

  const refresh = useCallback(async () => {
    await client.invalidateQueries({ queryKey: LIBRARY_QUERY_KEY });
  }, [client]);

  // 旧浏览器收藏：迁移成功后永久标记；「以后再说」只在本次会话内隐藏，
  // 下次打开还会提示——否则旧数据就再也没有迁移入口了。
  useEffect(() => {
    let alreadyMigrated = false;
    let dismissedThisSession = false;
    let legacyRaw: string | null = null;
    try {
      alreadyMigrated = window.localStorage.getItem(MIGRATION_MARKER_KEY) === "1";
      dismissedThisSession = window.sessionStorage.getItem(MIGRATION_DISMISS_KEY) === "1";
      legacyRaw = window.localStorage.getItem(LEGACY_BOOKMARKS_KEY);
    } catch {
      return;
    }
    if (alreadyMigrated || dismissedThisSession) return;
    const decision = decideMigration({ legacyRaw, alreadyMigrated: false });
    if (decision.shouldOffer) setMigration({ count: decision.count });
  }, []);

  /** 返回是否写成功：调用方据此决定要不要接着弹"编辑归属"（失败就不打扰用户）。 */
  const toggleSystem = useCallback(
    async (collection: SystemCollectionKey, result: UnifiedSearchResult, fetchedAt: Partial<Record<PlatformSlug, string | null>> = {}): Promise<boolean> => {
      const entries = toAddPayload([result], fetchedAt);
      if (!entries.length) {
        toast.error("这条内容的格式无法收藏");
        return false;
      }
      const keys = entries.map((entry) => resultKey(entry.result));
      if (query.isPending || query.isError || keys.some((key) => pending.current.has(key))) return false;
      keys.forEach((key) => pending.current.add(key));
      const existing = new Map(state.items.map((item) => [item.key, item]));
      const allSaved = keys.every((key) => {
        const item = existing.get(key);
        return item && (collection === "default" ? item.saved : item.watchLater);
      });

      // 服务端是唯一真源：不 optimistic 更新。这样快速连点只会产生幂等的
      // 重复请求（后端按 platform|content_id 去重），不会出现"想取消却变成收藏"。
      try {
        if (allSaved) {
          // 取消收藏和「彻底删除」是同一个动作：整条记录连备注和所有归属一起移除。
          // 取消稍后再看只解绑稍后再看；两头都不沾的条目由后端顺手清掉。
          if (collection === "default") await removeItems(keys);
          else await apiRemoveFromSystemCollection(collection, keys);
        } else {
          const stats = await apiAddToSystemCollection(collection, entries);
          if (stats.skipped) toast.warning(describeImport(stats));
        }
        await refresh();
        return true;
      } catch (error) {
        const label = collection === "default" ? "收藏" : "稍后再看";
        toast.error(errorText(error, allSaved ? `取消${label}失败` : `加入${label}失败，请稍后重试`));
        return false;
      } finally {
        keys.forEach((key) => pending.current.delete(key));
      }
    },
    [refresh, state.items, query.isPending, query.isError],
  );

  const setSystemMembership = useCallback(async (
    keys: string[], collection: SystemCollectionKey, enabled: boolean,
  ) => {
    try {
      if (enabled) {
        const wanted = new Set(keys);
        const entries = state.items
          .filter((item) => wanted.has(item.key))
          .map((item) => ({ result: item.result, fetched_at: item.fetchedAt }));
        await apiAddToSystemCollection(collection, entries);
      } else {
        await apiRemoveFromSystemCollection(collection, keys);
      }
      await refresh();
      return true;
    } catch (error) {
      toast.error(errorText(error, "收藏夹归属未保存，请重试"));
      return false;
    }
  }, [refresh, state.items]);

  const deleteItems = useCallback(async (keys: string[]) => {
    try {
      await removeItems(keys);
      await refresh();
      return true;
    } catch (error) {
      toast.error(errorText(error, "删除本地收藏失败"));
      return false;
    }
  }, [refresh]);

  const saveNote = useCallback(
    async (key: string, note: string) => {
      if (pending.current.has(key)) return false;
      pending.current.add(key);
      try {
        await updateNote(key, note);
        await refresh();
        return true;
      } catch (error) {
        toast.error(errorText(error, "备注未保存，请重试"));
        return false;
      } finally {
        pending.current.delete(key);
      }
    },
    [refresh],
  );

  const importBackup = useCallback(
    async (raw: string) => {
      let payload: unknown;
      try {
        payload = parseBackupFile(raw);
      } catch (error) {
        toast.error(errorText(error, "备份文件无法读取"));
        return false;
      }
      try {
        const stats = await importPayload(payload);
        await refresh();
        toast.success(describeImport(stats));
        return true;
      } catch (error) {
        toast.error(errorText(error, "导入失败，现有收藏未被修改"));
        return false;
      }
    },
    [refresh],
  );

  const exportBackup = useCallback(async (): Promise<string | null> => {
    try {
      const payload = await exportPayload();
      return JSON.stringify(payload, null, 2);
    } catch (error) {
      toast.error(errorText(error, "导出失败，请稍后重试"));
      return null;
    }
  }, []);

  const createCollection = useCallback(
    async (name: string) => {
      try {
        await apiCreateCollection(name);
        await refresh();
        return true;
      } catch (error) {
        toast.error(errorText(error, "新建收藏夹失败"));
        return false;
      }
    },
    [refresh],
  );

  const renameCollection = useCallback(
    async (id: number, name: string) => {
      try {
        await apiRenameCollection(id, name);
        await refresh();
        return true;
      } catch (error) {
        toast.error(errorText(error, "重命名失败"));
        return false;
      }
    },
    [refresh],
  );

  const deleteCollection = useCallback(
    async (id: number) => {
      try {
        await apiDeleteCollection(id);
        await refresh();
        toast.success("收藏夹已删除，其中的内容已保留");
        return true;
      } catch (error) {
        toast.error(errorText(error, "删除收藏夹失败"));
        return false;
      }
    },
    [refresh],
  );

  const addToCollection = useCallback(
    async (keys: string[], collectionId: number) => {
      try {
        await apiAddToCollection(keys, collectionId);
        await refresh();
        return true;
      } catch (error) {
        toast.error(errorText(error, "加入收藏夹失败"));
        return false;
      }
    },
    [refresh],
  );

  const removeFromCollection = useCallback(
    async (keys: string[], collectionId: number) => {
      try {
        await apiRemoveFromCollection(keys, collectionId);
        await refresh();
        return true;
      } catch (error) {
        toast.error(errorText(error, "移出收藏夹失败"));
        return false;
      }
    },
    [refresh],
  );

  const runMigration = useCallback(async () => {
    let raw: string | null = null;
    try {
      raw = window.localStorage.getItem(LEGACY_BOOKMARKS_KEY);
    } catch {
      raw = null;
    }
    if (!raw) {
      setMigration(null);
      return;
    }
    let payload: unknown;
    try {
      payload = parseBackupFile(raw);
    } catch (error) {
      toast.error(errorText(error, "旧收藏无法读取，未做任何修改"));
      return;
    }
    try {
      const stats = await importPayload(payload);
      await refresh();
      try {
        if (stats.skipped === 0) window.localStorage.setItem(MIGRATION_MARKER_KEY, "1");
      } catch {
        /* 标记写不进去也不影响已导入的数据 */
      }
      if (stats.skipped === 0) {
        setMigration(null);
        toast.success(`旧收藏迁移完成：${describeImport(stats)}`);
      } else {
        toast.warning(`旧收藏尚未全部迁移：${describeImport(stats)}。旧数据与迁移入口已保留。`);
      }
    } catch (error) {
      toast.error(errorText(error, "迁移失败，旧收藏仍然保留"));
    }
  }, [refresh]);

  const dismissMigration = useCallback(() => {
    try {
      // 只在本次会话内隐藏：下次打开程序仍会提示，保证旧数据始终有迁移入口。
      window.sessionStorage.setItem(MIGRATION_DISMISS_KEY, "1");
    } catch {
      /* 忽略存储权限问题 */
    }
    setMigration(null);
  }, []);

  return {
    items: state.items,
    collections: state.collections,
    stats: state.stats,
    loading: query.isPending,
    error: query.error ? errorText(query.error, "收藏库暂时不可用，请重试") : null,
    migration,
    refresh,
    toggle: (result: UnifiedSearchResult, fetchedAt: Partial<Record<PlatformSlug, string | null>> = {}) => toggleSystem("default", result, fetchedAt),
    toggleSystem,
    setSystemMembership,
    deleteItems,
    saveNote,
    importBackup,
    exportBackup,
    createCollection,
    renameCollection,
    deleteCollection,
    addToCollection,
    removeFromCollection,
    runMigration,
    dismissMigration,
  };
}

export type BookmarkLibrary = ReturnType<typeof useBookmarks>;
