import { ArrowUpRight, Bookmark, Clock3, Layers3 } from "lucide-react";
import type { UnifiedSearchResult } from "@/types/search";
import { LocalContentCard } from "@/components/favorites/LocalContentCard";
import { highlightSegments } from "@/lib/resultTools";

export function ZhihuQuestionCard({ result, grid, query, onOpen, canSave, canAdd }: {
  result: UnifiedSearchResult; grid: boolean; query: string; onOpen: () => void; canSave: boolean; canAdd: boolean;
}) {
  const question = result.question!;
  const info = <p className="local-content-detail-meta">已获取 {result.question_answers!.length} 条回答
    {question.metrics.answer_count !== undefined && ` · 问题共 ${question.metrics.answer_count.toLocaleString("zh-CN")} 条回答`}
    {question.metrics.view_count !== undefined && ` · 问题浏览 ${question.metrics.view_count.toLocaleString("zh-CN")}`}</p>;
  const actions = <div className="content-card-actions">
    <span className="content-card-platform"><i className="pd" style={{ backgroundColor: "#0084ff" }} aria-hidden="true" />知乎 · 问题</span>
    <a href={question.url} target="_blank" rel="noreferrer" aria-label={`在知乎打开问题：${question.title}`}><ArrowUpRight aria-hidden="true" />原文</a>
    {canSave && <><button type="button" title="选择回答收藏" aria-label={`选择回答收藏 ${question.title}`} onClick={onOpen}><Bookmark aria-hidden="true" /></button>
      <button type="button" title="选择回答加入稍后再看" aria-label={`选择回答加入稍后再看 ${question.title}`} onClick={onOpen}><Clock3 aria-hidden="true" /></button></>}
    {canAdd && <button type="button" title="选择回答加入研究空间" aria-label={`选择回答加入研究空间 ${question.title}`} onClick={onOpen}><Layers3 aria-hidden="true" /></button>}
  </div>;
  if (grid) return <><LocalContentCard result={result} highlightQuery={query} onOpen={onOpen} />{info}{actions}</>;
  return <article className="result-row zhihu-question-card"><div className="result-content">
    <h2><button className="result-title result-reader-title" type="button" onClick={onOpen}>{highlightSegments(question.title, query).map((part, i) => part.matched ? <mark className="result-highlight" key={i}>{part.text}</mark> : part.text)}</button></h2>
    <p className="result-description">{result.snippet}</p>{info}{actions}
  </div></article>;
}
