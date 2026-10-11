import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import type { UnifiedSearchResult } from "@/types/search";
import { decodeQuestionAnswers, mergeAnswers, type QuestionAnswersPage } from "@/lib/zhihuQuestions";
import { readingError } from "@/lib/reading";

export function useZhihuAnswers(result: UnifiedSearchResult) {
  const question = result.question_answers ? result.question : null;
  const identity = question?.id;
  const title = question?.title ?? "";
  const url = question?.url ?? "";
  const [answers, setAnswers] = useState<UnifiedSearchResult[]>(() => identity ? mergeAnswers([], result.question_answers!, identity) : []);
  const [page, setPage] = useState<Omit<QuestionAnswersPage, "answers"> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const seed = useRef(result.question_answers);
  seed.current = result.question_answers;

  const load = useCallback(async (cursor: string | null) => {
    if (!identity || controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setLoading(true);
    setError("");
    try {
      const { data } = await axios.post("/api/reading/zhihu/question/answers", { question_id: identity, title, url, cursor },
        { signal: request.signal, timeout: 30000 });
      if (request.signal.aborted) return;
      const next = decodeQuestionAnswers(data, identity);
      setAnswers(known => mergeAnswers(known, next.answers, identity));
      setPage(next);
    } catch (failure) {
      if (!request.signal.aborted) setError(readingError(failure).replace(/正文/g, "回答"));
    } finally {
      if (controller.current === request) controller.current = null;
      if (!request.signal.aborted) setLoading(false);
    }
  }, [identity, title, url]);

  useEffect(() => {
    setAnswers(identity ? mergeAnswers([], seed.current ?? [], identity) : []);
    setPage(null);
    setError("");
    if (identity) void load(null);
    return () => { controller.current?.abort(); controller.current = null; };
  }, [identity, load]);
  useEffect(() => {
    if (identity) setAnswers(known => mergeAnswers(known, result.question_answers ?? [], identity));
  }, [identity, result.question_answers]);

  return { answers, page, loading, error, loadMore: () => load(page?.next_cursor ?? null) };
}
