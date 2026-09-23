import { test } from "node:test";
import assert from "node:assert/strict";
import { GUIDE_PREFERENCE_KEY, GUIDE_SESSION_KEY, GUIDE_STEPS, guideHighlight, initialGuideState, writeGuidePreference, type GuideStorage } from "../src/lib/onboarding.js";

function storage(): GuideStorage {
  const values = new Map<string, string>();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); } };
}

test("首次使用给出邀请；跳过只影响当前标签页会话", () => {
  const local = storage(), session = storage();
  assert.equal(initialGuideState(local, session), -1);
  writeGuidePreference(session, GUIDE_SESSION_KEY, "hidden");
  assert.equal(initialGuideState(local, session), null);
  assert.equal(initialGuideState(local, storage()), -1);
});

test("完成或选择不再提示后，新会话也不会自动弹出", () => {
  for (const choice of ["completed", "dismissed"]) {
    const local = storage();
    writeGuidePreference(local, GUIDE_PREFERENCE_KEY, choice);
    assert.equal(initialGuideState(local, storage()), null);
  }
});

test("帮助页显式重开可覆盖不再提示，并在刷新后保留所在步骤", () => {
  const local = storage(), session = storage();
  writeGuidePreference(local, GUIDE_PREFERENCE_KEY, "dismissed");
  writeGuidePreference(session, GUIDE_SESSION_KEY, "2");
  assert.equal(initialGuideState(local, session), 2);
  writeGuidePreference(session, GUIDE_SESSION_KEY, "hidden");
  assert.equal(initialGuideState(local, session), null);
});

test("损坏或被禁用的存储不会阻止应用使用", () => {
  const local = storage(), session = storage();
  for (const bad of ["99", "-2", "NaN", "", "null", "1.5"]) {
    writeGuidePreference(session, GUIDE_SESSION_KEY, bad);
    assert.equal(initialGuideState(local, session), -1);
  }
  const denied: GuideStorage = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  assert.equal(initialGuideState(denied, denied), -1);
  assert.equal(writeGuidePreference(denied, GUIDE_PREFERENCE_KEY, "dismissed"), false);
  assert.equal(initialGuideState(null, null), -1);
});

test("步骤数以 GUIDE_STEPS 为准：每一步可恢复，越界回到邀请", () => {
  const local = storage(), session = storage();
  for (let index = 0; index < GUIDE_STEPS.length; index += 1) {
    writeGuidePreference(session, GUIDE_SESSION_KEY, String(index));
    assert.equal(initialGuideState(local, session), index);
  }
  writeGuidePreference(session, GUIDE_SESSION_KEY, String(GUIDE_STEPS.length));
  assert.equal(initialGuideState(local, session), -1);
});

test("每一步都落到一个真实页面，key 不重复", () => {
  for (const step of GUIDE_STEPS) {
    assert.ok(step.route.startsWith("#/"), `步骤 ${step.key} 的路由不是 hash 路由：${step.route}`);
    assert.ok(step.key.length > 0);
  }
  assert.equal(new Set(GUIDE_STEPS.map((step) => step.key)).size, GUIDE_STEPS.length);
});

test("高亮框只指向 data-tour 锚点，没声明的步骤不产生浮层", () => {
  const highlighted = GUIDE_STEPS.filter((step) => guideHighlight(step) !== undefined);
  // 一条都没有的话 GuideSpotlight 就是死代码，别再留着它。
  assert.ok(highlighted.length > 0, "至少要有一个步骤声明 highlight");
  for (const step of highlighted) {
    const selector = guideHighlight(step);
    // 用 data-tour 而不是 class：锚点是教程和页面之间的显式契约，
    // 顺带让 pytest 那条守卫能静态核对它真的存在。
    assert.ok(
      /^\[data-tour="[a-z][a-z0-9-]*"\]$/.test(String(selector)),
      `步骤 ${step.key} 的高亮目标要写成 [data-tour="..."]，现在是 ${selector}`,
    );
  }
  for (const step of GUIDE_STEPS) {
    if (guideHighlight(step) === undefined) {
      assert.ok(!("highlight" in step), `步骤 ${step.key} 声明了高亮却没被识别出来`);
    }
  }
});
