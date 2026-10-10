"""Extension installation, isolation, cancellation and retained-data regressions."""

import asyncio
import hashlib
import json
import stat
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.extensions import extensions_router, get_extensions
from api.routers.research import research_router
from api.services.extensions import Extensions, extract_archive, read_manifest

REAL_CLIENT = httpx.AsyncClient


def manifest(version="0.1.0", **changes):
    return {"id": "siye-ai", "version": version, "api_version": 1,
            "min_host_version": "1.2.0", "max_host_version": "2.0.0",
            "worker": "SiYeAI.exe", "ui": "assets/ui.js", **changes}


def package(root, row=None):
    root.mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "assets/ui.js").write_text("window.SiYeAI={apiVersion:1}")
    (root / "SiYeAI.exe").write_bytes(b"test fixture only")
    (root / "extension.json").write_text(json.dumps(row or manifest()))
    return root


def archive(root, extra=None, row=None):
    path = root / "test.zip"
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("extension.json", json.dumps(row or manifest()))
        output.writestr("SiYeAI.exe", b"fixture")
        output.writestr("assets/ui.js", "fixture")
        if extra:
            output.writestr(extra, "unsafe")
    if extra and chr(92) in extra:
        path.write_bytes(path.read_bytes().replace(extra.replace(chr(92), "/").encode(), extra.encode()))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


async def stopped():
    pass


def test_missing_extension_is_disabled_without_creating_user_data(tmp_path):
    manager = Extensions(tmp_path / "extensions")
    assert not manager.status()["installed"] and not manager.status()["enabled"]
    assert not manager.root.exists()
    with pytest.raises(ValueError, match="安装并开启"):
        manager.command()


@pytest.mark.parametrize("unsafe", ["../escape", "/escape", "C:/escape", "assets\\escape", "con.txt", "assets/name.", "assets/name "])
def test_archive_rejects_windows_traversal_and_ambiguous_names(tmp_path, unsafe):
    path, digest = archive(tmp_path, unsafe)
    with pytest.raises(ValueError, match="路径"):
        extract_archive(path, tmp_path / "output", digest)
    assert not (tmp_path / "escape").exists()


def test_archive_rejects_symlinks_and_duplicate_case_names(tmp_path):
    path, _ = archive(tmp_path)
    with zipfile.ZipFile(path, "a") as output:
        item = zipfile.ZipInfo("link")
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(item, "../escape")
    with pytest.raises(ValueError, match="路径"):
        extract_archive(path, tmp_path / "output", hashlib.sha256(path.read_bytes()).hexdigest())
    path, _ = archive(tmp_path, "ASSETS/UI.JS")
    with pytest.raises(ValueError, match="重复"):
        extract_archive(path, tmp_path / "another", hashlib.sha256(path.read_bytes()).hexdigest())


def test_archive_digest_and_compatibility_checks(tmp_path):
    path, digest = archive(tmp_path)
    with pytest.raises(ValueError, match="校验"):
        extract_archive(path, tmp_path / "bad", "0" * 64)
    assert extract_archive(path, tmp_path / "good", digest)["version"] == "0.1.0"
    for index, changes in enumerate(({"api_version": 2}, {"min_host_version": "9.0.0"}, {"worker": "../other.exe"})):
        path, digest = archive(tmp_path, row=manifest(**changes))
        with pytest.raises(ValueError):
            extract_archive(path, tmp_path / str(index), digest)


@pytest.mark.asyncio
async def test_first_install_is_disabled_enable_stops_tasks_and_survives_restart(tmp_path):
    manager = Extensions(tmp_path / "extensions")
    calls = []
    async def stop():
        calls.append("stop")
    await manager.activate(package(tmp_path / "staged"), manifest(), stop)
    assert manager.status()["installed"] and not manager.status()["enabled"]
    await manager.set_enabled(True, stop)
    assert Extensions(manager.root).status()["enabled"]
    assert manager.command() == [str(manager.root / "versions/0.1.0/SiYeAI.exe")]
    await manager.set_enabled(False, stop)
    assert calls == ["stop"] * 3
    with pytest.raises(ValueError):
        manager.command()


@pytest.mark.asyncio
async def test_disable_cancels_research_and_releases_active_job(tmp_path, monkeypatch):
    from api.services.research_config import ResearchConfig
    from api.services.research_jobs import ResearchJobs
    config = ResearchConfig(tmp_path / "ai.json", cipher=lambda text, decrypt=False: text)
    jobs = ResearchJobs(config)
    monkeypatch.setattr(jobs, "require_runtime", lambda: None)
    entered = asyncio.Event()
    async def process(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(jobs, "process", process)
    job = await jobs.create({"id": 1, "name": "fixture", "description": "", "archived": False, "items": [{"key": "douyin|1", "result": {"platform": "douyin", "title": "fixture", "url": "https://www.douyin.com/video/1"}}]}, "question", False)
    await asyncio.wait_for(entered.wait(), 2)
    manager = Extensions(tmp_path / "extension")
    await manager.activate(package(tmp_path / "staged"), manifest(), stopped)
    await manager.set_enabled(True, stopped)
    await manager.set_enabled(False, jobs.cleanup)
    assert jobs.active is None and jobs.get(job["job_id"])["status"] == "cancelled"
    assert not manager.status()["enabled"]


@pytest.mark.asyncio
async def test_failed_state_write_restores_previous_installation(tmp_path, monkeypatch):
    manager = Extensions(tmp_path / "extensions")
    await manager.activate(package(tmp_path / "first"), manifest(), stopped)
    original = (manager.installed_root() / "SiYeAI.exe").read_bytes()
    row = package(tmp_path / "replacement")
    (row / "SiYeAI.exe").write_bytes(b"replacement")
    def fail(*_):
        raise OSError("disk full")
    monkeypatch.setattr(manager, "save", fail)
    with pytest.raises(OSError):
        await manager.activate(row, manifest(), stopped)
    assert (manager.installed_root() / "SiYeAI.exe").read_bytes() == original


@pytest.mark.asyncio
async def test_uninstall_preserves_data_and_optional_clear_only_removes_ai_data(tmp_path, monkeypatch):
    from api.services import research_config as config_module, research_jobs as jobs_module
    config = config_module.ResearchConfig(tmp_path / "data/research-ai.json", cipher=lambda text, decrypt=False: text)
    config.save("https://example.com", "model", "fixture-key")
    jobs = jobs_module.ResearchJobs(config)
    jobs.history_store.root.mkdir()
    (jobs.history_store.root / "saved.json").write_text("{}")
    notes = tmp_path / "data/library.db"
    notes.write_bytes(b"retained notes")
    monkeypatch.setattr(config_module, "research_config", config)
    monkeypatch.setattr(jobs_module, "research_jobs", jobs)
    manager = Extensions(tmp_path / "extensions")
    await manager.activate(package(tmp_path / "first"), manifest(), stopped)
    await manager.uninstall(stopped)
    assert not manager.status()["installed"] and config.path.exists() and jobs.history_store.root.exists()
    await manager.activate(package(tmp_path / "second"), manifest(), stopped)
    await manager.uninstall(stopped, clear_data=True)
    assert not config.path.exists() and not jobs.history_store.root.exists()
    assert notes.read_bytes() == b"retained notes"


def mock_release(monkeypatch, data, *, digest=None, target=None, stream=None):
    from api.services import extensions as module
    original = REAL_CLIENT
    url = target or "https://github.com/KellenGO/SiYe-AI/releases/download/v0.1.0/SiYe-AI-Windows-x64.zip"
    async def respond(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"tag_name": "v0.1.0", "assets": [{"name": module.ASSET_NAME, "size": len(data), "digest": "sha256:" + (digest or hashlib.sha256(data).hexdigest()), "browser_download_url": url}]})
        return httpx.Response(200, stream=stream) if stream else httpx.Response(200, content=data)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))


@pytest.mark.asyncio
async def test_download_install_and_failed_update_keep_previous_version(tmp_path, monkeypatch):
    path, _ = archive(tmp_path)
    data = path.read_bytes()
    mock_release(monkeypatch, data)
    manager = Extensions(tmp_path / "extension")
    await manager.start_install(stopped)
    await manager.task
    assert manager.status()["installed"] and manager.downloaded == len(data)
    assert not list(manager.root.glob("install-*"))
    await manager.set_enabled(True, stopped)
    mock_release(monkeypatch, data, digest="0" * 64)
    await manager.start_install(stopped)
    await manager.task
    assert manager.status()["enabled"] and manager.status()["phase"] == "failed"
    assert "校验" in manager.error


@pytest.mark.asyncio
async def test_cancel_download_removes_partial_files(tmp_path, monkeypatch):
    entered = asyncio.Event()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            yield b"first"
            await asyncio.Event().wait()
    mock_release(monkeypatch, b"not a complete archive", stream=Stream())
    manager = Extensions(tmp_path / "extension")
    await manager.start_install(stopped)
    await asyncio.wait_for(entered.wait(), 2)
    await manager.cancel_install()
    assert manager.phase == "idle" and not manager.status()["installed"]
    assert not list(manager.root.glob("install-*"))


@pytest.mark.asyncio
async def test_catalog_rejects_non_official_downloads(tmp_path, monkeypatch):
    mock_release(monkeypatch, b"fixture", target="https://evil.example/plugin.zip")
    manager = Extensions(tmp_path / "extension")
    await manager.catalog()
    assert manager.available is None and manager.error


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_rate_limited_catalog_uses_official_release_manifest(tmp_path, monkeypatch, status):
    from api.services import extensions as module
    requests = []
    async def respond(request):
        requests.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(status, headers={"x-ratelimit-remaining": "0"})
        if request.url.host == "github.com":
            assert str(request.url) == module.RELEASE_MANIFEST
            return httpx.Response(302, headers={"location": "https://release-assets.githubusercontent.com/manifest.json"})
        return httpx.Response(200, json={"tag_name": "v0.1.0", "assets": [{
            "name": module.ASSET_NAME, "size": 128, "digest": "sha256:" + "a" * 64,
            "browser_download_url": "https://github.com/KellenGO/SiYe-AI/releases/download/v0.1.0/" + module.ASSET_NAME
        }]})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(respond), **kwargs))
    manager = Extensions(tmp_path / "extension")
    await manager.catalog()
    assert manager.available["sha256"] == "a" * 64 and not manager.error
    assert len(requests) == 3 and all("authorization" not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_release_manifest_rejects_external_redirect(tmp_path, monkeypatch):
    from api.services import extensions as module
    async def respond(request):
        if request.url.host == "api.github.com":
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
        return httpx.Response(302, headers={"location": "https://evil.example/manifest.json"})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(respond), **kwargs))
    manager = Extensions(tmp_path / "extension")
    await manager.catalog()
    assert manager.available is None and manager.error


def test_routes_reject_external_origins_and_research_when_disabled(tmp_path, monkeypatch):
    from api.services import extensions as module
    manager = Extensions(tmp_path / "extension")
    monkeypatch.setattr(module, "extensions", manager)
    app = FastAPI()
    app.include_router(extensions_router)
    app.include_router(research_router)
    app.dependency_overrides[get_extensions] = lambda: manager
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/extensions/ai").status_code == 200
        assert client.put("/api/extensions/ai/enabled", json={"enabled": True}).status_code == 409
        assert client.post("/api/research/connection-test", json={"web_enabled": False}).status_code == 409
        assert client.get("/api/extensions/ai/assets/ui.js").status_code == 409
        assert client.post("/api/extensions/ai/install", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/extensions/ai", headers={"Host": "evil.example", "Origin": "http://evil.example"}).status_code == 403


def test_broken_installation_never_becomes_enabled(tmp_path):
    manager = Extensions(tmp_path / "extension")
    manager.save({"version": "0.1.0", "enabled": True})
    assert manager.status()["installed"] and not manager.status()["enabled"]


@pytest.mark.asyncio
async def test_failed_uninstall_stays_visible_and_disabled_for_retry(tmp_path, monkeypatch):
    from api.services import extensions as module
    manager = Extensions(tmp_path / "extension")
    await manager.activate(package(tmp_path / "staged"), manifest(), stopped)
    await manager.set_enabled(True, stopped)
    def locked(*_):
        raise OSError("locked file")
    monkeypatch.setattr(module.shutil, "rmtree", locked)
    with pytest.raises(ValueError, match="无法移除"):
        await manager.uninstall(stopped)
    assert manager.status()["installed"] and not manager.status()["enabled"]
    assert manager.installed_root().exists()


@pytest.mark.asyncio
async def test_install_retries_transient_directory_lock(tmp_path, monkeypatch):
    from api.services import extensions as module
    manager = Extensions(tmp_path / "extensions")
    rename = Path.rename
    attempts = []
    def locked(source, target):
        if source.name == "staged":
            attempts.append(True)
            if len(attempts) < 3:
                raise PermissionError("temporary file lock")
        return rename(source, target)
    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    await manager.activate(package(tmp_path / "staged"), manifest(), stopped)
    assert len(attempts) == 3 and manager.status()["installed"]


@pytest.mark.asyncio
async def test_permanent_directory_lock_restores_existing_version(tmp_path, monkeypatch):
    from api.services import extensions as module
    manager = Extensions(tmp_path / "extensions")
    await manager.activate(package(tmp_path / "first"), manifest(), stopped)
    await manager.set_enabled(True, stopped)
    rename = Path.rename
    def locked(source, target):
        if source.name == "staged":
            raise PermissionError("permanent file lock")
        return rename(source, target)
    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        await manager.activate(package(tmp_path / "staged"), manifest(), stopped)
    assert manager.status()["enabled"]
    assert manager.installed_root().exists()
    assert not manager.installed_root().with_name("0.1.0.previous").exists()
