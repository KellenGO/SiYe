/** 本地收藏夹浏览方式；读写失败时自然退回既有列表模式。 */
export const LOCAL_FOLDER_VIEW_STORAGE_KEY = "siye.favorites.local-folder-view.v1";

export type LocalFolderView = "list" | "icon";

type ReadStorage = Pick<Storage, "getItem">;
type WriteStorage = Pick<Storage, "setItem">;

export function readLocalFolderView(storage: ReadStorage | null = window.localStorage): LocalFolderView {
  try {
    return storage?.getItem(LOCAL_FOLDER_VIEW_STORAGE_KEY) === "icon" ? "icon" : "list";
  } catch {
    return "list";
  }
}

export function writeLocalFolderView(view: LocalFolderView, storage: WriteStorage | null = window.localStorage): void {
  try {
    storage?.setItem(LOCAL_FOLDER_VIEW_STORAGE_KEY, view);
  } catch {
    // 隐私模式或存储被禁用时保留当前会话状态即可。
  }
}
