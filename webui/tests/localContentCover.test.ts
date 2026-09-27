import assert from "node:assert/strict";
import { test } from "node:test";
import { xhsCoverFallback } from "../src/lib/localContentCover.js";

test("小红书带时效封面可从原路径提取原图，保留 spectrum 前缀", () => {
  const prefix = "http://sns-webpic-qc.xhscdn.com/202609091523/0123456789abcdef0123456789abcdef/";
  assert.equal(xhsCoverFallback(prefix + "spectrum/image123!nc_n_webp_mw_1", "xhs"), "https://sns-img-qc.xhscdn.com/spectrum/image123");
  assert.equal(xhsCoverFallback(prefix + "image456!nc_n_webp_mw_1", "xhs"), "https://sns-img-qc.xhscdn.com/image456");
});

test("不对其他平台、未知域名或未知路径猜测封面地址", () => {
  for (const cover of [null, "", "invalid", "https://sns-webpic-qc.xhscdn.com/unrecognized", "https://sns-webpic-qc.xhscdn.com.evil.test/202609091523/0123456789abcdef0123456789abcdef/image123"]) {
    assert.equal(xhsCoverFallback(cover, "xhs"), null);
  }
  assert.equal(xhsCoverFallback("https://sns-webpic-qc.xhscdn.com/202609091523/0123456789abcdef0123456789abcdef/image123", "zhihu"), null);
});
