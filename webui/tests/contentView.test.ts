import assert from "node:assert/strict";
import { test } from "node:test";
import { readContentView, writeContentView, type ContentViewScope } from "../src/lib/contentView.js";

test("内容偏好默认网格；缺失、损坏或禁止存储不阻止浏览", () => {
  for (const value of [null, "broken", "icon", "grid"]) {
    assert.equal(readContentView("search", { getItem: () => value }), "grid");
  }
  assert.equal(readContentView("search", null), "grid");
  assert.equal(readContentView("history", { getItem: () => { throw new Error("blocked"); } }), "grid");
  writeContentView("local", "list", { setItem: () => { throw new Error("blocked"); } });
});

test("五类页面独立保存，不读取旧文件夹导航偏好", () => {
  const values = new Map([["siye.favorites.local-folder-view.v1", "list"]]);
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
  const scopes: ContentViewScope[] = ["search", "history", "local", "remote-all", "remote-folder", "spaces"];
  for (const scope of scopes) assert.equal(readContentView(scope, storage), "grid");
  for (const scope of scopes) {
    writeContentView(scope, "list", storage);
    assert.equal(readContentView(scope, storage), "list");
    for (const other of scopes.filter((entry) => entry !== scope)) assert.equal(readContentView(other, storage), "grid");
    writeContentView(scope, "grid", storage);
  }
  assert.equal(values.get("siye.favorites.local-folder-view.v1"), "list");
});
