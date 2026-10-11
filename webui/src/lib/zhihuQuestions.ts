import type { UnifiedSearchResult, ZhihuQuestion } from "../types/search.js";
import { publicResult } from "./bookmarks.js";

export function questionOf(answer: UnifiedSearchResult): ZhihuQuestion | null {
  if (answer.platform !== "zhihu" || answer.content_type !== "answer") return null;
  try {
    const url = new URL(answer.url);
    const match = url.pathname.match(/^\/question\/([0-9]{1,30})\/answer\/([0-9]{1,30})\/?$/);
    if (url.protocol !== "https:" || url.port || url.username || url.password
      || !["zhihu.com", "www.zhihu.com"].includes(url.hostname) || !match || match[2] !== answer.content_id) return null;
    if (answer.question && answer.question.id !== match[1]) return null;
    const metrics: Record<string, number> = {};
    for (const key of ["view_count", "answer_count"]) {
      const value = answer.question?.metrics?.[key];
      if (typeof value === "number" && Number.isSafeInteger(value) && value > 0) metrics[key] = value;
    }
    return { id: match[1], title: answer.question?.title || answer.title,
      url: `https://www.zhihu.com/question/${match[1]}`, metrics };
  } catch { return null; }
}

export function answerMetrics(source: Pick<UnifiedSearchResult, "platform" | "content_type" | "metrics">): Record<string, number> {
  const metrics = { ...source.metrics };
  if (source.platform === "zhihu" && source.content_type === "answer") delete metrics.view_count;
  return metrics;
}

export function sortAnswers(answers: readonly UnifiedSearchResult[]): UnifiedSearchResult[] {
  return [...answers].sort((a, b) => (b.metrics.like_count ?? -1) - (a.metrics.like_count ?? -1));
}

export function mergeAnswers(known: readonly UnifiedSearchResult[], incoming: readonly UnifiedSearchResult[], questionId: string): UnifiedSearchResult[] {
  const merged = new Map<string, UnifiedSearchResult>();
  for (const answer of [...known, ...incoming]) {
    if (questionOf(answer)?.id === questionId) merged.set(answer.content_id, { ...answer, metrics: answerMetrics(answer), grouped_sources: null });
  }
  return sortAnswers([...merged.values()]).slice(0, 100);
}

/** Question cards are a view of raw answers, separate from cross-platform grouping. */
export function groupZhihuQuestions(results: readonly UnifiedSearchResult[]): UnifiedSearchResult[] {
  const output: UnifiedSearchResult[] = [];
  const questions = new Map<string, UnifiedSearchResult>();
  for (const result of results) {
    const sources = result.grouped_sources?.length ? result.grouped_sources : [result];
    const remaining: UnifiedSearchResult[] = [];
    for (const snapshot of sources) {
      const source: UnifiedSearchResult = { ...snapshot, grouped_sources: null };
      const question = questionOf(source);
      if (!question) { remaining.push(source); continue; }
      let card = questions.get(question.id);
      if (!card) {
        card = { ...source, content_id: `question:${question.id}`, content_type: "question", title: question.title,
          url: question.url, question, question_answers: [], author: null, published_at: null, cover_url: null,
          metrics: {}, grouped_sources: null };
        questions.set(question.id, card);
        output.push(card);
      }
      card.question!.metrics = { ...card.question!.metrics, ...question.metrics };
      card.rank = Math.min(card.rank, source.rank);
      card.question_answers = mergeAnswers(card.question_answers!, [source], question.id);
      const lead = card.question_answers[0];
      card.snippet = `${lead.author || "作者未提供"}：${lead.snippet || "查看回答"}`;
    }
    if (remaining.length) output.push(remaining.length > 1 ? { ...remaining[0], grouped_sources: remaining } : remaining[0]);
  }
  return output;
}

export interface QuestionAnswersPage {
  answers: UnifiedSearchResult[];
  next_cursor: string | null;
  exhausted: boolean;
  limit_reached: boolean;
}

export function decodeQuestionAnswers(raw: unknown, questionId: string): QuestionAnswersPage {
  const page = raw as Record<string, unknown> | null;
  if (!page || page.question_id !== questionId || !Array.isArray(page.answers)
    || typeof page.exhausted !== "boolean" || typeof page.limit_reached !== "boolean"
    || !(page.next_cursor === null || typeof page.next_cursor === "string" && /^[A-Za-z0-9_-]{32}$/.test(page.next_cursor))) throw new Error("回答列表响应无效");
  const answers = page.answers.slice(0, 100).flatMap((value): UnifiedSearchResult[] => {
    if (!value || typeof value !== "object" || typeof value.title !== "string" || typeof value.url !== "string"
      || typeof value.content_id !== "string" || !value.metrics || typeof value.metrics !== "object") return [];
    const question = questionOf(value);
    if (question?.id !== questionId) return [];
    try { return [{ ...publicResult(value), question }]; } catch { return []; }
  });
  return { answers, next_cursor: page.next_cursor as string | null, exhausted: page.exhausted, limit_reached: page.limit_reached };
}
