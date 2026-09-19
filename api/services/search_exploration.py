"""One bounded exploration session owned by the current search job."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from aggregate_search.models import PLATFORM_SLUGS, interleave_results, make_dedup_key
from aggregate_search.pagination import PageState

if TYPE_CHECKING:  # 只用于类型标注，避免与 job manager 形成循环导入
    from .search_job_manager import _ActiveJob


class Exploration:
    MAX_RESULTS = 100
    MAX_ROUNDS = 20

    def __init__(self, job):
        self.id = job.job_id
        self.keyword = job.keyword
        self.platforms = list(job.platforms)
        self.generations = dict(job.account_generations)
        self.states = {p: PageState() for p in self.platforms}
        self.results = {p: [] for p in self.platforms}
        # 各平台最近一次已知状态：换批只跑其中几个平台，但用户看到的是整轮结果，
        # 响应要把其它平台的既有状态一起带上（见 _ActiveJob.to_response）。
        self.platforms_state = {}
        self.owners = {}
        self.batches = []
        self.committed = set()

    def add_platform(self, job: "_ActiveJob", platform: str) -> bool:
        """把一个平台的进度并进本次探索会话（进度从零开始）。

        用于「单独获取某个平台」：这一轮已经搜过别的平台，用户只想再补一个平台的结果，
        不该为此重搜全部平台。返回 True 表示这次真的新增了。
        """
        if platform in self.platforms:
            return False
        self.platforms.append(platform)
        # 与前端一致的平台顺序，保证综合结果交错顺序稳定。
        order = {slug: index for index, slug in enumerate(PLATFORM_SLUGS)}
        self.platforms.sort(key=lambda slug: order.get(slug, len(order)))
        self.states[platform] = PageState()
        self.results[platform] = []
        self.generations[platform] = job.account_generations[platform]
        return True

    def remaining(self, platform):
        return max(0, self.MAX_RESULTS - len(self.results[platform]))

    def more(self, platform):
        state = self.states[platform]
        return (len(self.batches) < self.MAX_ROUNDS and self.remaining(platform) > 0
                and (bool(state.pending) or not state.exhausted))

    def preview(self, job):
        combined = {p: self.results[p] + job.platform_results.get(p, []) for p in self.platforms}
        grouped = interleave_results(combined, platform_order=self.platforms)
        return [r for r in grouped if not any(
            make_dedup_key(s.platform, s.content_id) in self.owners for s in (r.grouped_sources or [r]))]

    def commit(self, job):
        if job.job_id in self.committed:
            return
        self.committed.add(job.job_id)
        number = len(self.batches) + 1
        added = 0
        for p in job.platforms:
            # Only checkpoints received from this worker advance pagination.
            if p in job.page_checkpoints:
                self.states[p] = job.page_states[p].model_copy(deep=True)
            for result in job.platform_results[p]:
                key = make_dedup_key(p, result.content_id)
                if key in self.owners or not self.remaining(p):
                    continue
                self.owners[key] = number
                self.results[p].append(result.model_copy(deep=True))
                added += 1
        grouped = interleave_results(self.results, platform_order=self.platforms)
        snapshot = {"job_id": job.job_id, "overall": job._compute_overall(),
                    "completed_at": job.completed_at,
                    "platforms": deepcopy(job.platforms_state)}
        batches = [{**batch, "results": []} for batch in self.batches]
        batches.append({"number": number, "results": [], **snapshot})
        for result in grouped:
            sources = result.grouped_sources or [result]
            owner = min(self.owners[make_dedup_key(s.platform, s.content_id)] for s in sources)
            batches[owner - 1]["results"].append(result)
        self.batches = batches
        job.exploration_info = self.info(job, added)
        # 记住这一批各平台的状态，供后续换批响应拼出完整的平台状态条。
        self.platforms_state.update(deepcopy(job.platforms_state))
        # New versions of old content update its original batch, not the new list.
        job._final_results = batches[-1]["results"]

    def info(self, job, added):
        return {"id": self.id, "round": len(self.batches), "max_per_platform": self.MAX_RESULTS,
                "new_sources": added, "new_contents": len(self.batches[-1]["results"]),
                "page_requests": sum(t.page_requests for t in job.timings.values()),
                "duplicates": sum(t.duplicate_count for t in job.timings.values()),
                "platforms": {p: {"collected": len(self.results[p]), "has_more": self.more(p)} for p in self.platforms},
                "previous_batches": deepcopy(self.batches[:-1])}
