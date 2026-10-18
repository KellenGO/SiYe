# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
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
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""
Unit tests for platform adapters.

All test data is local fixture — no live platform API calls.
"""

import pytest
from aggregate_search.adapters import (
    XhsAdapter,
    DouyinAdapter,
    BilibiliAdapter,
    ZhihuAdapter,
)


# ── Xiaohongshu fixtures ───────────────────────────────────────────────

XHS_NOTE_FIXTURE = {
    "note_id": "abc123def",
    "title": "露营装备推荐｜新手入门必备清单",
    "desc": "整理了20件露营必备装备...",
    "type": "normal",
    "time": 1736937000,  # 2025-01-15
    "user": {
        "nickname": "户外小白",
        "user_id": "user_001",
        "avatar": "https://example.com/avatar.jpg",
    },
    "interact_info": {
        "liked_count": "2300",
        "collected_count": "1800",
        "comment_count": "156",
        "share_count": "89",
    },
    "image_list": [
        {"url_default": "https://ci.xiaohongshu.com/abc123.jpg"},
    ],
    "tag_list": [{"name": "露营"}, {"name": "装备"}],
}

XHS_VIDEO_FIXTURE = {
    "note_id": "video001",
    "title": "露营Vlog｜山间清晨",
    "type": "video",
    "time": 1737023400,
    "user": {"nickname": "旅行达人"},
    "interact_info": {
        "liked_count": "5000",
        "collected_count": "3200",
        "comment_count": "450",
        "share_count": "200",
    },
    "video": {
        "image": {
            "url_default": "https://ci.xiaohongshu.com/video_cover.jpg",
        }
    },
}

XHS_NO_COVER_FIXTURE = {
    "note_id": "nocover001",
    "title": "纯文字笔记",
    "type": "normal",
    "time": 1736937000,
    "user": {"nickname": "文字控"},
    "interact_info": {},
    "image_list": [],
}


class TestXhsAdapter:
    def test_basic_note(self):
        adapter = XhsAdapter()
        results = adapter.adapt([XHS_NOTE_FIXTURE])
        assert len(results) == 1
        r = results[0]
        assert r.platform == "xhs"
        assert r.content_id == "abc123def"
        assert r.content_type == "note"
        assert r.title == "露营装备推荐｜新手入门必备清单"
        assert r.snippet == "整理了20件露营必备装备..."
        assert r.author == "户外小白"
        assert r.url == "https://www.xiaohongshu.com/explore/abc123def"
        assert r.published_at is not None
        assert "2025-01-15" in r.published_at
        assert r.cover_url == "https://ci.xiaohongshu.com/abc123.jpg"
        assert r.metrics["like_count"] == 2300
        assert r.metrics["collect_count"] == 1800
        assert r.metrics["comment_count"] == 156
        assert r.metrics["share_count"] == 89

    def test_video_type(self):
        adapter = XhsAdapter()
        results = adapter.adapt([XHS_VIDEO_FIXTURE])
        assert len(results) == 1
        assert results[0].content_type == "video"
        assert results[0].cover_url == "https://ci.xiaohongshu.com/video_cover.jpg"

    def test_no_cover(self):
        adapter = XhsAdapter()
        results = adapter.adapt([XHS_NO_COVER_FIXTURE])
        assert len(results) == 1
        assert results[0].cover_url is None

    def test_empty_list(self):
        adapter = XhsAdapter()
        assert adapter.adapt([]) == []

    def test_missing_note_id(self):
        adapter = XhsAdapter()
        results = adapter.adapt([{"title": "No ID"}])
        assert results == []

    def test_rank_is_sequential(self):
        adapter = XhsAdapter()
        results = adapter.adapt([XHS_NOTE_FIXTURE, XHS_VIDEO_FIXTURE])
        assert results[0].rank == 0
        assert results[1].rank == 1

    def test_author_privacy(self):
        """Verify author is only public nickname, no user_id leak."""
        adapter = XhsAdapter()
        results = adapter.adapt([XHS_NOTE_FIXTURE])
        r = results[0]
        assert r.author == "户外小白"
        assert "user_001" not in r.model_dump_json()


# ── Douyin fixtures ────────────────────────────────────────────────────

DOUYIN_AWEME_FIXTURE = {
    "aweme_id": "7123456789012345678",
    "desc": "露营帐篷搭建教程 ⛺️ #露营 #户外",
    "create_time": 1736937000,
    "author": {
        "nickname": "野营老王",
        "uid": "uid_123",
        "sec_uid": "sec_uid_456",
    },
    "statistics": {
        "digg_count": 15000,
        "collect_count": 8200,
        "comment_count": 1200,
        "share_count": 3400,
        "play_count": 250000,
    },
    "video": {
        "cover": {
            "url_list": [
                "https://p3.douyinpic.com/img/cover_low.jpg",
                "https://p3.douyinpic.com/img/cover_high.jpg",
            ],
        },
    },
    "share_url": "https://v.douyin.com/abc123/",
}

DOUYIN_DUP_FIXTURE = {
    "aweme_id": "7123456789012345678",  # Same as above
    "desc": "露营帐篷搭建教程 ⛺️ (重复)",
    "create_time": 1736937000,
    "author": {"nickname": "野营老王"},
    "statistics": {},
    "video": {},
}


class TestDouyinAdapter:
    def test_basic_aweme(self):
        adapter = DouyinAdapter()
        results = adapter.adapt([DOUYIN_AWEME_FIXTURE])
        assert len(results) == 1
        r = results[0]
        assert r.platform == "douyin"
        assert r.content_id == "7123456789012345678"
        assert r.content_type == "video"
        assert r.title == "露营帐篷搭建教程 ⛺️ #露营 #户外"
        assert r.snippet == "露营帐篷搭建教程 ⛺️ #露营 #户外"
        assert r.author == "野营老王"
        assert r.published_at is not None
        # Highest quality cover
        assert "cover_high.jpg" in r.cover_url
        assert r.metrics["like_count"] == 15000
        assert r.metrics["view_count"] == 250000

    def test_dedup(self):
        """Douyin adapter must de-duplicate within platform."""
        adapter = DouyinAdapter()
        results = adapter.adapt([DOUYIN_AWEME_FIXTURE, DOUYIN_DUP_FIXTURE])
        assert len(results) == 1

    def test_no_aweme_id(self):
        adapter = DouyinAdapter()
        results = adapter.adapt([{"desc": "No ID"}])
        assert results == []

    def test_missing_cover(self):
        adapter = DouyinAdapter()
        results = adapter.adapt([{"aweme_id": "1", "desc": "Test", "create_time": 1736937000}])
        assert results[0].cover_url is None

    def test_missing_snippet_fields_are_compatible(self):
        result = DouyinAdapter().adapt([{"aweme_id": "1"}])[0]
        assert result.snippet is None

    def test_share_url_is_never_used_for_navigation(self):
        """收藏列表的 share_url 指向 iesdouyin.com 且带跟踪参数 —— 必须忽略。

        用 share_url 会让前端域名白名单拒掉这个链接（卡片点不开），还会把
        u_code / share_sign 带进界面和导出。
        """
        result = DouyinAdapter().adapt([{
            "aweme_id": "7123456789012345678",
            "desc": "标题",
            "share_url": ("https://www.iesdouyin.com/share/video/7123456789012345678/"
                          "?u_code=abc&share_sign=xyz"),
        }])[0]
        assert result.url == "https://www.douyin.com/video/7123456789012345678"
        dump = result.model_dump_json()
        assert "iesdouyin" not in dump
        assert "u_code" not in dump
        assert "share_sign" not in dump

    def test_author_privacy(self):
        """Verify no uid/sec_uid in output."""
        adapter = DouyinAdapter()
        results = adapter.adapt([DOUYIN_AWEME_FIXTURE])
        r = results[0]
        assert r.author == "野营老王"
        assert "uid_123" not in r.model_dump_json()
        assert "sec_uid_456" not in r.model_dump_json()


# ── Bilibili fixtures ──────────────────────────────────────────────────

BILIBILI_VIDEO_FIXTURE = {
    "View": {
        "aid": 12345678,
        "bvid": "BV1xx411c7mD",
        "title": "【4K】川西露营｜雪山下的星空帐篷",
        "description": "记录雪山下搭建帐篷、做饭和看星空的全过程。",
        "pic": "https://i0.hdslb.com/bfs/archive/abc123.jpg",
        "pubdate": 1736937000,
        "owner": {
            "mid": 100001,
            "name": "旅行摄影师小李",
            "face": "https://i0.hdslb.com/bfs/face/face001.jpg",
        },
        "stat": {
            "view": 350000,
            "danmaku": 2800,
            "reply": 650,
            "favorite": 12000,
            "coin": 4500,
            "share": 1800,
            "like": 22000,
        },
    }
}

BILIBILI_FLAT_FIXTURE = {
    # Some API responses flatten the View fields
    "bvid": "BV1yy411c8nE",
    "aid": 87654321,
    "title": "露营美食｜户外烧烤全攻略",
    "pic": "https://i0.hdslb.com/bfs/archive/def456.jpg",
    "pubdate": 1737023400,
    "owner": {"name": "美食探险家"},
    "stat": {"view": 150000, "like": 8000, "danmaku": 1200, "coin": 2000},
}


class TestBilibiliAdapter:
    def test_basic_video(self):
        adapter = BilibiliAdapter()
        results = adapter.adapt([BILIBILI_VIDEO_FIXTURE])
        assert len(results) == 1
        r = results[0]
        assert r.platform == "bilibili"
        assert r.content_id == "BV1xx411c7mD"
        assert r.content_type == "video"
        assert r.title == "【4K】川西露营｜雪山下的星空帐篷"
        assert r.snippet == "记录雪山下搭建帐篷、做饭和看星空的全过程。"
        assert r.author == "旅行摄影师小李"
        assert r.url == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert r.published_at is not None
        assert r.cover_url == "https://i0.hdslb.com/bfs/archive/abc123.jpg"
        assert r.metrics["view_count"] == 350000
        assert r.metrics["danmaku_count"] == 2800
        assert r.metrics["like_count"] == 22000
        assert r.metrics["coin_count"] == 4500

    def test_flat_response(self):
        """Some Bilibili API paths return flattened dicts."""
        adapter = BilibiliAdapter()
        results = adapter.adapt([BILIBILI_FLAT_FIXTURE])
        assert len(results) == 1
        r = results[0]
        assert r.content_id == "BV1yy411c8nE"
        assert r.author == "美食探险家"

    def test_author_privacy(self):
        """Verify no mid/face in output."""
        adapter = BilibiliAdapter()
        results = adapter.adapt([BILIBILI_VIDEO_FIXTURE])
        r = results[0]
        assert r.author == "旅行摄影师小李"
        assert "mid" not in r.model_dump_json()
        assert "face" not in r.model_dump_json()


# ── Zhihu fixtures ─────────────────────────────────────────────────────

ZHIHU_ANSWER_FIXTURE = {
    "id": 9876543210,
    "type": "answer",
    "title": "新手露营需要准备哪些装备？",
    "question": {"id": 12345678, "title": "新手露营需要准备哪些装备？"},
    "author": {
        "id": "auth_001",
        "name": "户外装备控",
    },
    "created_time": 1736937000,
    "voteup_count": 3200,
    "comment_count": 180,
    "favorites_count": 640,
    "visits_count": 52000,
    "excerpt": "作为一个露营5年的老手，我来分享一些经验...",
    "thumbnail": "https://pic1.zhimg.com/80/thumb_abc.jpg",
}

ZHIHU_ARTICLE_FIXTURE = {
    "id": 11223344,
    "type": "article",
    "title": "露营装备选购指南（2025版）",
    "author": {
        "id": "auth_002",
        "name": "装备评测师",
    },
    "created_time": 1737023400,
    "voteup_count": 5600,
    "comment_count": 320,
    "excerpt": "本文将从帐篷、睡袋、炉具三大件入手...",
    "title_image": "https://pic3.zhimg.com/80/article_cover.jpg",
}

ZHIHU_ZVIDEO_FIXTURE = {
    "id": 55667788,
    "type": "zvideo",
    "title": "露营Vlog｜第一次野外露营全记录",
    "author": {
        "id": "auth_003",
        "name": "Vlogger小陈",
    },
    "created_at": 1737110000,
    "voteup_count": 1800,
    "comment_count": 95,
    "cover_url": "https://pic2.zhimg.com/80/video_cover.jpg",
    "video_url": "https://www.zhihu.com/zvideo/55667788",
}

ZHIHU_AUTHOR_MEMBER_WRAPPER = {
    "id": "ans_wrapper",
    "type": "answer",
    "title": "Wrapper Test",
    "question": {"id": 1},
    "author": {
        "member": {
            "id": "m001",
            "name": "包装昵称",
        }
    },
    "created_time": 1736937000,
    "voteup_count": 10,
    "comment_count": 2,
}


class TestZhihuAdapter:
    def test_answer(self):
        adapter = ZhihuAdapter()
        results = adapter.adapt([ZHIHU_ANSWER_FIXTURE])
        assert len(results) == 1
        r = results[0]
        assert r.platform == "zhihu"
        assert r.content_id == "9876543210"
        assert r.content_type == "answer"
        assert r.title == "新手露营需要准备哪些装备？"
        assert r.snippet == "作为一个露营5年的老手，我来分享一些经验..."
        assert r.author == "户外装备控"  # PUBLIC nickname, not masked!
        assert "question/12345678/answer/9876543210" in r.url
        assert r.published_at is not None
        assert r.cover_url == "https://pic1.zhimg.com/80/thumb_abc.jpg"
        assert r.metrics["like_count"] == 3200
        assert r.metrics["comment_count"] == 180
        # 回答浏览量口径不明；收藏仍走 favorites_count。
        assert "view_count" not in r.metrics
        assert r.metrics["collect_count"] == 640

    def test_title_html_highlight_is_cleaned(self):
        adapter = ZhihuAdapter()
        results = adapter.adapt([{
            "id": "html-title",
            "type": "answer",
            "title": "2026年（8月）1000元<em>以下人体工学椅</em>选购推荐",
            "question": {"id": 1},
            "author": {"name": "知乎用户"},
        }])
        assert results[0].title == "2026年（8月）1000元以下人体工学椅选购推荐"

    def test_article(self):
        adapter = ZhihuAdapter()
        results = adapter.adapt([ZHIHU_ARTICLE_FIXTURE])
        assert len(results) == 1
        r = results[0]
        assert r.content_type == "article"
        assert "zhuanlan.zhihu.com/p/11223344" in r.url
        assert r.author == "装备评测师"
        assert r.cover_url == "https://pic3.zhimg.com/80/article_cover.jpg"

    def test_zvideo(self):
        adapter = ZhihuAdapter()
        results = adapter.adapt([ZHIHU_ZVIDEO_FIXTURE])
        assert len(results) == 1
        r = results[0]
        assert r.content_type == "zvideo"
        assert r.url == "https://www.zhihu.com/zvideo/55667788"
        assert r.author == "Vlogger小陈"

    def test_question_and_unknown_search_objects_are_not_content(self):
        """search_v3 mixes question/person cards into data[].object; only content is adapted."""
        adapter = ZhihuAdapter()
        question = {
            "id": "2093997001205445814",
            "type": "question",
            "title": "搜索命中的问题",
            "url": "https://api.zhihu.com/questions/2093997001205445814",
            "comment_count": 60,
            "visits_count": 3583123,
        }
        unknown = {"id": "person-1", "type": "people", "title": "某用户"}
        results = adapter.adapt([question, unknown, ZHIHU_ANSWER_FIXTURE])
        assert [r.content_type for r in results] == ["answer"]
        assert results[0].content_id == "9876543210"

    def test_author_member_wrapper(self):
        """Zhihu sometimes wraps author in a 'member' sub-object."""
        adapter = ZhihuAdapter()
        results = adapter.adapt([ZHIHU_AUTHOR_MEMBER_WRAPPER])
        assert len(results) == 1
        assert results[0].author == "包装昵称"

    def test_public_nickname_not_masked(self):
        """Critical: zhihu adapter must return PUBLIC nickname, not masked.

        The existing ZhihuExtractor masks nicknames via mask_nickname().
        Our adapter bypasses that and reads author.name directly.
        """
        adapter = ZhihuAdapter()
        results = adapter.adapt([ZHIHU_ANSWER_FIXTURE])
        # "户外装备控" would be masked to something like "户***" by the extractor
        assert results[0].author == "户外装备控"
        assert len(results[0].author) > 3  # not truncated/masked

    def test_no_cover(self):
        adapter = ZhihuAdapter()
        results = adapter.adapt([{
            "id": "1",
            "type": "answer",
            "title": "No Cover Test",
            "question": {"id": 1},
            "author": {"name": "Test"},
            "created_time": 1736937000,
            "voteup_count": 0,
            "comment_count": 0,
        }])
        assert results[0].cover_url is None
        assert results[0].snippet is None

    def test_author_privacy_no_hash(self):
        """Author field should not contain hashed user IDs."""
        adapter = ZhihuAdapter()
        results = adapter.adapt([ZHIHU_ANSWER_FIXTURE])
        r = results[0]
        assert r.author == "户外装备控"
        assert "auth_001" not in r.model_dump_json()
