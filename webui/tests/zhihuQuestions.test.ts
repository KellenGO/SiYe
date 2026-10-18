import { test } from "node:test";
import assert from "node:assert/strict";
import type { UnifiedSearchResult } from "../src/types/search.js";
import { answerMetrics, decodeQuestionAnswers, groupZhihuQuestions, mergeAnswers, questionOf, sortAnswers } from "../src/lib/zhihuQuestions.js";
import { filterResultGroups, DEFAULT_FILTERS, groupKey, resultSources, exportRows } from "../src/lib/resultTools.js";
import { publicResult } from "../src/lib/bookmarks.js";
import { expandGroupedResults } from "../src/lib/search/results.js";

function answer(id = "42", question = "1", votes = 4): UnifiedSearchResult {
  return { platform: "zhihu", content_type: "answer", content_id: id, title: "测试问题",
    url: `https://www.zhihu.com/question/${question}/answer/${id}`, author: `作者${id}`, snippet: `摘要${id}`,
    published_at: null, cover_url: null, metrics: { like_count: votes, comment_count: 5, view_count: 112424515 }, rank: 0 };
}

test("同一问题合为卡片，仍保留具体回答身份和稳定问题键", () => {
  const low = answer(), high = answer("99", "1", 5436), other = answer("80", "2");
  const cards = groupZhihuQuestions([low, other, high]);
  assert.equal(cards.length, 2);
  assert.deepEqual(cards[0].question_answers?.map(row => row.content_id), ["99", "42"]);
  assert.equal(cards[0].author, null);
  assert.equal(cards[0].metrics.view_count, undefined);
  assert.ok(/^作者99：/.test(cards[0].snippet!));
  assert.equal(groupKey(cards[0]), groupKey(groupZhihuQuestions([low])[0]));
  assert.deepEqual(resultSources(cards[0]).map(row => row.content_id), ["99", "42"]);
  assert.deepEqual(exportRows([cards[0]], () => ({ note: "", fetchedAt: null, savedAt: null })).map(row => row.result.content_id), ["99", "42"]);
  assert.throws(() => publicResult(cards[0]));
});

test("按具体回答筛选后才生成问题卡片，问题分组不吞掉其他平台来源", () => {
  const low = answer(), high = answer("99");
  const xhs: UnifiedSearchResult = { ...low, platform: "xhs", content_type: "note", content_id: "x1", url: "https://www.xiaohongshu.com/explore/x1" };
  const grouped = { ...low, grouped_sources: [low, xhs] };
  const cards = groupZhihuQuestions([grouped, high]);
  assert.equal(cards.length, 2);
  assert.equal(cards[1].platform, "xhs");
  const filtered = filterResultGroups([low, high], { ...DEFAULT_FILTERS, query: "作者99" }, Date.now(), "zhihu");
  assert.deepEqual(groupZhihuQuestions(filtered)[0].question_answers?.map(row => row.content_id), ["99"]);
});

test("问题元信息只接纳同一问题的明确指标，旧链接可以恢复问题", () => {
  assert.equal(questionOf(answer())?.id, "1");
  const row = { ...answer(), question: { id: "1", title: "问题标题", url: "https://evil.test", metrics: { view_count: 112424515, answer_count: 0 } } };
  assert.deepEqual(questionOf(row), { id: "1", title: "问题标题", url: "https://www.zhihu.com/question/1", metrics: { view_count: 112424515 } });
  assert.equal(questionOf({ ...row, question: { ...row.question, id: "2" } }), null);
  assert.equal(questionOf({ ...row, url: "https://evil.test/question/1/answer/42" }), null);
  assert.equal(questionOf({ ...row, url: "https://www.zhihu.com/question/1/answer/43" }), null);
});

test("按赞数排序、同赞保持顺序，分页合并去重并拒绝其他问题", () => {
  assert.deepEqual(sortAnswers([answer("42", "1", 10), answer("99", "1", 10), answer("80", "1", 30)]).map(row => row.content_id), ["80", "42", "99"]);
  const merged = mergeAnswers([answer()], [answer("99", "1", 5436), answer("42", "1", 5), answer("80", "2")], "1");
  assert.deepEqual(merged.map(row => row.content_id), ["99", "42"]);
  assert.equal(merged[1].metrics.like_count, 5);
  assert.ok(merged.every(row => row.metrics.view_count === undefined));
  assert.equal(mergeAnswers([], Array.from({ length: 110 }, (_, i) => answer(String(i+1))), "1").length, 100);
});

test("旧收藏回答隐藏混合浏览量，文章和其他平台维持指标", () => {
  assert.deepEqual(answerMetrics(answer()), { like_count: 4, comment_count: 5 });
  assert.equal(publicResult(answer()).metrics.view_count, undefined);
  assert.equal(answerMetrics({ ...answer(), content_type: "article" }).view_count, 112424515);
  assert.equal(answerMetrics({ ...answer(), platform: "bilibili" }).view_count, 112424515);
});

test("拆开跨平台组时保留问题级指标", () => {
  const zhihu = { ...answer(), question: { id: "1", title: "问题标题", url: "https://www.zhihu.com/question/1", metrics: { view_count: 112424515, answer_count: 200 } } };
  const xhs: UnifiedSearchResult = { ...answer(), platform: "xhs", content_type: "note", content_id: "x1" };
  const expanded = expandGroupedResults([{ ...xhs, grouped_sources: [xhs, zhihu] }]);
  assert.deepEqual(groupZhihuQuestions(expanded)[1].question, zhihu.question);
});

test("搜索响应里的独立问题对象不当作内容卡片", () => {
  const question: UnifiedSearchResult = {
    platform: "zhihu", content_type: "question", content_id: "2093997001205445814",
    title: "搜索命中的问题", url: "https://api.zhihu.com/questions/2093997001205445814",
    author: null, snippet: "问题摘要", published_at: null, cover_url: null, metrics: {}, rank: 0,
  };
  const mixed = { ...answer(), grouped_sources: [question, answer()] };
  const cards = groupZhihuQuestions([question, mixed]);
  assert.equal(cards.length, 1);
  assert.equal(cards[0].content_type, "question");
  assert.deepEqual(cards[0].question_answers?.map(row => row.content_id), ["42"]);
});

test("回答分页解码核对问题与游标，仅保留公开回答字段", () => {
  const raw = { question_id: "1", answers: [{ ...answer(), cookie: "private", content: "private body" }, answer("99", "2")], next_cursor: "a".repeat(32), exhausted: false, limit_reached: false };
  const page = decodeQuestionAnswers(raw, "1");
  assert.equal(page.answers.length, 1);
  assert.ok(!JSON.stringify(page).includes("private"));
  assert.throws(() => decodeQuestionAnswers(raw, "2"));
  assert.throws(() => decodeQuestionAnswers({ ...raw, next_cursor: "https://evil.test" }, "1"));
  assert.throws(() => decodeQuestionAnswers({ ...raw, exhausted: "false" }, "1"));
});
