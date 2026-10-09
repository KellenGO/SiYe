import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeReadingDetail, readingError, readingImageUrl, supportsReading } from "../src/lib/reading.js";

const source = { platform: "zhihu" as const, content_type: "answer", content_id: "42", url: "https://www.zhihu.com/question/1/answer/42" };

test("仅可信且身份匹配的知乎回答和文章进入站内阅读", () => {
  assert.equal(supportsReading(source), true);
  assert.equal(supportsReading({ ...source, url: "https://www.zhihu.com/answer/42" }), true);
  assert.equal(supportsReading({ ...source, content_type: "article", url: "https://zhuanlan.zhihu.com/p/42" }), true);
  for (const url of ["https://www.zhihu.com/answer/43", "https://www.zhihu.com.evil.test/answer/42", "javascript:alert(1)", "https://user:pass@www.zhihu.com/answer/42", "https://www.zhihu.com:444/answer/42"]) assert.equal(supportsReading({ ...source, url }), false);
  assert.equal(supportsReading({ ...source, platform: "xhs" }), false);
  assert.equal(supportsReading({ ...source, content_type: "zvideo" }), false);
});

test("图片限定知乎 CDN 并升级 HTTPS", () => {
  assert.equal(readingImageUrl("//pic.zhimg.com/a"), "https://pic.zhimg.com/a");
  assert.equal(readingImageUrl("http://pic.zhimg.com/a"), "https://pic.zhimg.com/a");
  for (const value of [null, "data:image/png,x", "https://pic.zhimg.com.evil.test/a", "https://u:p@pic.zhimg.com/a", "https://localhost/a"]) assert.equal(readingImageUrl(value), null);
});

test("正文响应不能串到其它内容，非法图片给出明确占位", () => {
  const raw = { content_id: "42", content_type: "answer", limited: true, notices: ["正文截断"], blocks: [{ type: "paragraph", text: "正文" }, { type: "image", text: "图", url: "https://evil.test/a" }, { type: "script", text: "alert()" }] };
  const detail = decodeReadingDetail(raw, source);
  assert.equal(detail.limited, true);
  assert.deepEqual(detail.blocks.map((block) => block.type), ["paragraph", "unsupported"]);
  assert.throws(() => decodeReadingDetail({ ...raw, content_id: "43" }, source));
  assert.throws(() => decodeReadingDetail({}, source));
});

test("失败文案不会展示平台异常或凭据", () => {
  assert.ok(readingError({ response: { data: { detail: { code: "busy" } } } }).includes("正在进行"));
  assert.equal(readingError(new Error("cookie=private-secret")), "暂时无法读取正文，请重试或打开原文。");
});
