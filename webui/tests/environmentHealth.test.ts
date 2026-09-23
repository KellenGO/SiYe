import { test } from "node:test";
import assert from "node:assert/strict";

import {
  environmentHealthWarning,
  type EnvironmentHealth,
} from "../src/lib/environmentHealth.js";

const healthy: EnvironmentHealth = {
  status: "ok",
  environment_status: "ok",
  backend_available: true,
  version: "0.2.0",
  api_version: "0.2.0",
  web_version: "0.2.0",
  version_match: true,
  browser_available: true,
  browser_backend: "chrome",
  redis_required: false,
  redis_available: null,
};

test("正常环境不显示启动警告", () => {
  assert.equal(environmentHealthWarning(healthy), null);
});

test("后端不可用显示明确提示", () => {
  assert.equal(environmentHealthWarning(false), "四野服务未连接，请重新启动应用。");
});

test("浏览器不可用显示安装提示", () => {
  const warning = environmentHealthWarning({ ...healthy, browser_available: false });
  if (!warning) throw new Error("expected browser warning");
  assert.ok(warning.includes("没有找到可用的浏览器"));
  assert.ok(!warning.includes("playwright"));
});

test("版本不匹配显示重建提示", () => {
  const warning = environmentHealthWarning({ ...healthy, version_match: false });
  if (!warning) throw new Error("expected version warning");
  assert.ok(warning.includes("应用文件版本不一致"));
  assert.ok(!warning.includes("重新构建"));
});
