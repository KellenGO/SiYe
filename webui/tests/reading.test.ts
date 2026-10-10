import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeReadingComments, decodeReadingDetail, readingError, readingImageUrl, readingMediaUrl, supportsReading } from "../src/lib/reading.js";

const source = { platform: "zhihu" as const, content_type: "answer", content_id: "42", url: "https://www.zhihu.com/question/1/answer/42" };

test("评论校验来源，保留回复及部分结果，不带出额外字段", () => {
  const raw = { ...source, entries: [{ id: "a", text: "<script>只是文字</script>", author: "读者", parent_id: "root", cookie: "secret", like_count: 2 }], limited: true, sort: "热门", notice: "部分读取失败" };
  const result = decodeReadingComments(raw, source);
  assert.equal(result.entries[0].parent_id, "root");
  assert.equal(result.entries[0].text, raw.entries[0].text);
  assert.equal(result.limited, true);
  assert.ok(!JSON.stringify(result).includes("secret"));
  assert.throws(() => decodeReadingComments({ ...raw, content_id: "43" }, source));
  assert.throws(() => decodeReadingComments({ ...raw, platform: "douyin" }, source));
  assert.throws(() => decodeReadingComments({}, source));
});

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

test("三平台只接受匹配的官方内容链接，不把番剧或短链当作普通视频", () => {
  const examples = [
    { platform: "xhs" as const, content_type: "note", content_id: "abcdef0123456789abcdef01", url: "https://www.xiaohongshu.com/explore/abcdef0123456789abcdef01?xsec_token=t" },
    { platform: "douyin" as const, content_type: "video", content_id: "123", url: "https://www.douyin.com/video/123" },
    { platform: "bilibili" as const, content_type: "video", content_id: "BV1234567890", url: "https://www.bilibili.com/video/BV1234567890" },
    { platform: "bilibili" as const, content_type: "video", content_id: "123", url: "https://www.bilibili.com/video/av123" },
  ];
  for (const row of examples) {
    assert.equal(supportsReading(row), true);
    assert.equal(supportsReading({ ...row, content_id: "other" }), false);
    assert.equal(supportsReading({ ...row, url: row.url.replace(".com", ".com.evil.test") }), false);
  }
  assert.equal(supportsReading({ ...examples[2], url: "https://www.bilibili.com/bangumi/play/ep123" }), false);
  assert.equal(supportsReading({ ...examples[1], url: "https://v.douyin.com/123" }), false);
});

test("本机媒体句柄允许播放，远端签名地址和跨平台响应被拒绝", () => {
  const url = "/api/reading/media/" + "a".repeat(32);
  assert.equal(readingMediaUrl(url), url);
  assert.equal(readingImageUrl(url), url);
  for (const row of ["//evil.test/api/reading/media/" + "a".repeat(32), url + "?url=x", "/api/reading/media/../", "https://cdn.bilivideo.com/a.mp4"]) assert.equal(readingMediaUrl(row), null);
  const src = { platform: "douyin" as const, content_type: "video", content_id: "123" };
  const raw = { ...src, blocks: [], portrait: true, media: [{ url, label: "视频" }, { url: "https://evil.test/a", label: "bad" }] };
  const value = decodeReadingDetail(raw, src);
  assert.deepEqual(value.media, [{ url, label: "视频" }]);
  assert.equal(value.portrait, true);
  assert.throws(() => decodeReadingDetail({ ...raw, platform: "xhs" }, src));
});
