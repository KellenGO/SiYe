/**
 * 热搜卡片纯逻辑测试 —— 直接 import 生产模块（webui/src/lib/trendingApi.ts）。
 *
 * 覆盖四件容易出错的事：热度格式化（万/亿进位）、后端脏数据的兜底、
 * 标签页规则（三个平台都在、默认选第一个有词的），以及小红书不会回到标签里。
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  MAX_WORDS_SHOWN,
  TRENDING_PLATFORMS,
  formatHeat,
  initialTab,
  toTrendingSnapshot,
  topWords,
  trendingTabs,
} from "../src/lib/trendingApi.js";

test("热度按万 / 亿进位，拿不到就返回 null", () => {
  assert.equal(formatHeat(3456), "3456");
  assert.equal(formatHeat(11943156), "1194万");
  assert.equal(formatHeat(34000), "3.4万");
  assert.equal(formatHeat(120000000), "1.2亿");
  assert.equal(formatHeat(null), null);
  assert.equal(formatHeat(-1), null);
  assert.equal(formatHeat(Number.NaN), null);
});

test("规整后端响应：丢掉未知平台、脏项与非法状态", () => {
  const snapshot = toTrendingSnapshot({
    platforms: {
      douyin: {
        status: "ok",
        active_time: "2026-09-19 13:41:06",
        words: [
          { rank: 1, word: "热词", hot_value: 100 },
          { rank: 2, word: "   " },
          { word: "没有 rank" },
          "不是字典",
        ],
      },
      weibo: { status: "ok", words: [{ rank: 1, word: "不该出现" }] },
      bilibili: { status: "什么鬼", words: [] },
    },
    fetched_at: "2026-09-19T05:41:06+00:00",
    cached: true,
  });

  assert.deepEqual(Object.keys(snapshot.platforms).sort(), ["bilibili", "douyin"]);
  assert.equal(snapshot.platforms.douyin?.activeTime, "2026-09-19 13:41:06");
  assert.deepEqual(snapshot.platforms.douyin?.words.map((item) => item.word), ["热词", "没有 rank"]);
  assert.equal(snapshot.platforms.douyin?.words[0]?.hotValue, 100);
  assert.equal(snapshot.platforms.douyin?.words[1]?.hotValue, null);
  assert.equal(snapshot.platforms.douyin?.words[1]?.rank, 2);
  assert.equal(snapshot.platforms.bilibili?.status, "failed");
  assert.equal(snapshot.cached, true);
});

test("标签页：三个平台都在，并标出谁有词", () => {
  const snapshot = toTrendingSnapshot({
    platforms: {
      douyin: { status: "ok", words: [{ rank: 1, word: "a" }] },
      bilibili: { status: "failed", words: [] },
      zhihu: { status: "ok", words: [{ rank: 1, word: "b" }] },
    },
  });
  const tabs = trendingTabs(snapshot);
  assert.deepEqual(tabs.map((tab) => tab.platform), ["douyin", "bilibili", "zhihu"]);
  assert.deepEqual(tabs.map((tab) => tab.hasWords), [true, false, true]);
  assert.deepEqual(tabs.map((tab) => tab.status), ["ok", "failed", "ok"]);
});

test("还没拿到数据时所有标签均等待实际状态", () => {
  const tabs = trendingTabs(undefined);
  assert.deepEqual(tabs.map((tab) => tab.platform), ["douyin", "bilibili", "zhihu"]);
  assert.ok(tabs.every((tab) => tab.status === "missing" && !tab.hasWords));
});

test("小红书不在热搜平台里：旧响应里的 xhs 会被丢掉", () => {
  assert.deepEqual(TRENDING_PLATFORMS, ["douyin", "bilibili", "zhihu"]);
  const snapshot = toTrendingSnapshot({
    platforms: {
      xhs: { status: "ok", words: [{ rank: 1, word: "小红书热词", hot_value: 9475000 }] },
      douyin: { status: "ok", words: [{ rank: 1, word: "抖音热词" }] },
    },
  });
  assert.equal(snapshot.platforms.xhs, undefined);
  assert.deepEqual(trendingTabs(snapshot).map((tab) => tab.platform), ["douyin", "bilibili", "zhihu"]);
  assert.equal(initialTab(snapshot), "douyin");
});

test("失败只保留文案，不产出小红书专属占位", () => {
  const snapshot = toTrendingSnapshot({
    platforms: {
      bilibili: { status: "failed", words: [], message: "热搜暂时取不到，稍后再试" },
    },
  });
  assert.equal(snapshot.platforms.bilibili?.message, "热搜暂时取不到，稍后再试");
  assert.equal(snapshot.platforms.bilibili?.status, "failed");
});

test("默认选中第一个有词的平台", () => {
  const snapshot = toTrendingSnapshot({
    platforms: {
      douyin: { status: "failed", words: [] },
      bilibili: { status: "ok", words: [{ rank: 1, word: "b" }] },
    },
  });
  assert.equal(initialTab(snapshot), "bilibili");
  // 一个词都没有时退回第一个平台，保证标签页始终有选中项
  assert.equal(initialTab(toTrendingSnapshot({ platforms: {} })), "douyin");
  assert.equal(initialTab(undefined), "douyin");
});

test("每个标签只取前 N 条", () => {
  const words = Array.from({ length: MAX_WORDS_SHOWN + 5 }, (_, index) => ({
    rank: index + 1,
    word: `w${index}`,
    hotValue: null,
  }));
  assert.equal(topWords(words).length, MAX_WORDS_SHOWN);
  assert.equal(topWords(words, 3).length, 3);
  assert.equal(topWords(words, 0).length, 0);
});
