/**
 * 搜索下拉浮层状态机（无 React 依赖）。
 *
 * 视觉基准要求：输入框聚焦时在输入框下方弹出浮层（最近搜索 + 推荐搜索）；
 * 点击外部或开始搜索后关闭浮层。本模块把"开/关"规则提取为生产 reducer，
 * 组件通过 useReducer 消费同一套逻辑，node:test 直接测试本模块。
 *
 * 关闭规则（与组件接线一致）：
 * - focus_within   → open（搜索输入框获得焦点）
 * - focus_left     → closed（焦点离开 / Escape 关闭）
 * - outside_pointer→ closed（document pointerdown 点击整个搜索 form 之外）
 * - search_started → closed（提交搜索）
 * - picked         → closed（点击历史项或推荐词填入关键词）
 *
 * 注意：DOM 的 contains 判断属于组件接线（SearchBar.tsx 的
 * searchPanelRef），本模块只负责纯状态转换，reducer 测试不覆盖 DOM 判断。
 */

export type SearchPopoverState = "open" | "closed";

export type SearchPopoverEvent =
  | { type: "focus_within" }
  | { type: "focus_left" }
  | { type: "outside_pointer" }
  | { type: "escape" }
  | { type: "search_started" }
  | { type: "picked" };

export const INITIAL_POPOVER_STATE: SearchPopoverState = "closed";

export function searchPopoverReducer(
  state: SearchPopoverState,
  event: SearchPopoverEvent
): SearchPopoverState {
  switch (event.type) {
    case "focus_within":
      return "open";
    case "focus_left":
    case "outside_pointer":
    case "escape":
    case "search_started":
    case "picked":
      return "closed";
    default:
      return state;
  }
}

/** 推荐搜索词（效果稿固定五项；点击后只填入搜索框）。 */
export const RECOMMENDED_SEARCHES: readonly string[] = [
  "AI 智能体",
  "产品设计",
  "开源项目",
  "自媒体工具",
  "效率工作流",
];
