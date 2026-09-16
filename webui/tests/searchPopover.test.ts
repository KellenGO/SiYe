/**
 * 搜索下拉浮层状态机测试 —— 直接 import 编译后的生产模块
 * （webui/src/lib/searchPopover.ts），不复制任何生产逻辑。
 *
 * 覆盖"最近搜索浮层的显示/关闭逻辑"：
 * - 输入框获得焦点 → open；
 * - 焦点完全离开（等价点击外部）→ closed；
 * - 开始搜索 / 点击历史项填词 / 点击推荐词填词 → closed；
 * - 事件幂等与非法事件不改变状态。
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  INITIAL_POPOVER_STATE,
  RECOMMENDED_SEARCHES,
  searchPopoverReducer,
  type SearchPopoverEvent,
  type SearchPopoverState,
} from "../src/lib/searchPopover.js";

function step(
  state: SearchPopoverState,
  ...events: SearchPopoverEvent[]
): SearchPopoverState {
  return events.reduce((s, e) => searchPopoverReducer(s, e), state);
}

test("初始状态为 closed", () => {
  assert.equal(INITIAL_POPOVER_STATE, "closed");
});

test("focus_within 打开浮层；focus_left（点击外部）关闭", () => {
  const opened = searchPopoverReducer(INITIAL_POPOVER_STATE, { type: "focus_within" });
  assert.equal(opened, "open");
  const closed = searchPopoverReducer(opened, { type: "focus_left" });
  assert.equal(closed, "closed");
});

test("开始搜索后浮层关闭", () => {
  const opened = step(INITIAL_POPOVER_STATE, { type: "focus_within" });
  const closed = step(opened, { type: "search_started" });
  assert.equal(closed, "closed");
});

test("点击历史项填词（picked）后浮层关闭", () => {
  const closed = step(INITIAL_POPOVER_STATE, { type: "focus_within" }, { type: "picked" });
  assert.equal(closed, "closed");
});

test("点击推荐词填词（picked）后浮层关闭", () => {
  const closed = step(INITIAL_POPOVER_STATE, { type: "focus_within" }, { type: "picked" });
  assert.equal(closed, "closed");
});

test("事件幂等：连续 focus_within 保持 open，连续关闭事件保持 closed", () => {
  const opened = step(INITIAL_POPOVER_STATE, { type: "focus_within" }, { type: "focus_within" });
  assert.equal(opened, "open");
  const closed = step(INITIAL_POPOVER_STATE, { type: "focus_left" }, { type: "search_started" }, { type: "picked" });
  assert.equal(closed, "closed");
});

test("关闭状态下 focus_left / search_started / picked 不改变状态", () => {
  assert.equal(searchPopoverReducer("closed", { type: "focus_left" }), "closed");
  assert.equal(searchPopoverReducer("closed", { type: "search_started" }), "closed");
  assert.equal(searchPopoverReducer("closed", { type: "picked" }), "closed");
});

test("focus_within 可从任何状态打开（重新聚焦恢复浮层）", () => {
  const afterPick = searchPopoverReducer("closed", { type: "picked" });
  const reopened = searchPopoverReducer(afterPick, { type: "focus_within" });
  assert.equal(reopened, "open");
});

test("非法事件类型保持原状态（default 分支）", () => {
  const state = searchPopoverReducer("open", { type: "unknown" } as unknown as SearchPopoverEvent);
  assert.equal(state, "open");
});

// ── outside_pointer（点击面板外）/ escape（Escape 键）──
// 注意：DOM 的 contains 判断属于组件接线（SearchBar 的 searchPanelRef），
// 本测试只验证纯状态转换，不声称覆盖 DOM 判断。

test("outside_pointer 关闭浮层", () => {
  const opened = step(INITIAL_POPOVER_STATE, { type: "focus_within" });
  const closed = searchPopoverReducer(opened, { type: "outside_pointer" });
  assert.equal(closed, "closed");
});

test("outside_pointer 在已关闭状态保持 closed（幂等）", () => {
  assert.equal(searchPopoverReducer("closed", { type: "outside_pointer" }), "closed");
});

test("escape 关闭浮层，且不改变其他关闭事件语义", () => {
  const closed = step(INITIAL_POPOVER_STATE, { type: "focus_within" }, { type: "escape" });
  assert.equal(closed, "closed");
  assert.equal(searchPopoverReducer("closed", { type: "escape" }), "closed");
});

test("outside_pointer / escape 后重新聚焦可再次打开", () => {
  const afterOutside = step(INITIAL_POPOVER_STATE, { type: "focus_within" }, { type: "outside_pointer" });
  assert.equal(step(afterOutside, { type: "focus_within" }), "open");
  const afterEscape = step(INITIAL_POPOVER_STATE, { type: "focus_within" }, { type: "escape" });
  assert.equal(step(afterEscape, { type: "focus_within" }), "open");
});

test("推荐搜索词为固定五项", () => {
  assert.equal(RECOMMENDED_SEARCHES.length, 5);
  assert.ok(RECOMMENDED_SEARCHES.every((w) => typeof w === "string" && w.length > 0));
});
