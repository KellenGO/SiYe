"""Prepare the pinned official Windows agent binary for source and EXE builds."""

import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.1.287"
URL = f"https://registry.npmjs.org/@anthropic-ai/claude-code-win32-x64/-/claude-code-win32-x64-{VERSION}.tgz"
INTEGRITY = "PWFxa2+cd8sIco+W3qDR85GhumGzNNdwgqHkz2u5jNhvoH8i4loBvOMEyrtZo3bYVwNeEt905GImhchv2duQGg=="
NOTICE_URL = f"https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-{VERSION}.tgz"
NOTICE_INTEGRITY = "V5WRpA+p41siSlf/Ujxjm//RtYvySxH/p9/rh6zRxNoHeBzqTgSDw3nP8PgtACM/KcVx/g5TFUNdVkq51dfg0g=="


def prepare_notices(target):
    notice = target / "CLAUDE_CODE_README.md"
    if not notice.exists():
        with urllib.request.urlopen(NOTICE_URL, timeout=60) as response:
            archive = response.read()
        if base64.b64encode(hashlib.sha512(archive).digest()).decode() != NOTICE_INTEGRITY:
            raise RuntimeError("Agent notice integrity check failed")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
            notice.write_bytes(package.extractfile("package/README.md").read())
    from importlib.metadata import distribution
    sdk = distribution("claude-agent-sdk")
    for entry in sdk.files:
        if str(entry).endswith("/licenses/LICENSE"):
            (target / "CLAUDE_AGENT_SDK_LICENSE").write_bytes(sdk.locate_file(entry).read_bytes())
            break
    else:
        raise RuntimeError("Agent SDK license is missing")


def prepare():
    target = ROOT / "agent_runtime"
    stamp = target / "runtime.json"
    if stamp.exists():
        metadata = json.loads(stamp.read_text(encoding="utf-8"))
        binary = target / "claude.exe"
        if metadata.get("version") == VERSION and binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest() == metadata.get("sha256"):
            prepare_notices(target)
            print(f"Agent runtime {VERSION}: verified")
            return
    print(f"Downloading official agent runtime {VERSION}")
    with urllib.request.urlopen(URL, timeout=120) as response:
        archive = response.read()
    if base64.b64encode(hashlib.sha512(archive).digest()).decode() != INTEGRITY:
        raise RuntimeError("Agent runtime integrity check failed")
    target.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        files = {Path(member.name).name: member for member in package.getmembers() if member.isfile()}
        if "claude.exe" not in files:
            raise RuntimeError("Official package has no Windows executable")
        for name in ("claude.exe", "LICENSE", "README.md"):
            if name in files:
                (target / name).write_bytes(package.extractfile(files[name]).read())
    stamp.write_text(json.dumps({"version": VERSION, "sha256": hashlib.sha256((target / "claude.exe").read_bytes()).hexdigest()}), encoding="utf-8")
    prepare_notices(target)
    print(f"Agent runtime {VERSION}: verified and ready")


if __name__ == "__main__":
    prepare()
