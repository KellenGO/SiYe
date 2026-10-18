"""Acquisition regressions; fake platforms and temporary profiles only."""

import asyncio
import json
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from tenacity import Future, RetryError

from api.services import accounts, research_materials as materials
from api.services.research_diagnostics import AcquisitionError
from api.services.research_platforms import ResearchBrowserProvider, browser_json
from tests.fixtures.browser import FakeBrowserContext, FakePage, FakePlaywright, douyin_test_context


@pytest.fixture(autouse=True)
def isolated_profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "profile_dir_for", lambda platform: tmp_path / platform)
    monkeypatch.setattr(accounts, "_profile_locks", {})
    monkeypatch.setattr(accounts, "_session_snapshots", {})


@pytest.mark.asyncio
async def test_douyin_uses_account_profile_and_snapshot_despite_default_platform(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "PLATFORM", "xhs")
    profile = tmp_path / "dy_user_data_dir"
    profile.mkdir()
    monkeypatch.setattr(accounts, "profile_dir_for", lambda platform: profile)
    context = douyin_test_context(existing=[{"name": "LOGIN_STATUS", "value": "1"}])
    playwright = FakePlaywright()
    platforms = []
    async def launch(platform):
        platforms.append(platform)
        return playwright, context, "test"
    monkeypatch.setattr(accounts, "_launch_profile_context", launch)
    collector = materials.MaterialCollector({"douyin": {"sessionid": "memory-secret"}})
    try:
        client = await collector.douyin_client()
        assert platforms == ["douyin"] and client.playwright_page is context.page
        assert context.added == [{"name": "sessionid", "value": "memory-secret", "url": "https://www.douyin.com"}]
        assert config.PLATFORM == "xhs"
    finally:
        await collector.close()
    assert context.close_count == 1 and playwright.stop_count == 1


@pytest.mark.asyncio
async def test_missing_douyin_session_does_not_create_anonymous_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "profile_dir_for", lambda _: tmp_path / "missing")
    collector = materials.MaterialCollector()
    try:
        with pytest.raises(AcquisitionError) as error:
            await collector.douyin_client()
        assert error.value.safe_code == "login_required"
    finally:
        await collector.close()
    assert not (tmp_path / "missing").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,cookies", [
    ("zhihu", [{"name": "z_c0", "value": "login"}, {"name": "d_c0", "value": "sign"}]),
    ("bilibili", [{"name": "SESSDATA", "value": "login"}]),
])
async def test_restart_restores_platform_profile_to_memory_without_verifying_account(monkeypatch, tmp_path, platform, cookies):
    profile = tmp_path / platform
    profile.mkdir()
    monkeypatch.setattr(accounts, "profile_dir_for", lambda _: profile)
    monkeypatch.setattr(accounts, "_session_snapshots", {})
    monkeypatch.setattr(accounts, "_profile_locks", {})
    context = FakeBrowserContext(existing=cookies, strict_closed=True)
    async def launch(_):
        return FakePlaywright(), context, "test"
    monkeypatch.setattr(accounts, "_launch_profile_context", launch)
    result = await accounts.ensure_session_snapshot(platform)
    assert result == {row["name"]: row["value"] for row in cookies}
    assert context.closed and not context.pages
    assert accounts.get_session_snapshot(platform) == result


@pytest.mark.asyncio
async def test_zhihu_incomplete_snapshot_restores_signing_cookie_once(monkeypatch, tmp_path):
    profile = tmp_path / "zhihu"
    profile.mkdir()
    monkeypatch.setattr(accounts, "profile_dir_for", lambda _: profile)
    monkeypatch.setattr(accounts, "_session_snapshots", {"zhihu": {"z_c0": "login"}})
    monkeypatch.setattr(accounts, "_profile_locks", {})
    context = FakeBrowserContext(existing=[{"name": "z_c0", "value": "login"}], initialized_cookies=[
        {"name": "z_c0", "value": "login"}, {"name": "d_c0", "value": "browser-sign"}])
    calls = []
    async def launch(platform):
        calls.append(platform)
        return FakePlaywright(), context, "test"
    monkeypatch.setattr(accounts, "_launch_profile_context", launch)
    values = await asyncio.gather(*(accounts.ensure_session_snapshot("zhihu") for _ in range(3)))
    assert calls == ["zhihu"] and all(value["d_c0"] == "browser-sign" for value in values)
    assert len(context.pages) == 1 and len(context.pages[0].goto_urls) == 1


@pytest.mark.asyncio
async def test_xhs_fallback_once_and_later_failure_keeps_acquired_comments(monkeypatch):
    collector = materials.MaterialCollector()
    calls = []
    class Client:
        cookie_dict = {"a1": "a1-secret"}
        async def get(self, *args):
            calls.append("http")
            raise AcquisitionError("rate_limited", 461)
    async def browser(client, uri, params):
        calls.append("browser")
        if params["cursor"]:
            raise AcquisitionError("restricted", 403)
        return {"comments": [{"id": "1", "content": "已取得评论"}], "cursor": "next", "has_more": True}
    async def direct(method, *args, **kwargs):
        return await method(*args, **kwargs)
    monkeypatch.setattr(collector, "request", direct)
    monkeypatch.setattr(collector.browser_provider, "xhs_browser_request", browser)
    source = {"platform": "xhs", "content_id": "one", "url": "https://www.xiaohongshu.com/explore/one?xsec_token=secret"}
    result = await collector.comments(Client(), source, {})
    assert calls == ["http", "browser", "browser"]
    assert result["state"] == "restricted" and result["entries"][0]["text"] == "已取得评论" and result["truncated"]
    assert result["diagnostics"]["primary_http_status"] == 461
    assert result["diagnostics"]["http_status"] == 403
    assert "secret" not in json.dumps(result, ensure_ascii=False)
    await collector.close()


@pytest.mark.asyncio
async def test_xhs_rejected_browser_fallback_is_not_repeated(monkeypatch):
    calls = []
    provider = ResearchBrowserProvider(AsyncExitStack(), {})
    class Client:
        cookie_dict = {}
        async def get(self, *args):
            raise AcquisitionError("restricted", 403)
    async def fail(*args):
        calls.append("browser")
        raise AcquisitionError("restricted", 403)
    monkeypatch.setattr(provider, "xhs_browser_request", fail)
    for _ in range(2):
        with pytest.raises(AcquisitionError):
            await provider.xhs_comments(Client(), {})
    assert calls == ["browser"]


@pytest.mark.asyncio
async def test_browser_response_diagnostics_never_copy_secrets():
    page = FakePage(evaluate_result={"status": 403, "data": {"cookie": "secret"}})
    with pytest.raises(AcquisitionError) as error:
        await browser_json(page, "https://www.douyin.com/api?signature=secret", {})
    result = materials.failure(error.value, "douyin", "body", "detail_api")
    assert result["state"] == "restricted" and result["diagnostics"]["http_status"] == 403
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("error,code", [
    (PermissionError("cookie=secret"), "login_required"),
    (AcquisitionError("session_expired", 401), "session_expired"),
    (AcquisitionError("rate_limited", 429), "rate_limited"),
    (TimeoutError("https://secret"), "timeout"),
    (ValueError("secret response"), "malformed_response"),
    (RuntimeError("secret traceback"), "internal_failure"),
])
def test_safe_failure_codes(error, code):
    future = Future(1)
    future.set_exception(error)
    result = materials.failure(RetryError(future), "zhihu", "comments", "comment_api")
    assert result["diagnostics"]["safe_error_code"] == code and "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_zhihu_answer_identity_uses_question_and_answer_and_rejects_mismatch(monkeypatch):
    collector = materials.MaterialCollector()
    calls = []
    class Client:
        async def get_answer_info(self, question, answer):
            calls.append((question, answer))
            return SimpleNamespace(content_text="完整回答", desc="摘要")
    async def client(_):
        return Client()
    async def comments(*args):
        return materials.component("restricted", reason="评论受限")
    async def direct(method, *args, **kwargs):
        return await method(*args, **kwargs)
    monkeypatch.setattr(collector, "client", client)
    monkeypatch.setattr(collector, "comments", comments)
    monkeypatch.setattr(collector, "request", direct)
    source = {"key": "zhihu|456", "result": {"platform": "zhihu", "content_type": "answer", "content_id": "456", "title": "回答",
              "url": "https://www.zhihu.com/question/123/answer/456"}}
    result = await collector.collect(source)
    assert result["body"]["text"] == "完整回答" and result["comments"]["state"] == "restricted"
    assert calls == [("123", "456")]
    source["result"]["url"] = "https://www.zhihu.com/question/123/answer/999"
    result = await collector.collect(source)
    assert result["body"]["state"] == "failed" and calls == [("123", "456")]
    await collector.close()


@pytest.mark.asyncio
async def test_douyin_prefers_low_bitrate_existing_stream_without_background_music():
    collector = materials.MaterialCollector()
    detail = {"music": {"play_url": {"url_list": ["https://cdn.example/music"]}}, "video": {
        "play_addr": {"url_list": ["https://cdn.example/large"]},
        "bit_rate": [{"bit_rate": 1000, "play_addr": {"url_list": ["https://cdn.example/high"]}},
                     {"bit_rate": 200, "play_addr": {"url_list": ["https://cdn.example/low"]}}]}}
    assert await collector.audio_resource(None, {"platform": "douyin"}, detail) == "https://cdn.example/low"
    detail["video"]["bit_rate"].extend([
        {"bit_rate": 50, "format": "dash", "play_addr": {"url_list": ["https://cdn.example/video-only"]}},
        {"bit_rate": 100, "format": "mp4", "is_bytevc1": 1, "play_addr": {"url_list": ["https://cdn.example/hevc"]}}])
    assert await collector.audio_resource(None, {"platform": "douyin"}, detail) == "https://cdn.example/low"
    detail["video"]["audio"] = {"url_list": ["https://cdn.example/speech"]}
    assert await collector.audio_resource(None, {"platform": "douyin"}, detail) == "https://cdn.example/speech"
    await collector.close()


@pytest.mark.asyncio
async def test_session_restore_timeout_is_not_reported_as_missing_login():
    collector = materials.MaterialCollector(session_errors={"zhihu": {"code": "timeout", "http_status": None}})
    source = {"key": "zhihu|123", "result": {"platform": "zhihu", "content_type": "answer", "content_id": "123",
        "title": "回答", "url": "https://www.zhihu.com/question/1/answer/123"}}
    result = await collector.collect(source)
    assert result["body"]["diagnostics"]["safe_error_code"] == "timeout"
    assert result["body"]["state"] == "failed" and "超时" in result["body"]["reason"]
    await collector.close()


@pytest.mark.asyncio
async def test_failed_browser_preparation_releases_profile_and_does_not_retry(monkeypatch, tmp_path):
    profile = tmp_path / "xhs"
    profile.mkdir()
    page = FakePage()
    async def denied(*args, **kwargs):
        return SimpleNamespace(status=403)
    page.goto = denied
    context = FakeBrowserContext(page=page)
    calls = []
    async def launch(platform):
        calls.append(platform)
        return FakePlaywright(), context, "test"
    monkeypatch.setattr(accounts, "_launch_profile_context", launch)
    stack = AsyncExitStack()
    provider = ResearchBrowserProvider(stack, {})
    for _ in range(2):
        with pytest.raises(AcquisitionError) as error:
            await provider.page("xhs", object())
        assert error.value.http_status == 403
        assert context.closed and not accounts._profile_lock("xhs").locked()
    await stack.aclose()
    assert calls == ["xhs"] and context.close_count == 1


@pytest.mark.asyncio
async def test_expired_douyin_profile_is_closed_before_next_material(monkeypatch, tmp_path):
    (tmp_path / "douyin").mkdir()
    context = douyin_test_context(existing=[{"name": "LOGIN_STATUS", "value": "0"}])
    calls = []
    async def launch(platform):
        calls.append(platform)
        return FakePlaywright(), context, "test"
    monkeypatch.setattr(accounts, "_launch_profile_context", launch)
    collector = materials.MaterialCollector()
    for _ in range(2):
        with pytest.raises(AcquisitionError) as error:
            await collector.douyin_client()
        assert error.value.safe_code == "login_required"
        assert context.closed and not accounts._profile_lock("douyin").locked()
    await collector.close()
    assert calls == ["douyin"] and context.close_count == 1


@pytest.mark.asyncio
async def test_zhihu_search_fallback_requires_exact_identity_and_access_flags(monkeypatch):
    collector = materials.MaterialCollector()
    calls = []
    source = {"content_type": "answer", "content_id": "42", "title": "原问题", "snippet": "独特摘要",
              "url": "https://www.zhihu.com/question/1/answer/42"}
    class Client:
        default_headers = {}
        async def get(self, path, params):
            calls.append(params)
            assert path == "/api/v4/search_v3" and params["sort"] == ""
            obj = {"id": "99" if len(calls) == 1 else "42", "type": "answer", "question": {"id": "1"},
                   "content": "<p>原回答正文</p><script>秘密脚本</script>", "is_truncated": True}
            return {"data": [{"object": obj}]}
    async def direct(method, *args, **kwargs):
        return await method(*args, **kwargs)
    monkeypatch.setattr(collector, "request", direct)
    result = await collector.zhihu_search_body(Client(), source)
    assert result["text"] == "原回答正文" and result["truncated"]
    assert result["diagnostics"]["provider"] == "search_api"
    assert [p["q"] for p in calls] == ["原问题", "独特摘要"]
    assert await collector.zhihu_search_body(Client(), source) == result and len(calls) == 2
    collector.zhihu_bodies[("answer", "42")][1]["can_read"] = False
    with pytest.raises(AcquisitionError) as error:
        await collector.zhihu_search_body(Client(), source)
    assert error.value.safe_code == "restricted"
    source["url"] = "https://www.zhihu.com/question/2/answer/42"
    with pytest.raises(AcquisitionError) as error:
        await collector.zhihu_search_body(Client(), source)
    assert error.value.safe_code == "invalid_identity"
    await collector.close()


@pytest.mark.asyncio
async def test_zhihu_search_excerpt_never_becomes_body(monkeypatch):
    collector = materials.MaterialCollector()
    class Client:
        default_headers = {}
        async def get(self, *args):
            return {"data": [{"object": {"type": "answer", "id": "42", "excerpt": "只有摘要"}}]}
    async def direct(method, *args):
        return await method(*args)
    monkeypatch.setattr(collector, "request", direct)
    result = await collector.zhihu_search_body(Client(), {"content_type": "answer", "content_id": "42",
        "title": "问题", "url": "https://www.zhihu.com/answer/42"})
    assert result is None
    await collector.close()


@pytest.mark.asyncio
async def test_collector_recovers_restricted_zhihu_body_then_reuses_it(monkeypatch):
    collector = materials.MaterialCollector()
    requests = []
    class Client:
        default_headers = {}
        async def get_answer_info(self, *args):
            requests.append("detail")
            raise AcquisitionError("restricted", 403)
        async def get(self, *args):
            requests.append("search")
            return {"data": [{"object": {"type": "answer", "id": "42", "question": {"id": "1"}, "content": "<p>精确回答</p>"}}]}
    async def client(_):
        return Client()
    async def comments(*args):
        return materials.component("ok")
    async def direct(method, *args):
        return await method(*args)
    monkeypatch.setattr(collector, "client", client)
    monkeypatch.setattr(collector, "comments", comments)
    monkeypatch.setattr(collector, "request", direct)
    item = {"key": "zhihu|42", "result": {"platform": "zhihu", "content_type": "answer", "content_id": "42",
        "title": "问题", "url": "https://www.zhihu.com/question/1/answer/42"}}
    result = await collector.collect(item)
    assert result["body"]["text"] == "精确回答" and result["body"]["diagnostics"]["fallback_outcome"] == "succeeded"
    assert requests == ["detail", "search"]
    await collector.collect(item, result)
    assert requests == ["detail", "search"]
    await collector.close()


@pytest.mark.asyncio
async def test_douyin_collector_uses_official_page_detail_for_body_and_audio(monkeypatch):
    collector = materials.MaterialCollector()
    source = {"key": "douyin|42", "result": {"platform": "douyin", "content_type": "video", "content_id": "42",
        "title": "视频", "url": "https://www.douyin.com/video/42"}}
    calls = []
    client = object()
    async def get_client(platform):
        return client
    async def page_detail(actual_client, actual_source):
        assert actual_client is client and actual_source == source["result"]
        calls.append("page")
        return {"aweme_id": "42", "desc": "官方页面正文", "video": {"play_addr": {"url_list": ["https://cdn.example/speech"]}}}
    async def comments(*args):
        return materials.component("ok")
    async def subtitles(actual_client, actual_source, detail):
        assert await collector.audio_resource(actual_client, actual_source, detail) == "https://cdn.example/speech"
        return materials.component("ok", entries=[{"start": 0, "end": 1, "text": "字幕"}])
    monkeypatch.setattr(collector, "client", get_client)
    monkeypatch.setattr(collector.browser_provider, "douyin_detail", page_detail)
    monkeypatch.setattr(collector, "comments", comments)
    monkeypatch.setattr(collector, "subtitles", subtitles)
    result = await collector.collect(source)
    assert result["body"]["text"] == "官方页面正文" and calls == ["page"]
    assert result["subtitles"]["state"] == "ok"
    await collector.close()


@pytest.mark.asyncio
async def test_douyin_adapter_reuses_current_page_instead_of_opening_profile(monkeypatch):
    from api.services import reading_browser
    provider = ResearchBrowserProvider(AsyncExitStack(), {})
    page = FakePage()
    async def detail(snapshot, identity, kind, url, **kwargs):
        assert kwargs == {"page": page, "raw": True} and identity == "42"
        return {"aweme_id": "42"}
    monkeypatch.setattr(reading_browser, "douyin_page_detail", detail)
    result = await provider.douyin_detail(SimpleNamespace(playwright_page=page),
        {"content_type": "video", "content_id": "42", "url": "https://www.douyin.com/video/42"})
    assert result["aweme_id"] == "42"
