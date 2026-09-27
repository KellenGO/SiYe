/** 内容呈现偏好与文件夹导航偏好分开，按页面分别保存。 */
export type ContentView = "list" | "grid";
export type ContentViewScope = "search" | "history" | "local" | "remote-all" | "remote-folder";

function storageKey(scope: ContentViewScope): string {
  return `siye.content-view.${scope}.v1`;
}

export function readContentView(scope: ContentViewScope, storage?: Pick<Storage, "getItem"> | null): ContentView {
  try {
    const target = storage === undefined ? window.localStorage : storage;
    return target?.getItem(storageKey(scope)) === "list" ? "list" : "grid";
  } catch {
    return "grid";
  }
}

export function writeContentView(scope: ContentViewScope, view: ContentView, storage?: Pick<Storage, "setItem"> | null): void {
  try {
    const target = storage === undefined ? window.localStorage : storage;
    target?.setItem(storageKey(scope), view);
  } catch {
    // 无法保存时，当前页面仍可正常切换。
  }
}
