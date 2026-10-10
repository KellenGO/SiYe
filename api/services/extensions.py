"""Install and control the official, separately packaged SiYe AI extension."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from base.app_version import APP_VERSION
from base.runtime_paths import library_data_root

RELEASE_API = "https://api.github.com/repos/KellenGO/SiYe-AI/releases/latest"
ASSET_NAME = "SiYe-AI-Windows-x64.zip"
MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
API_VERSION = 1


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("扩展版本信息无效")
    return tuple(map(int, value.split(".")))


def read_manifest(root):
    path = root / "extension.json"
    if not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("扩展包缺少有效的安装信息")
    row = json.loads(path.read_text(encoding="utf-8"))
    if (row.get("id") != "siye-ai" or row.get("api_version") != API_VERSION
            or row.get("worker") != "SiYeAI.exe" or row.get("ui") != "assets/ui.js"):
        raise ValueError("此扩展与四野不兼容")
    version(row.get("version"))
    if not version(row.get("min_host_version")) <= version(APP_VERSION) < version(row.get("max_host_version")):
        raise ValueError("此扩展版本不支持当前四野，请更新软件或安装兼容版本")
    if not (root / "SiYeAI.exe").is_file() or not (root / "assets/ui.js").is_file():
        raise ValueError("扩展包缺少运行程序或界面")
    return row


def extract_archive(archive, target, digest):
    with open(archive, "rb") as source:
        measured = hashlib.file_digest(source, "sha256").hexdigest()
    if not re.fullmatch(r"[a-f0-9]{64}", digest or "") or measured != digest:
        raise ValueError("扩展包校验失败，请重新下载")
    total = 0
    seen = set()
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist()) > 6000:
            raise ValueError("扩展包包含过多文件")
        for item in bundle.infolist():
            name = item.orig_filename
            path = PurePosixPath(name)
            parts = tuple(name.rstrip("/").split("/"))
            if (not parts or path.is_absolute() or "\\" in name or ":" in name
                    or any(part in {"", ".", ".."} or part.endswith((".", " ")) or
                           re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                           for part in parts)
                    or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1):
                raise ValueError("扩展包包含不安全的文件路径")
            normalized = name.rstrip("/").casefold()
            if normalized in seen:
                raise ValueError("扩展包包含重复文件")
            seen.add(normalized)
            total += item.file_size
            if total > MAX_EXPANDED:
                raise ValueError("扩展包解压后过大")
            destination = target.joinpath(*parts)
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, destination.open("wb") as out:
                    shutil.copyfileobj(source, out)
    return read_manifest(target)


def move_directory(source, target):
    for delay in (0.1, 0.2, 0.4, None):
        try:
            return source.rename(target)
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


class Extensions:
    def __init__(self, root=None, release_api=RELEASE_API):
        self.root = Path(root) if root else library_data_root().parent / "extensions" / "siye-ai"
        self.release_api = release_api
        self.task = None
        self.phase = "idle"
        self.downloaded = 0
        self.total = None
        self.error = ""
        self.available = None
        self.lock = asyncio.Lock()

    def settings(self):
        try:
            return json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {"version": None, "enabled": False}

    def save(self, row):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / "state.tmp"
        temporary.write_text(json.dumps(row), encoding="utf-8")
        os.replace(temporary, self.root / "state.json")

    def installed_root(self):
        value = self.settings().get("version")
        if not value:
            return None
        version(value)
        return self.root / "versions" / value

    def status(self):
        row = self.settings()
        compatible = False
        installed = bool(row.get("version"))
        detail = ""
        if installed:
            try:
                read_manifest(self.installed_root())
                compatible = True
            except (ValueError, OSError, TypeError):
                detail = "扩展文件不完整或与当前软件不兼容，请重新安装"
        busy = self.phase in {"downloading", "installing", "stopping", "uninstalling"}
        return {"id": "siye-ai", "name": "AI 研究助手", "installed": installed,
                "enabled": bool(row.get("enabled") and compatible and not busy),
                "version": row.get("version"), "compatible": compatible,
                "phase": self.phase, "downloaded": self.downloaded, "total": self.total,
                "error": self.error or detail, "available": self.available}

    def require_enabled(self):
        if not self.status()["enabled"]:
            raise ValueError("请先在扩展管理中安装并开启 AI 研究助手")

    def command(self):
        self.require_enabled()
        return [str(self.installed_root() / "SiYeAI.exe")]

    async def catalog(self):
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.get(self.release_api, headers={"Accept": "application/vnd.github+json"})
                response.raise_for_status()
                if len(response.content) > 1024 * 1024:
                    raise ValueError("扩展发布信息过大")
                release = response.json()
            asset = next(row for row in release.get("assets", []) if row.get("name") == ASSET_NAME)
            digest = asset.get("digest", "").removeprefix("sha256:")
            download = asset["browser_download_url"]
            if (urlsplit(download).scheme != "https" or urlsplit(download).hostname != "github.com"
                    or not urlsplit(download).path.startswith("/KellenGO/SiYe-AI/releases/download/")
                    or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or not 0 < asset.get("size", 0) <= MAX_DOWNLOAD):
                raise ValueError("扩展发布信息无效")
            release_version = release["tag_name"].removeprefix("v")
            version(release_version)
            self.available = {"version": release_version, "size": asset["size"], "url": download, "sha256": digest}
            self.error = ""
        except (httpx.HTTPError, ValueError, KeyError, StopIteration, TypeError):
            self.available = None
            self.error = "暂时无法获取扩展，请检查网络或稍后重试"
        return self.status()

    async def start_install(self, stop):
        async with self.lock:
            if self.phase in {"downloading", "installing", "stopping", "uninstalling"}:
                raise ValueError("扩展操作正在进行")
            self.phase = "downloading"
            self.error = ""
            self.downloaded = 0
            self.total = None
            self.task = asyncio.create_task(self._install(stop))
        return self.status()

    async def _install(self, stop):
        scratch = None
        try:
            await self.catalog()
            if not self.available:
                raise ValueError(self.error)
            available = dict(self.available)
            self.total = available["size"]
            self.root.mkdir(parents=True, exist_ok=True)
            scratch = Path(tempfile.mkdtemp(prefix="install-", dir=self.root))
            archive = scratch / "download.zip"
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                url = available["url"]
                for attempt in range(6):
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"} or parsed.username or parsed.password:
                        raise ValueError("扩展下载地址无效")
                    async with client.stream("GET", url) as response:
                        if response.is_redirect:
                            from urllib.parse import urljoin
                            url = urljoin(url, response.headers["location"])
                            continue
                        response.raise_for_status()
                        with archive.open("wb") as output:
                            async for chunk in response.aiter_bytes():
                                self.downloaded += len(chunk)
                                if self.downloaded > MAX_DOWNLOAD or self.downloaded > self.total:
                                    raise ValueError("扩展下载大小异常")
                                output.write(chunk)
                        break
                else:
                    raise ValueError("扩展下载跳转过多")
            if self.downloaded != self.total:
                raise ValueError("扩展下载不完整，请重试")
            self.phase = "installing"
            # Extraction is bounded and runs off the event loop. Cancellation must
            # wait for it before cleaning the temporary files.
            extraction = asyncio.create_task(asyncio.to_thread(extract_archive, archive, scratch / "package", available["sha256"]))
            try:
                manifest = await asyncio.shield(extraction)
            except asyncio.CancelledError:
                await extraction
                raise
            if manifest["version"] != available["version"]:
                raise ValueError("扩展版本与发布信息不一致")
            await self.activate(scratch / "package", manifest, stop)
            self.phase = "idle"
            self.error = ""
        except asyncio.CancelledError:
            self.phase = "idle"
            self.error = ""
        except Exception as error:
            self.phase = "failed"
            self.error = str(error) if isinstance(error, ValueError) else "扩展安装失败，原有版本已保留，请重试"
        finally:
            if scratch and scratch.exists():
                shutil.rmtree(scratch)

    async def activate(self, package, manifest, stop):
        old = self.settings()
        await stop()
        target = self.root / "versions" / manifest["version"]
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.with_name(target.name + ".previous")
        if backup.exists():
            shutil.rmtree(backup)
        existed = target.exists()
        if existed:
            move_directory(target, backup)
        try:
            move_directory(package, target)
            self.save({"version": manifest["version"], "enabled": bool(old.get("enabled"))})
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            if existed:
                move_directory(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup)

    async def set_enabled(self, enabled, stop):
        async with self.lock:
            if self.phase in {"downloading", "installing", "stopping", "uninstalling"}:
                raise ValueError("扩展操作正在进行")
            row = self.settings()
            if enabled:
                root = self.installed_root()
                if not root:
                    raise ValueError("请先安装扩展")
                read_manifest(root)
            self.phase = "stopping"
            try:
                await stop()
                row["enabled"] = enabled
                self.save(row)
                self.error = ""
            finally:
                self.phase = "idle"
        return self.status()

    async def cancel_install(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await self.task
        return self.status()

    async def uninstall(self, stop, clear_data=False):
        async with self.lock:
            await self.cancel_install()
            self.phase = "uninstalling"
            try:
                await stop()
                previous = self.settings()
                self.save({**previous, "enabled": False})
                versions = self.root / "versions"
                if versions.exists():
                    shutil.rmtree(versions)
                self.save({"version": None, "enabled": False})
                if clear_data:
                    from .research_config import research_config
                    from .research_jobs import research_jobs
                    for path in (research_config.path, research_jobs.history_store.root):
                        if path.is_dir():
                            shutil.rmtree(path)
                        elif path.is_file():
                            path.unlink()
                    research_jobs.jobs.clear()
                    research_jobs.history_store.index = None
                self.error = ""
            except OSError:
                self.error = "扩展文件暂时无法移除，请关闭占用程序后重试"
                raise ValueError(self.error) from None
            finally:
                self.phase = "idle"
        return self.status()


extensions = Extensions()
