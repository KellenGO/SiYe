# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。

"""
小红书聚合搜索轻量列表模式测试。

直接调用真实 ``XiaoHongShuCrawler.search`` 与真实 ``XhsAdapter``：

1. 搜索只把轻量列表项交给 result_sink，不调用详情 API/任务；
2. 完整链路 core → result_sink → XhsAdapter.adapt → UnifiedSearchResult；
3. limit=1/5/10/20 精确裁剪、详情调用恒为 0；
4. rec/hot 过滤 + 跨页 source_index 连续 + 最终顺序与原始相关性一致；
5. 可选字段缺失（author/published_at/metrics/cover）照常输出；
6. 搜索分页、去重、limit 和可选字段处理保持稳定。

性能断言以"请求数量 + 固定等待消失"为准，不使用不稳定毫秒。
"""

import asyncio
import io


import pytest

import config
from aggregate_search.adapters.xhs import XhsAdapter
from aggregate_search.protocol import parse_event_line
from base.crawler_runtime import CrawlerRuntimeOptions
from media_platform.xhs.core import XiaoHongShuCrawler


# ── 真实形状的搜索列表项（外层 item + 内层 note_card）────────────────────

def _search_item(i: int, **overrides) -> dict:
    item = {
        "id": f"note{i:02d}",
        "model_type": "note",
        "xsec_token": f"xtok{i:02d}",
        "xsec_source": "pc_search",
        "note_card": {
            "note_id": f"note{i:02d}",
            "display_title": f"露营装备推荐第{i}期",
            "user": {"nickname": f"测试博主{i}", "user_id": f"u{i}"},
            # Round 17.1 真实确认结构：cover.url_default / url_pre +
            # image_list[].info_list[].url（请求带 image_formats 时返回）。
            "cover": {
                "url_default": f"https://sns-webpic.xhscdn.com/fake{i}.jpg",
                "url_pre": f"https://sns-webpic.xhscdn.com/pre{i}.jpg",
            },
            "image_list": [
                {"info_list": [
                    {"url": f"https://sns-webpic.xhscdn.com/img{i}.jpg"},
                ]},
            ],
            "interact_info": {
                "liked_count": str(1000 + i),
                "collected_count": str(100 + i),
                "comment_count": str(10 + i),
                "share_count": str(i),
            },
            "type": "normal",
            "time": 1736937000 + i * 86400,
        },
    }
    if overrides:
        item.update(overrides)
    return item


class _FakeXhsClient:
    """Fake XiaoHongShuClient：search 返回真实形状列表项；详情方法是触发线
    —— 轻量模式下被调用即失败。"""

    def __init__(self, pages, raise_on_detail=True):
        self.pages = list(pages)
        self.search_calls = 0
        self.detail_calls = 0
        self.raise_on_detail = raise_on_detail

    async def get_note_by_keyword(self, **kwargs):
        self.search_calls += 1
        if self.pages:
            return self.pages.pop(0)
        return {"items": [], "has_more": False}

    async def get_note_by_id(self, *a, **k):
        self.detail_calls += 1
        if self.raise_on_detail:
            raise AssertionError(
                "get_note_by_id must not be called in light-list mode")
        note_id = a[0]
        return {"note_id": note_id, "title": f"详情标题{note_id}",
                "type": "normal", "time": 1736937000,
                "user": {"nickname": "详情博主", "user_id": "u-d"},
                "interact_info": {"liked_count": "1"}}

def _configure_config(monkeypatch):
    monkeypatch.setattr(config, "KEYWORDS", "露营")
    monkeypatch.setattr(config, "START_PAGE", 1)
    monkeypatch.setattr(config, "CRAWLER_MAX_SLEEP_SEC", 2)
    monkeypatch.setattr(config, "CRAWLER_TYPE", "search")
    monkeypatch.setattr(config, "MAX_CONCURRENCY_NUM", 2)


def _make_crawler(fake_client, sink_list, limit, fetch_details=False,
                  stream_results=False):
    crawler = XiaoHongShuCrawler()
    crawler.xhs_client = fake_client
    crawler.runtime_options = CrawlerRuntimeOptions(
        result_sink=lambda items: sink_list.extend(items),
        login_policy="fail_fast",
        result_limit=limit,
        strict_errors=True,
        headless=True,
    )
    return crawler


def _recorder_sleep(sleeps):
    async def fake_sleep(seconds):
        sleeps.append(seconds)
    return fake_sleep


# ═══════════════════════════════════════════════════════════════════════
# 1. light 模式不调用详情
# ═══════════════════════════════════════════════════════════════════════

class TestLightModeNoDetailCalls:
    @pytest.mark.asyncio
    async def test_no_detail_api_no_tasks_no_sleep(self, monkeypatch):
        """搜索 1 次、详情 0 次、sink 恰好 10 条、
        第一页达 limit 时 asyncio.sleep 0 次。"""
        _configure_config(monkeypatch)
        sleeps = []
        monkeypatch.setattr(
            "media_platform.xhs.core.asyncio.sleep", _recorder_sleep(sleeps))
        pages = [{"items": [_search_item(i) for i in range(20)],
                  "has_more": True}]
        fake = _FakeXhsClient(pages)
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)

        await crawler.search()

        assert fake.search_calls == 1
        assert fake.detail_calls == 0, "轻量模式不得调用 get_note_by_id"
        assert len(sink) == 10, "sink 必须恰好 10 条"
        assert sleeps == [], "第一页已达 limit：不得有任何 sleep"
        # 每条都带聚合专用 source_index（原始搜索列表序号）。
        assert [s["source_index"] for s in sink] == list(range(10))

    @pytest.mark.asyncio
    async def test_empty_page_is_empty(self, monkeypatch):
        """搜索接口返回合法空列表 → 不 sink、不翻页、不抛异常。"""
        _configure_config(monkeypatch)
        fake = _FakeXhsClient([{"items": [], "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)

        await crawler.search()

        assert fake.search_calls == 1
        assert fake.detail_calls == 0
        assert sink == []


# ═══════════════════════════════════════════════════════════════════════
# 2. 完整 core → sink → adapter → DTO
# ═══════════════════════════════════════════════════════════════════════

class TestFullChainDTO:
    @pytest.mark.asyncio
    async def test_sink_to_adapter_to_dto(self, monkeypatch):
        _configure_config(monkeypatch)
        monkeypatch.setattr(
            "media_platform.xhs.core.asyncio.sleep", _recorder_sleep([]))
        fake = _FakeXhsClient(
            [{"items": [_search_item(i) for i in range(10)],
              "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)
        await crawler.search()

        results = XhsAdapter().adapt(sink, keyword="露营")
        assert len(results) == 10
        r = results[0]
        assert r.platform == "xhs"
        assert r.content_id == "note00"
        assert r.title == "露营装备推荐第0期"
        assert r.author == "测试博主0"
        assert r.content_type == "note"
        assert r.cover_url == "https://sns-webpic.xhscdn.com/fake0.jpg"
        assert r.metrics["like_count"] == 1000
        assert r.rank == 0
        assert r.published_at is not None
        # URL 用外层 id + xsec_token/xsec_source 安全构建。
        assert "xsec_token=xtok00" in r.url
        assert "xsec_source=pc_search" in r.url
        assert r.url.startswith("https://www.xiaohongshu.com/explore/note00")
        # token 只允许出现在最终跳转 URL，不得独立泄漏。
        dump = r.model_dump_json()
        assert dump.count("xtok00") == 1, "xsec_token 只能出现在 URL 中"

    @pytest.mark.asyncio
    async def test_no_published_at_when_missing(self, monkeypatch):
        """列表项没有可靠时间字段 → published_at=None（不为时间抓详情）。"""
        _configure_config(monkeypatch)
        item = _search_item(0)
        del item["note_card"]["time"]
        fake = _FakeXhsClient([{"items": [item], "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=1, fetch_details=False)
        await crawler.search()

        results = XhsAdapter().adapt(sink, keyword="露营")
        assert results[0].published_at is None


# ═══════════════════════════════════════════════════════════════════════
# 3. 自定义数量 limit=1/5/10/20
# ═══════════════════════════════════════════════════════════════════════

class TestCustomLimits:
    @pytest.mark.parametrize("limit", [1, 5, 10, 20])
    @pytest.mark.asyncio
    async def test_limit_exact(self, monkeypatch, limit):
        _configure_config(monkeypatch)
        monkeypatch.setattr(
            "media_platform.xhs.core.asyncio.sleep", _recorder_sleep([]))
        fake = _FakeXhsClient(
            [{"items": [_search_item(i) for i in range(25)],
              "has_more": True}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=limit, fetch_details=False)

        await crawler.search()

        assert len(sink) == limit, f"limit={limit} 必须精确输出 {limit} 条"
        assert fake.detail_calls == 0
        ids = [s["id"] for s in sink]
        assert ids == [f"note{i:02d}" for i in range(limit)], "按原始顺序裁剪"


# ═══════════════════════════════════════════════════════════════════════
# 4. 过滤与跨页排序
# ═══════════════════════════════════════════════════════════════════════

class TestFilterAndCrossPageOrdering:
    @pytest.mark.asyncio
    async def test_rec_hot_filtered_and_page2_ordered(self, monkeypatch):
        """第一页混入 rec/hot 导致有效不足 → 第二页；source_index 连续且
        按过滤前位置；最终顺序与原始有效搜索项一致；达 limit 停止翻页。"""
        _configure_config(monkeypatch)
        sleeps = []
        monkeypatch.setattr(
            "media_platform.xhs.core.asyncio.sleep", _recorder_sleep(sleeps))

        page1 = [_search_item(i) for i in range(20)]
        # 位置 2 与 7 插入推荐占位（占据原始位置）。
        page1[2] = {"id": "rec1", "model_type": "rec_query"}
        page1[7] = {"id": "rec2", "model_type": "hot_query"}
        page2 = [_search_item(i) for i in range(20, 30)]
        fake = _FakeXhsClient([
            {"items": page1, "has_more": True},
            {"items": page2, "has_more": False},
        ])
        sink = []
        crawler = _make_crawler(fake, sink, limit=20, fetch_details=False)

        await crawler.search()

        assert fake.search_calls == 2, "第一页不足时必须请求第二页"
        assert fake.detail_calls == 0
        assert len(sink) == 20, "达 limit 后停止翻页、不超量"
        # source_index 连续基于过滤前原始列表（占位位置 2、7 被跳过，
        # 其余 0,1,3,4,5,6,8..21 保持原始位置）。
        expected_indices = [i for i in range(22) if i not in (2, 7)]
        assert [s["source_index"] for s in sink] == expected_indices
        # 推荐占位被过滤：无 rec1/rec2。
        assert "rec1" not in [s.get("id") for s in sink]
        assert "rec2" not in [s.get("id") for s in sink]
        # 翻页间隔沿用一次 page sleep（remaining>0 且 has_more=True）。
        assert len(sleeps) == 1
        # 最终顺序与原始有效搜索项顺序一致（manager 按 rank 排序后）。
        results = XhsAdapter().adapt(sink, keyword="露营")
        results.sort(key=lambda r: r.rank)
        assert [r.content_id for r in results] == [
            f"note{i:02d}" for i in list(range(0, 2)) + list(range(3, 7))
            + list(range(8, 20)) + list(range(20, 22))
        ]

    @pytest.mark.asyncio
    async def test_no_duplicates_no_overflow(self, monkeypatch):
        _configure_config(monkeypatch)
        monkeypatch.setattr(
            "media_platform.xhs.core.asyncio.sleep", _recorder_sleep([]))
        fake = _FakeXhsClient(
            [{"items": [_search_item(i) for i in range(5)],
              "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)

        await crawler.search()

        assert len(sink) == 5, "只有 5 条就只输出 5 条（不重复、不超量）"
        assert fake.search_calls == 1


# ═══════════════════════════════════════════════════════════════════════
# 5. 可选字段缺失
# ═══════════════════════════════════════════════════════════════════════

class TestMissingOptionalFields:
    @pytest.mark.asyncio
    async def test_missing_author_time_metrics_cover(self, monkeypatch):
        """只要 content_id 有效就能生成结果；缺失字段安全为 null/空；标题
        使用安全回退；不调详情、不抛异常。"""
        _configure_config(monkeypatch)
        items = [
            _search_item(0),  # 完整
            {  # 缺 author / time / metrics / cover
                "id": "note99",
                "model_type": "note",
                "xsec_token": "xtok99",
                "xsec_source": "pc_search",
                "note_card": {"note_id": "note99"},
            },
            {  # 标题也缺 → 占位
                "id": "note98",
                "model_type": "note",
                "note_card": {},
            },
        ]
        fake = _FakeXhsClient([{"items": items, "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)
        await crawler.search()

        assert fake.detail_calls == 0
        assert len(sink) == 3
        results = XhsAdapter().adapt(sink, keyword="露营")
        assert len(results) == 3
        r_missing = results[1]
        assert r_missing.content_id == "note99"
        assert r_missing.author is None
        assert r_missing.published_at is None
        assert r_missing.metrics == {}
        assert r_missing.cover_url is None
        assert r_missing.title == "小红书笔记" or r_missing.title == ""
        r_placeholder = results[2]
        assert r_placeholder.title == "小红书笔记"
        assert r_placeholder.url.startswith(
            "https://www.xiaohongshu.com/explore/note98")

    @pytest.mark.asyncio
    async def test_item_without_content_id_skipped(self, monkeypatch):
        """缺 content_id 的项跳过，其余继续输出。"""
        _configure_config(monkeypatch)
        items = [
            {"model_type": "note", "note_card": {}},  # 无外层 id / note_id
            _search_item(0),
        ]
        fake = _FakeXhsClient([{"items": items, "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)
        await crawler.search()

        results = XhsAdapter().adapt(sink, keyword="露营")
        assert [r.content_id for r in results] == ["note00"]


# ═══════════════════════════════════════════════════════════════════════
# 5b. Round 17.1 封面（真实确认结构）与 ID 边界
# ═══════════════════════════════════════════════════════════════════════

class TestRealCoverStructure:
    @pytest.mark.asyncio
    async def test_real_cover_fields_produce_cover_url(self, monkeypatch):
        """真实确认结构（cover.url_default / url_pre / image_list[].info_list
        [].url）→ core → sink → adapter → cover_url 非空，detail=0。"""
        _configure_config(monkeypatch)
        monkeypatch.setattr(
            "media_platform.xhs.core.asyncio.sleep", _recorder_sleep([]))
        fake = _FakeXhsClient(
            [{"items": [_search_item(i) for i in range(3)],
              "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=3, fetch_details=False)
        await crawler.search()

        results = XhsAdapter().adapt(sink, keyword="露营")
        assert len(results) == 3
        for r in results:
            assert r.cover_url, "cover_url 必须非空"
            assert r.cover_url.startswith("https://")
        assert fake.detail_calls == 0


class TestCoverExtractionMatrix:
    """Adapter 封面提取矩阵（真实命中路径 url_default/url_pre/url + 兜底）。

    protocol-relative / 非法协议 / 空字段：按现状返回或为 None —— 不做
    额外归一化（真实接口带 image_formats 后返回完整 https URL）。"""

    def _adapt_one(self, note_card_fields):
        item = {"id": "n1", "model_type": "note",
                "note_card": {"note_id": "n1", **note_card_fields}}
        results = XhsAdapter().adapt([item], keyword="露营")
        return results[0] if results else None

    def test_cover_url_default(self):
        r = self._adapt_one(
            {"cover": {"url_default": "https://cdn.example.com/a.jpg"}})
        assert r.cover_url == "https://cdn.example.com/a.jpg"

    def test_cover_url_pre(self):
        r = self._adapt_one(
            {"cover": {"url_pre": "https://cdn.example.com/b.jpg"}})
        assert r.cover_url == "https://cdn.example.com/b.jpg"

    def test_cover_url_plain(self):
        r = self._adapt_one({"cover": {"url": "https://cdn.example.com/c.jpg"}})
        assert r.cover_url == "https://cdn.example.com/c.jpg"

    def test_cover_plain_string(self):
        r = self._adapt_one({"cover": "https://cdn.example.com/d.jpg"})
        assert r.cover_url == "https://cdn.example.com/d.jpg"

    def test_image_list_url_default(self):
        r = self._adapt_one(
            {"image_list": [{"url_default": "https://cdn.example.com/e.jpg"}]})
        assert r.cover_url == "https://cdn.example.com/e.jpg"

    def test_image_list_info_list_url(self):
        """真实命中路径之一：image_list[].info_list[].url。"""
        r = self._adapt_one(
            {"image_list": [{"info_list": [
                {"url": "https://cdn.example.com/f.jpg"}]}]})
        assert r.cover_url == "https://cdn.example.com/f.jpg"

    def test_protocol_relative_returned_as_is(self):
        """protocol-relative（//host/...）按现状返回（浏览器会按页面协议解析）。"""
        r = self._adapt_one({"cover": {"url": "//cdn.example.com/g.jpg"}})
        assert r.cover_url == "//cdn.example.com/g.jpg"

    def test_illegal_protocol_not_extracted_as_cover(self):
        r = self._adapt_one({"cover": "javascript:alert(1)"})
        assert r.cover_url is None or not r.cover_url.startswith("javascript")

    def test_empty_cover_is_none(self):
        r = self._adapt_one({})
        assert r.cover_url is None


class TestInvalidAndDuplicateIds:
    @pytest.mark.asyncio
    async def test_missing_ids_do_not_consume_limit(self, monkeypatch):
        """前 3 条缺 content_id，后面有足够合法项：limit=10 仍输出 10 条。"""
        _configure_config(monkeypatch)
        items = [{"model_type": "note", "note_card": {}} for _ in range(3)]
        items += [_search_item(i) for i in range(10)]
        fake = _FakeXhsClient([{"items": items, "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)
        await crawler.search()

        assert len(sink) == 10, "无效项不得消耗 limit"
        assert fake.detail_calls == 0
        # source_index 保留原始位置（0/1/2 无效被跳过 → 从 3 开始）。
        assert [s["source_index"] for s in sink] == list(range(3, 13))

    @pytest.mark.asyncio
    async def test_card_note_id_without_outer_id(self, monkeypatch):
        """外层无 id 但 note_card.note_id 存在 → 正常输出且 rank 正确。"""
        _configure_config(monkeypatch)
        items = [
            {"model_type": "note",
             "note_card": {"note_id": "card1", "display_title": "T"}},
            _search_item(1),
        ]
        fake = _FakeXhsClient([{"items": items, "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=5, fetch_details=False)
        await crawler.search()

        results = XhsAdapter().adapt(sink, keyword="露营")
        assert [r.content_id for r in results] == ["card1", "note01"]
        assert [r.rank for r in results] == [0, 1]
        assert fake.detail_calls == 0

    @pytest.mark.asyncio
    async def test_same_page_duplicate_ids_do_not_consume(self, monkeypatch):
        """同页重复 content_id：重复项不消耗 remaining，后续有效项补足。"""
        _configure_config(monkeypatch)
        items = [_search_item(0), _search_item(0),
                 _search_item(1), _search_item(2)]
        fake = _FakeXhsClient([{"items": items, "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)
        await crawler.search()

        ids = [s.get("id") for s in sink]
        assert ids == ["note00", "note01", "note02"], "重复项不得占用数量"
        assert len(ids) == len(set(ids))
        assert fake.detail_calls == 0

    @pytest.mark.asyncio
    async def test_cross_page_duplicate_ids_do_not_consume(self, monkeypatch):
        """跨页重复 content_id：只出现一次，后续有效项补足 limit。"""
        _configure_config(monkeypatch)
        page1 = [_search_item(i) for i in range(5)]
        page2 = [_search_item(0), _search_item(1),
                 _search_item(5), _search_item(6), _search_item(7)]
        fake = _FakeXhsClient([
            {"items": page1, "has_more": True},
            {"items": page2, "has_more": False},
        ])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)
        await crawler.search()

        ids = [s.get("id") for s in sink]
        assert ids == ["note00", "note01", "note02", "note03", "note04",
                       "note05", "note06", "note07"]
        assert len(ids) == len(set(ids)), "跨页重复项只出现一次"
        assert fake.search_calls == 2
        assert fake.detail_calls == 0

    @pytest.mark.asyncio
    async def test_comprehensive_mixed(self, monkeypatch):
        """rec/hot + 缺 ID + 重复 ID + card.note_id + 正常项混合：
        数量精确、顺序正确、detail=0。"""
        _configure_config(monkeypatch)
        items = [
            {"id": "rec1", "model_type": "rec_query"},               # 占位 0
            {"model_type": "note", "note_card": {}},                 # 缺 ID（1）
            _search_item(0),                                         # 2
            _search_item(0),                                         # 同页重复（3）
            {"model_type": "note",
             "note_card": {"note_id": "card9"}},                     # 4
            {"id": "hot1", "model_type": "hot_query"},               # 占位 5
            _search_item(1),                                         # 6
        ]
        fake = _FakeXhsClient([{"items": items, "has_more": False}])
        sink = []
        crawler = _make_crawler(fake, sink, limit=10, fetch_details=False)
        await crawler.search()

        results = XhsAdapter().adapt(sink, keyword="露营")
        assert [r.content_id for r in results] == ["note00", "card9", "note01"]
        assert [r.rank for r in results] == [2, 4, 6], \
            "source_index 必须对应第一次出现的原始位置"
        assert fake.detail_calls == 0


# ═══════════════════════════════════════════════════════════════════════


# ── Adapter：互动数据字段名 ─────────────────────────────────────────────

def test_adapter_reads_shared_count_from_search_shape():
    """搜索/收藏列表用 ``shared_count``，详情接口用 ``share_count``。

    旧实现只读 ``share_count``，导致搜索结果的分享数一直是 0。
    """
    adapter = XhsAdapter()
    search_item = {
        "id": "n1",
        "note_card": {
            "display_title": "标题",
            "interact_info": {"liked_count": "10", "shared_count": "7"},
        },
    }
    detail_item = {
        "note_id": "n2",
        "title": "详情",
        "interact_info": {"liked_count": "10", "share_count": "5"},
    }
    assert adapter.adapt([search_item])[0].metrics["share_count"] == 7
    assert adapter.adapt([detail_item])[0].metrics["share_count"] == 5


def test_adapter_extracts_full_metric_set_from_search_card():
    """搜索卡片必须同时给出点赞/评论/收藏/分享，缺一不可。"""
    item = {
        "id": "n3",
        "note_card": {
            "display_title": "标题",
            "type": "video",
            "interact_info": {
                "liked_count": "47039",
                "collected_count": "34361",
                "comment_count": "391",
                "shared_count": "1667",
            },
        },
    }
    metrics = XhsAdapter().adapt([item])[0].metrics
    assert metrics == {
        "like_count": 47039,
        "collect_count": 34361,
        "comment_count": 391,
        "share_count": 1667,
    }
    # 小红书不提供播放量，不得凭空补 0。
    assert "view_count" not in metrics


# ── Adapter：推荐词 / 热搜词占位项不是内容 ──────────────────────────────


def test_adapter_drops_recommendation_placeholders():
    """占位项自带 ``id``，只判 id 存在会把它们当笔记输出。

    聚合搜索的分页路径（``aggregate_search/pagination.py``）直接调
    ``adapter.adapt()``，绕过了 ``media_platform/xhs/core.py`` 里的
    ``model_type in ("rec_query", "hot_query")`` 过滤 —— 于是第一页会混进
    点开即 404 的假卡片，还挤掉真实名额。这里锁住适配器层的兜底。
    """
    placeholders = [
        {"id": "rec1", "model_type": "rec_query"},
        {"id": "hot1", "model_type": "hot_query"},
    ]
    assert XhsAdapter().adapt(placeholders, "露营") == []


def test_adapter_keeps_real_notes_next_to_placeholders():
    """混合输入时只丢占位项，正常笔记的 rank/顺序不受影响。"""
    items = [
        {"id": "rec1", "model_type": "rec_query"},   # 0 占位
        _search_item(0),                              # 1 正常
        {"id": "hot1", "model_type": "hot_query"},   # 2 占位
        _search_item(1),                              # 3 正常
    ]
    results = XhsAdapter().adapt(items, "露营")
    assert [r.content_id for r in results] == ["note00", "note01"]
