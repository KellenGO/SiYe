import assert from "node:assert/strict";
import { test } from "node:test";

import { LOCAL_FOLDER_VIEW_STORAGE_KEY, readLocalFolderView, writeLocalFolderView } from "../src/lib/localFolderView.js";

function storageWith(value: string | null) {
  let current = value;
  return {
    getItem: () => current,
    setItem: (_key: string, next: string) => { current = next; },
  };
}

test("本地收藏夹视图偏好默认列表，并拒绝无效值", () => {
  assert.equal(readLocalFolderView(storageWith(null)), "list");
  assert.equal(readLocalFolderView(storageWith("unexpected")), "list");
  assert.equal(readLocalFolderView(storageWith("icon")), "icon");
});

test("本地收藏夹视图偏好使用带命名空间的键且存储失败不影响页面", () => {
  const storage = storageWith(null);
  writeLocalFolderView("icon", storage);
  assert.equal(readLocalFolderView(storage), "icon");
  assert.equal(LOCAL_FOLDER_VIEW_STORAGE_KEY.startsWith("siye."), true);
  writeLocalFolderView("list", { setItem: () => { throw new Error("blocked"); } });
});
