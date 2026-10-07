"""No real platforms, models or user data: normalized ASR, cache and graceful fallback."""

import asyncio
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from api.services import research_materials as materials
from api.services import research_transcription as module


def source(platform="xhs"):
    return {"key": f"{platform}|one", "result": {"platform": platform, "content_id": "one", "title": "视频",
        "content_type": "video", "url": "https://example.com/video/one"}}


def transcript():
    return {"state": "ok", "entries": [{"start": 0, "end": 87, "text": "视频讲述"}], "text": "", "truncated": False,
        "reason": "", "metadata": {"source": "local_asr", "engine": "faster-whisper", "model": "small", "language": "zh", "duration": 87, "cached": False}}


def test_normalization_rejects_invalid_timestamps_and_bounds_output(monkeypatch):
    rows = [{"start": "1.25", "end": "2.5", "text": "  中文  "}, {"start": float("nan"), "end": 2, "text": "bad"},
        {"start": -1, "end": 2, "text": "bad"}, {"start": 5, "end": 2, "text": "bad"}, {"text": "bad"},
        {"start": 2.5, "end": 3, "text": "   "}]
    assert module.normalize_segments(rows) == ([{"start": 1.25, "end": 2.5, "text": "中文"}], False)
    monkeypatch.setattr(module, "MAX_ENTRIES", 1)
    assert module.normalize_segments(rows + rows)[1]
    assert module.normalize_segments([{"start": 0, "end": module.MAX_SECONDS + 1, "text": "long"}]) == ([], True)


@pytest.mark.asyncio
async def test_cache_reuses_without_media_or_model_and_omits_sensitive_urls(monkeypatch, tmp_path):
    service = module.TranscriptionService(tmp_path)
    calls = []
    monkeypatch.setattr(module, "asr_python", lambda: "optional-python")
    async def download(url, target, referer):
        calls.append("download")
        target.write_bytes(b"media")
    async def run(executable, media, models, progress):
        assert media.read_bytes() == b"media"
        calls.append("asr")
        progress("正在本地 AI 转写")
        return {"entries": transcript()["entries"], "language": "zh", "duration": 87}
    monkeypatch.setattr(module, "download_media", download)
    monkeypatch.setattr(module, "run_asr", run)
    first = await service.transcribe("xhs|one", "https://cdn.example/audio?token=secret", "https://example.com")
    assert first["metadata"]["source"] == "local_asr" and not first["metadata"]["cached"]
    assert calls == ["download", "asr"] and not list(tmp_path.glob("audio-*"))
    monkeypatch.setattr(module, "asr_python", lambda: None)
    second = await service.transcribe("xhs|one", "https://expired.example", "https://example.com")
    assert second["metadata"]["cached"] and calls == ["download", "asr"]
    stored = service.cache_path("xhs|one").read_text(encoding="utf-8")
    assert "secret" not in stored and "cdn.example" not in stored
    assert service.cached("xhs|two") is None


def test_cache_expiration_corruption_and_capacity(monkeypatch, tmp_path):
    service = module.TranscriptionService(tmp_path)
    service.save("old", transcript())
    path = service.cache_path("old")
    os.utime(path, (0, 0))
    assert service.cached("old") is None
    service.save("broken", transcript())
    service.cache_path("broken").write_text("invalid", encoding="utf-8")
    assert service.cached("broken") is None
    monkeypatch.setattr(module, "CACHE_FILES", 2)
    for identity in range(5):
        service.save(str(identity), transcript())
    assert len(list(path.parent.glob("*.json"))) == 2
    assert service.cached("4")


@pytest.mark.asyncio
@pytest.mark.parametrize("error,state", [(PermissionError("secret-url"), "restricted"), (TimeoutError(), "failed"), (RuntimeError("secret-response"), "failed")])
async def test_failed_media_or_asr_cleans_audio_and_never_caches(monkeypatch, tmp_path, error, state):
    service = module.TranscriptionService(tmp_path)
    monkeypatch.setattr(module, "asr_python", lambda: "python")
    async def fail(url, target, referer):
        target.write_bytes(b"partial")
        raise error
    monkeypatch.setattr(module, "download_media", fail)
    result = await service.transcribe("video", "https://example.com", "https://example.com")
    assert result["state"] == state and "secret" not in result["reason"]
    assert not list(tmp_path.glob("audio-*")) and service.cached("video") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,error,state,reason", [
    ("media", PermissionError("secret-token"), "restricted", "CDN"),
    ("media", TimeoutError(), "failed", "音轨获取超时"),
    ("media", ValueError("媒体超过 96 MB 转写上限"), "failed", "96 MB"),
    ("media", RuntimeError("secret-url"), "failed", "媒体地址不可用"),
    ("asr", TimeoutError(), "failed", "worker 超时"),
    ("asr", ValueError("音频超过 30 分钟转写上限"), "failed", "30 分钟"),
    ("asr", ValueError("转写模型准备失败，请检查网络或可选组件"), "failed", "模型准备失败"),
    ("asr", None, "missing", "未识别到可读语音"),
])
async def test_asr_failure_reasons_distinguish_stages_without_sensitive_response(monkeypatch, tmp_path, stage, error, state, reason):
    monkeypatch.setattr(module, "asr_python", lambda: "python")
    async def download(url, target, referer):
        target.write_bytes(b"media")
        if stage == "media":
            raise error
    async def run(*args):
        if error:
            raise error
        return {"entries": []}
    monkeypatch.setattr(module, "download_media", download)
    monkeypatch.setattr(module, "run_asr", run)
    service = module.TranscriptionService(tmp_path)
    result = await service.transcribe("video", "https://example.com", "https://example.com")
    assert result["state"] == state and reason in result["reason"] and "secret" not in result["reason"]
    assert not list(tmp_path.glob("audio-*")) and service.cached("video") is None


@pytest.mark.asyncio
async def test_platform_audio_url_failure_returns_specific_gap(monkeypatch, tmp_path):
    collector = materials.MaterialCollector(workdir=tmp_path)
    collector.transcription = module.TranscriptionService(tmp_path)
    monkeypatch.setattr(materials, "asr_python", lambda: "python")
    async def fail(*args):
        raise RuntimeError("secret-cookie-and-url")
    monkeypatch.setattr(collector, "audio_resource", fail)
    result = await collector.transcribe_subtitles(None, source()["result"], {}, {})
    assert result["state"] == "failed" and "媒体资源地址未能取得" in result["reason"]
    assert "secret" not in result["reason"]


@pytest.mark.asyncio
async def test_cancelled_asr_keeps_cleanup_owned_by_parent(monkeypatch, tmp_path):
    service = module.TranscriptionService(tmp_path / "cache", temporary_root=tmp_path)
    monkeypatch.setattr(module, "asr_python", lambda: "python")
    entered = asyncio.Event()
    async def download(url, target, referer):
        target.write_bytes(b"media")
    async def run(*args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(module, "download_media", download)
    monkeypatch.setattr(module, "run_asr", run)
    task = asyncio.create_task(service.transcribe("video", "https://example.com", "https://example.com"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(tmp_path.glob("audio-*"))


@pytest.mark.asyncio
async def test_native_priority_and_asr_failure_preserve_other_components(monkeypatch, tmp_path):
    events = []
    collector = materials.MaterialCollector(emit=lambda event: events.append(copy.deepcopy(event)), workdir=tmp_path)
    collector.transcription = module.TranscriptionService(tmp_path)
    class Client:
        async def get_note_by_id(self, *args):
            return {"desc": "正文", "video": {"media": {"stream": {"h264": [{"master_url": "https://cdn.example/video"}]}}}}
    async def client(_):
        return Client()
    async def request(method, *args, **kwargs):
        return await method(*args, **kwargs)
    async def comments(*args):
        return materials.component("ok", entries=[{"id": "one", "text": "评论"}])
    async def native(*args):
        return materials.component("ok", entries=[{"start": 0, "end": 522, "text": "原生字幕"}])
    async def forbidden(*args):
        raise AssertionError("native success must skip ASR")
    monkeypatch.setattr(collector, "client", client)
    monkeypatch.setattr(collector, "request", request)
    monkeypatch.setattr(collector, "comments", comments)
    monkeypatch.setattr(collector, "subtitles", native)
    monkeypatch.setattr(collector, "transcribe_subtitles", forbidden)
    row = await collector.collect(source())
    assert row["subtitles"]["metadata"] == {"source": "native", "duration": 522}
    async def missing(*args):
        return materials.component("missing", reason="平台无字幕")
    async def failed(*args):
        assert any(event["type"] == "material" and event["material"]["body"]["text"] == "正文" for event in events)
        return materials.component("failed", reason="本地转写失败")
    monkeypatch.setattr(collector, "subtitles", missing)
    monkeypatch.setattr(collector, "transcribe_subtitles", failed)
    row = await collector.collect(source())
    assert row["body"]["text"] == "正文" and row["comments"]["entries"]
    assert row["subtitles"]["state"] == "failed" and "平台无字幕" in row["subtitles"]["reason"]
    previous = materials.material(source())
    previous["subtitles"] = materials.component("failed", entries=[{"start": 0, "end": 2, "text": "保留的部分原生字幕"}])
    previous["subtitles"]["metadata"] = {"source": "native", "duration": 2}
    monkeypatch.setattr(collector, "transcribe_subtitles", forbidden)
    retried = await collector.collect(source(), previous)
    assert retried["subtitles"]["entries"][0]["text"] == "保留的部分原生字幕"
    assert retried["subtitles"]["truncated"] and retried["subtitles"]["metadata"]["source"] == "native"


@pytest.mark.asyncio
async def test_media_adapters_reuse_detail_and_bilibili_client(monkeypatch):
    collector = materials.MaterialCollector()
    async def request(method, uri, params):
        assert uri == "/x/player/wbi/playurl" and params["fnval"] == 16
        return {"dash": {"audio": [{"bandwidth": 100, "baseUrl": "https://cdn.example/high"},
            {"bandwidth": 10, "base_url": "https://cdn.example/audio"}]}}
    monkeypatch.setattr(collector, "request", request)
    client = SimpleNamespace(get=object())
    assert await collector.audio_resource(client, source("bilibili")["result"], {"aid": 1, "cid": 2}) == "https://cdn.example/audio"
    assert await collector.audio_resource(client, source()["result"], {"video": {"media": {"stream": {"h264": [
        {"master_url": "https://cdn.example/video"}]}}}}) == "https://cdn.example/video"
    assert await collector.audio_resource(client, source("douyin")["result"], {"video": {"play_addr": {"url_list": [
        "https://cdn.example/speech"]}}, "music": {"play_url": "https://cdn.example/music"}}) == "https://cdn.example/speech"
    assert await collector.audio_resource(client, source("zhihu")["result"], {}) is None
    assert await collector.audio_resource(client, source("douyin")["result"], {"music": {"play_url": "https://cdn.example/music"}}) is None


@pytest.mark.asyncio
async def test_asr_unavailable_and_zhihu_limit_do_not_download(monkeypatch, tmp_path):
    collector = materials.MaterialCollector()
    collector.transcription = module.TranscriptionService(tmp_path)
    monkeypatch.setattr(materials, "asr_python", lambda: None)
    row = materials.material(source())
    result = await collector.transcribe_subtitles(None, source()["result"], {}, row)
    assert result["state"] == "missing" and "未安装" in result["reason"]
    monkeypatch.setattr(materials, "asr_python", lambda: "python")
    result = await collector.transcribe_subtitles(None, source("zhihu")["result"], SimpleNamespace(desc="正文"), row)
    assert result["state"] == "missing" and "稳定音频" in result["reason"]


@pytest.mark.asyncio
async def test_asr_cache_marks_bilibili_multipart_limit(monkeypatch, tmp_path):
    collector = materials.MaterialCollector()
    collector.transcription = module.TranscriptionService(tmp_path)
    collector.transcription.save("bilibili|one|primary", transcript())
    result = await collector.transcribe_subtitles(None, source("bilibili")["result"], {"pages": [{}, {}]}, {})
    assert result["metadata"]["cached"] and result["truncated"] and "主分集" in result["reason"]


@pytest.mark.asyncio
async def test_media_download_rechecks_redirects_limits_size_and_has_no_cookie(monkeypatch, tmp_path):
    original = httpx.AsyncClient
    calls = []
    async def target(url):
        calls.append(url)
        if "127.0.0.1" in url:
            raise ValueError("private")
        return url, "cdn.example"
    async def redirect(request):
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    monkeypatch.setattr(module, "public_target", target)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(redirect), **kwargs))
    with pytest.raises(ValueError, match="private"):
        await module.download_media("https://cdn.example/audio", tmp_path / "media", "https://example.com")
    assert len(calls) == 2
    monkeypatch.setattr(module, "MAX_MEDIA_BYTES", 5)
    async def large(request):
        return httpx.Response(200, content=b"123456")
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(large), **kwargs))
    with pytest.raises(ValueError, match="媒体超过"):
        await module.download_media("https://cdn.example/audio", tmp_path / "media", "https://example.com")


def test_worker_uses_cpu_int8_vad_lazy_model_and_auto_language(monkeypatch):
    spec = importlib.util.spec_from_file_location("asr_worker", Path(__file__).parents[1] / "scripts/research_asr_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    calls, events = [], []
    class Model:
        def __init__(self, name, **kwargs):
            calls.append(kwargs)
            assert name == "small" and kwargs["device"] == "cpu" and kwargs["compute_type"] == "int8"
            if kwargs.get("local_files_only"):
                raise RuntimeError("model not yet downloaded")
        def transcribe(self, audio, **kwargs):
            assert kwargs["vad_filter"] and kwargs["language"] is None
            return iter([SimpleNamespace(start=0, end=1, text=" 中文 ")]), SimpleNamespace(language="zh")
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(worker, "decode_bounded", lambda *args: [0] * 16000)
    monkeypatch.setattr(worker, "emit", events.append)
    result = worker.run({"media": "temporary", "models": "local", "model": "small", "max_seconds": 1800})
    assert result["entries"] == [{"start": 0, "end": 1, "text": "中文"}]
    assert result["duration"] == 1 and len(calls) == 2
    assert [event["stage"] for event in events] == ["decode", "model", "download", "transcribe"]


def test_asr_worker_environment_never_inherits_keys_cookies_or_pythonpath(monkeypatch):
    for name in ("SIYE_RESEARCH_API_KEY", "ANTHROPIC_API_KEY", "COOKIE", "HF_TOKEN", "PYTHONPATH", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "secret")
    assert "secret" not in json.dumps(module.worker_environment())


def test_comment_metrics_are_kept_without_extra_requests():
    for platform, row in [("xhs", {"id": "1", "content": "观点", "like_count": "12", "sub_comment_count": 3}),
        ("douyin", {"cid": "1", "text": "观点", "digg_count": 12, "reply_comment_total": 3}),
        ("bilibili", {"rpid": "1", "content": {"message": "观点"}, "like": 12, "rcount": 3}),
        ("zhihu", {"id": "1", "content": "观点", "vote_count": 12, "child_comment_count": 3})]:
        result = materials.normalize_comments([row], platform)[0]
        assert result["like_count"] == 12 and result["reply_count"] == 3


@pytest.mark.asyncio
async def test_real_asr_subprocess_protocol_and_cleanup(monkeypatch, tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text('''import json, sys
payload = json.loads(sys.stdin.readline())
assert payload["language"] is None and payload["model"] == "small"
print(json.dumps({"type": "progress", "stage": "download", "secret": "ignored"}), flush=True)
print(json.dumps({"type": "done", "entries": [{"start": 0, "end": 1, "text": "中文语音"}], "language": "zh", "duration": 1}, ensure_ascii=False), flush=True)
''', encoding="utf-8")
    monkeypatch.setattr(module, "resource_path", lambda *args: worker)
    updates = []
    result = await module.run_asr(sys.executable, tmp_path / "media", tmp_path / "models", updates.append)
    assert result["entries"][0]["text"] == "中文语音" and updates == ["首次使用：正在下载 small 转写模型"]


@pytest.mark.asyncio
async def test_real_asr_subprocess_cancel_terminates_worker(monkeypatch, tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text('''import json, sys, time
sys.stdin.readline()
print(json.dumps({"type": "progress", "stage": "transcribe"}), flush=True)
time.sleep(60)
''', encoding="utf-8")
    original = asyncio.create_subprocess_exec
    processes = []
    async def spawn(*args, **kwargs):
        proc = await original(*args, **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(module, "resource_path", lambda *args: worker)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    entered = asyncio.Event()
    task = asyncio.create_task(module.run_asr(sys.executable, tmp_path / "media", tmp_path / "models", lambda _: entered.set()))
    await asyncio.wait_for(entered.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(processes) == 1 and processes[0].returncode is not None


def test_poll_summary_exposes_transcript_metadata_without_raw_evidence():
    from api.services.research_jobs import ResearchJobs
    manager = ResearchJobs()
    row = materials.material(source())
    row["body"] = materials.component("ok", text="private body")
    row["subtitles"] = transcript()
    row["subtitles"]["metadata"]["private_url"] = "secret"
    manager.jobs["test"] = {"materials": [row], "snapshot": {"items": [source()]}}
    summary = manager.public("test")
    assert summary["materials"][0]["subtitles"]["metadata"]["duration"] == 87
    assert summary["materials"][0]["subtitles"]["count"] == 1
    encoded = json.dumps(summary)
    assert "secret" not in encoded and "private body" not in encoded and "视频讲述" not in encoded


@pytest.mark.asyncio
async def test_missing_native_falls_back_to_normalized_asr_in_collector(monkeypatch, tmp_path):
    collector = materials.MaterialCollector(workdir=tmp_path)
    collector.transcription = module.TranscriptionService(tmp_path)
    class Client:
        async def get_note_by_id(self, *args):
            return {"desc": "正文", "video": {"media": {"stream": {"h264": [{"master_url": "https://cdn.example/video"}]}}}}
    async def client(_):
        return Client()
    async def request(method, *args, **kwargs):
        return await method(*args, **kwargs)
    async def comments(*args):
        return materials.component("ok", entries=[{"id": "c", "text": "评论"}])
    async def transcribe(identity, url, referer, progress):
        assert identity == "xhs|one|primary" and url == "https://cdn.example/video"
        progress("正在本地 AI 转写")
        return transcript()
    monkeypatch.setattr(collector, "client", client)
    monkeypatch.setattr(collector, "request", request)
    monkeypatch.setattr(collector, "comments", comments)
    monkeypatch.setattr(materials, "asr_python", lambda: "python")
    monkeypatch.setattr(collector.transcription, "transcribe", transcribe)
    result = await collector.collect(source())
    assert result["body"]["state"] == result["comments"]["state"] == result["subtitles"]["state"] == "ok"
    assert result["subtitles"]["metadata"]["source"] == "local_asr" and result["subtitles"]["entries"][0]["end"] == 87


@pytest.mark.asyncio
async def test_cancel_collection_marks_pending_transcription_failed_and_keeps_text(monkeypatch, tmp_path):
    from api.services import research_jobs as jobs
    config = SimpleNamespace(path=tmp_path / "research-ai.json")
    manager = jobs.ResearchJobs(config)
    manager.require_runtime = lambda: None
    monkeypatch.setattr(jobs, "get_session_snapshot", lambda _: {})
    monkeypatch.setattr(jobs, "ensure_session_snapshot", lambda _, **kwargs: asyncio.sleep(0, result={}))
    entered = asyncio.Event()
    async def process(job, payload, credentials=None):
        row = materials.material(source())
        row["body"] = materials.component("ok", text="正文保留")
        row["comments"] = materials.component("ok", entries=[{"id": "c", "text": "评论保留"}])
        row["subtitles"]["reason"] = "正在本地 AI 转写"
        job.update(materials=[row], transcribing_key=row["key"])
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(manager, "process", process)
    job = await manager.create({"id": 1, "name": "视频", "description": "", "archived": False, "items": [source()]}, "", False)
    await entered.wait()
    cancelled = await manager.cancel(job["job_id"])
    assert cancelled["status"] == "cancelled" and cancelled["materials"][0]["body"]["state"] == "ok"
    assert cancelled["materials"][0]["comments"]["count"] == 1
    assert cancelled["materials"][0]["subtitles"]["state"] == "failed"
    assert "已中断" in cancelled["materials"][0]["subtitles"]["reason"] and "transcribing_key" not in cancelled


def test_frozen_app_uses_optional_runtime_instead_of_bundle_dependencies(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "library_data_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert module.asr_python() is None
    executable = tmp_path / "research-asr/runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")
    assert module.asr_python() == str(executable)


@pytest.mark.asyncio
async def test_component_status_missing_runtime_and_broken_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "library_data_root", lambda: tmp_path)
    monkeypatch.setattr(module, "asr_python", lambda: None)
    assert (await module.asr_component_status())["reason"] == "not_installed"
    monkeypatch.setattr(module, "asr_python", lambda: str(tmp_path / "broken-python"))
    status = await module.asr_component_status()
    assert status["installed"] and not status["usable"] and status["reason"] == "runtime_unavailable"

@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_component_probe_is_offline_and_sanitized(monkeypatch, tmp_path, ready):
    worker = tmp_path / "status.py"
    worker.write_text("import json,sys\np=json.loads(sys.stdin.readline())\nassert p['mode']=='status' and set(p)=={'mode','models','model'}\nprint(json.dumps({'type':'done','engine_version':'1.2.1','model_ready':" + str(ready) + "}))\n")
    monkeypatch.setattr(module, "library_data_root", lambda: tmp_path)
    monkeypatch.setattr(module, "asr_python", lambda: sys.executable)
    monkeypatch.setattr(module, "resource_path", lambda *args: worker)
    monkeypatch.setenv("HF_TOKEN", "secret")
    status = await module.asr_component_status()
    assert status["usable"] == ready and status["model_ready"] == ready
    assert status["reason"] == ("ready" if ready else "model_not_ready")

def test_asr_runtime_detection_prefers_independent_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "library_data_root", lambda: tmp_path)
    runtime = tmp_path / "research-asr" / "runtime" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    runtime.parent.mkdir(parents=True)
    runtime.touch()
    assert module.asr_python() == str(runtime)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _: None)
    runtime.unlink()
    assert module.asr_python() is None

def test_model_ready_requires_files_and_never_downloads(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    worker_path = Path(__file__).parents[1] / "scripts" / "research_asr_worker.py"
    spec = importlib.util.spec_from_file_location("asr_status_worker", worker_path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    calls = []
    def cached_model(name, **kwargs):
        calls.append(kwargs)
        assert kwargs["local_files_only"] is True
        return str(tmp_path)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(__version__="1.2.1"))
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=cached_model))
    monkeypatch.setitem(sys.modules, "av", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    payload = {"model": "small", "models": str(tmp_path)}
    assert not worker.component_status(payload)["model_ready"]
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / name).write_bytes(b"test")
    assert worker.component_status(payload)["model_ready"]
    assert len(calls) == 2
