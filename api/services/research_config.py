"""Local AI configuration; credentials never appear in public responses."""

import base64
import ctypes
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from base.runtime_paths import library_data_root, resource_path

SDK_VERSION = "0.2.163"
CLI_VERSION = "2.1.287"
DEFAULT_BASE_URL = "https://api.anthropic.com"


def protect_key(value: str, decrypt: bool = False) -> str:
    if os.name != "nt":
        raise ValueError("API Key 的本机加密保存目前仅支持 Windows")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

    raw = base64.b64decode(value, validate=True) if decrypt else value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("无法读取本机 API Key，请重新保存配置")
    try:
        decoded = ctypes.string_at(target.data, target.size)
        return decoded.decode("utf-8") if decrypt else base64.b64encode(decoded).decode("ascii")
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def agent_cli_path() -> Path:
    packaged = resource_path("agent_runtime", "claude.exe" if os.name == "nt" else "claude")
    if packaged.is_file():
        return packaged
    try:
        import claude_agent_sdk
        return Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if os.name == "nt" else "claude")
    except ImportError:
        return packaged


def runtime_status() -> dict:
    try:
        import claude_agent_sdk  # noqa: F401
        available = True
    except (ImportError, OSError):
        available = False
    return {"sdk_available": available, "cli_available": agent_cli_path().is_file(),
            "sdk_version": SDK_VERSION, "cli_version": CLI_VERSION}


class ResearchConfig:
    def __init__(self, path=None, cipher=protect_key):
        self.path = Path(path) if path else library_data_root() / "research-ai.json"
        self.cipher = cipher
        self.lock = threading.RLock()

    def _load(self):
        if not self.path.exists():
            return {"base_url": DEFAULT_BASE_URL, "model": "", "preferences": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("AI 配置无法读取，请检查本机配置文件") from None

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def public(self):
        with self.lock:
            data = self._load()
            return {"base_url": data["base_url"], "model": data["model"],
                    "has_key": bool(data.get("key")), "key_mask": "••••••••" if data.get("key") else "",
                    "runtime": runtime_status()}

    def save(self, base_url, model, api_key=None):
        base_url, model = base_url.strip().rstrip("/"), model.strip()
        url = urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or len(base_url) > 500):
            raise ValueError("请输入有效的 Anthropic 兼容服务地址（不是 /v1/messages 接口）")
        if not model or len(model) > 200 or any(ord(char) < 32 for char in model):
            raise ValueError("请输入模型名称")
        with self.lock:
            data = self._load()
            if api_key is not None:
                if not api_key.strip() or len(api_key) > 4096:
                    raise ValueError("请输入 API Key；留空可保留已有 Key")
                data["key"] = self.cipher(api_key.strip())
            if not data.get("key"):
                raise ValueError("请先填写 API Key")
            data.update(base_url=base_url, model=model)
            data.pop("web_check", None)
            self._write(data)
            return self.public()

    def credentials(self):
        with self.lock:
            data = self._load()
            if not data.get("key") or not data.get("model"):
                raise ValueError("请先配置 AI 服务")
            return {"base_url": data["base_url"], "model": data["model"],
                    "api_key": self.cipher(data["key"], decrypt=True)}

    def delete(self):
        with self.lock:
            data = self._load()
            self._write({"base_url": DEFAULT_BASE_URL, "model": "", "preferences": data.get("preferences", {})})
            return self.public()

    def preference(self, space_id):
        with self.lock:
            return bool(self._load().get("preferences", {}).get(str(space_id), False))

    def set_preference(self, space_id, enabled):
        with self.lock:
            data = self._load()
            data.setdefault("preferences", {})[str(space_id)] = bool(enabled)
            self._write(data)
            return {"web_enabled": bool(enabled)}

    def remove_preference(self, space_id):
        with self.lock:
            data = self._load()
            if data.get("preferences", {}).pop(str(space_id), None) is not None:
                self._write(data)


research_config = ResearchConfig()
