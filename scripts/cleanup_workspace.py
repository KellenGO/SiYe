# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Preview or remove explicitly retired local artifacts without following links."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess


RETIRED = (
    "build/codebase-cleanup/locked-dist", "build/codebase-cleanup/locked-venv",
    "build/codebase-cleanup/baseline-ui", "build/codebase-cleanup/baseline-webui.zip",
    "build/v1.2.0", "build/ai-extension/dist/SiYe",
    "build/ai-extension/dist/.pyinstaller",
    "build/ai-extension/published/SiYe-AI-Windows-x64.zip",
    "build/ai-extension/published/SiYe-AI-Windows-x64.zip.sha256",
    "dist/SiYe-Windows-x64.zip", "dist/SiYe-Windows-x64.zip.sha256",
    "dist/SiYe-Setup-Windows-x64.exe", "dist/SiYe-Setup-Windows-x64.exe.sha256",
    "agent_runtime", ".tmp_restore_uv_template",
    "build/reading-ui", "build/extension-manager-ui", "build/extensions-ui",
    "build/detail-actions", "webui/.test-dist", ".pytest_cache",
    ".tmp_reading_access_live.py", ".tmp_reading_live.py", ".tmp_reading_play_live.py",
    ".tmp_comments_live.py", ".tmp_recorder_probe.py", ".tmp_published_ai_verify.py",
    ".tmp_ai_extension_release_notes.md", ".tmp_restore_environment_export.log",
    ".tmp_restore_environment_requirements.txt",
    "build/research-chat.png", "build/research-desktop.png", "build/research-docked-history.png",
    "build/research-expanded.png", "build/research-mobile.png", "build/research-new-fullscreen.png",
    "build/research-note-fullscreen.png", "build/research-sessions.png", "build/research-settings.png",
    "build/research-split-1024.png", "build/research-split-1440.png", "build/research-split-1920.png",
    "build/spaces-desktop.png", "build/spaces-favorites-1920.png", "build/spaces-favorites-390.png",
    "build/spaces-favorites-sidebars.png", "build/spaces-layout-1024.png", "build/spaces-layout-1280.png",
    "build/spaces-layout-1440.png", "build/spaces-mobile-material.png", "build/spaces-mobile-note.png",
    "build/spaces-note-fullscreen.png", "build/spaces-search-edge-1440.png", "build/spaces-search-edge-1920.png",
)
PROFILES = ("xhs_user_data_dir", "dy_user_data_dir", "bili_user_data_dir", "zhihu_user_data_dir")
CACHE_PATHS = (
    "Default/Cache", "Default/Code Cache", "Default/GPUCache",
    "ShaderCache", "GrShaderCache", "GraphiteDawnCache",
    "Default/DawnGraphiteCache", "Default/DawnWebGPUCache",
)
PERSONAL_FILES = {"library.db", "cookies", "login data", "research-ai.json"}
PROTECTED_ROOTS = {".git", ".venv", "node_modules", "data", ".cache", ".workbuddy"}


@dataclass
class Candidate:
    path: Path
    browser_cache: bool = False


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def tree_entries(path: Path):
    """Yield entries, including reparse points, but never their contents."""
    yield path
    if not is_link(path) and path.is_dir():
        with os.scandir(path) as entries:
            children = sorted((Path(entry.path) for entry in entries))
        for child in children:
            yield from tree_entries(child)


def discover(root: Path, include_browser_caches: bool = False) -> list[Candidate]:
    candidates = [Candidate(root / name) for name in RETIRED]
    candidates.extend(Candidate(path) for path in sorted(root.glob(".tmp_pytest_*")))
    # Only visit source directories; dependencies, user data and worktrees are excluded.
    source_dirs = ("api", "aggregate_search", "base", "cache", "config", "constant",
                   "media_platform", "model", "tools", "scripts", "tests")
    candidates.append(Candidate(root / "__pycache__"))
    for name in source_dirs:
        base = root / name
        if not base.exists() or is_link(base):
            continue
        for current, dirs, _ in os.walk(base, followlinks=False):
            dirs[:] = [item for item in dirs if not is_link(Path(current) / item)]
            if ".git" in dirs or ".git" in os.listdir(current):
                dirs[:] = []
                continue
            if "__pycache__" in dirs:
                candidates.append(Candidate(Path(current) / "__pycache__"))
                dirs.remove("__pycache__")
    if include_browser_caches:
        candidates.extend(Candidate(root / "browser_data" / profile / name, True)
                          for profile in PROFILES for name in CACHE_PATHS)
    return [item for item in candidates if os.path.lexists(item.path)]


def git_guards(root: Path) -> tuple[set[Path], set[Path]]:
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True,
                              capture_output=True).stdout
    tracked = {root / os.fsdecode(name) for name in git("ls-files", "-z").split(b"\0") if name}
    worktrees = {Path(os.fsdecode(line.removeprefix(b"worktree "))).resolve()
                 for line in git("worktree", "list", "--porcelain", "-z").split(b"\0")
                 if line.startswith(b"worktree ")}
    return tracked, worktrees


def process_commands() -> list[str] | None:
    """Keep process details private; return None if inspection is unavailable."""
    if os.name != "nt":
        return None
    command = ("$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | "
               "Where-Object { $_.ProcessId -ne " + str(os.getpid()) +
               " -and $_.Name -match 'python|SiYe|四野|node|chrome|msedge|claude' } | "
               "Select-Object Name,CommandLine) | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                check=True, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=20)
        rows = json.loads(result.stdout or "[]")
        if isinstance(rows, dict):
            rows = [rows]
        if any(not row.get("CommandLine") for row in rows):
            return None
        return [row["CommandLine"].lower().replace("\\", "/") for row in rows]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def inspect(candidate: Candidate, root: Path, tracked: set[Path],
            worktrees: set[Path], commands: list[str] | None) -> tuple[int, list[Path]]:
    path = candidate.path
    relative = path.relative_to(root)
    if ".." in relative.parts or not relative.parts:
        raise ValueError("target is outside the workspace")
    if relative.parts[0] in PROTECTED_ROOTS or relative.parts[:2] in {
            ("webui", "node_modules"), ("dist", "SiYe")}:
        raise ValueError("protected directory")
    if relative.parts[0] == "browser_data":
        allowed = {Path("browser_data") / profile / name
                   for profile in PROFILES for name in CACHE_PATHS}
        if not candidate.browser_cache or relative not in allowed:
            raise ValueError("not an approved browser cache")
    for parent in path.parents:
        if parent == root:
            break
        if is_link(parent):
            raise ValueError("target has a linked parent")
    if any(file == path or path in file.parents for file in tracked):
        raise ValueError("contains Git-tracked files")
    if any(tree == path or path in tree.parents or tree in path.parents
           for tree in worktrees if tree != root):
        raise ValueError("overlaps a registered worktree")
    if commands is None:
        raise ValueError("cannot verify active processes")
    if candidate.browser_cache:
        profile = root / "browser_data" / relative.parts[1]
        needle = str(profile).lower().replace("\\", "/")
        if any(needle in command or "browser_data/" + relative.parts[1] in command
               for command in commands):
            raise ValueError("browser profile is in use")
    needle = str(path).lower().replace("\\", "/")
    if any(needle in command or relative.as_posix().lower() in command for command in commands):
        raise ValueError("target is referenced by an active process")
    size = 0
    links = []
    for entry in tree_entries(path):
        if entry.name == ".git":
            raise ValueError("contains a nested Git repository")
        if is_link(entry):
            links.append(entry)
        elif entry.is_file():
            fixture = (relative.parts[0].startswith(".tmp_pytest_") and
                       any(part.startswith("test_") for part in entry.relative_to(path).parts[:-1]))
            if not fixture and (entry.name.lower() in PERSONAL_FILES or
                                entry.suffix.lower() in {".db", ".sqlite", ".sqlite3"}):
                raise ValueError("contains a database or personal settings; inspect manually")
            size += entry.stat().st_size
    return size, links


def remove_tree(path: Path) -> None:
    """Use native unlink/rmdir operations; junction targets are never traversed."""
    if is_link(path):
        directory_link = bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x10)
        if os.name == "nt" and directory_link:
            path.rmdir()
        else:
            path.unlink()
    elif path.is_dir():
        with os.scandir(path) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            remove_tree(child)
        path.rmdir()
    else:
        path.unlink()


def run(root: Path, *, apply: bool = False, include_browser_caches: bool = False,
        tracked: set[Path] | None = None, worktrees: set[Path] | None = None,
        commands: list[str] | None = None) -> dict:
    root = root.resolve()
    if tracked is None or worktrees is None:
        tracked, worktrees = git_guards(root)
    if commands is None:
        commands = process_commands()
    report = {"mode": "apply" if apply else "preview", "items": [], "removed_bytes": 0,
              "eligible_bytes": 0, "unknown_build_entries": []}
    candidates = discover(root, include_browser_caches)
    for item in candidates:
        row = {"path": item.path.relative_to(root).as_posix(), "bytes": 0}
        try:
            size, links = inspect(item, root, tracked, worktrees, commands)
            row.update(bytes=size, links=[str(link.relative_to(root)) for link in links])
            report["eligible_bytes"] += size
            if apply:
                # Repeat preflight immediately before mutation, including process inspection.
                fresh_commands = process_commands() if os.name == "nt" else commands
                inspect(item, root, tracked, worktrees, fresh_commands)
                targets = []
                for link in links:
                    target = link.resolve()
                    # Targets inside this disposable tree are independently removed.
                    if target.exists() and target != item.path and item.path not in target.parents:
                        targets.append((target, target.stat()))
                remove_tree(item.path)
                for target, before in targets:
                    after = target.stat()
                    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                        raise OSError("link target changed during cleanup")
                report["removed_bytes"] += size
                row["status"] = "removed"
            else:
                row["status"] = "eligible"
        except (OSError, ValueError) as error:
            row.update(status="skipped", reason=str(error))
        report["items"].append(row)
    known = {Path(name).parts[1] for name in RETIRED if name.startswith("build/")}
    known.update({"tools", "workspace-cleanup"})
    report["unknown_build_entries"] = [path.name for path in sorted((root / "build").glob("*"))
                                       if path.name not in known]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete approved artifacts (default: preview)")
    parser.add_argument("--include-browser-caches", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        status = subprocess.run(["git", "status", "--short"], cwd=root, check=True,
                                capture_output=True, text=True).stdout
        print("Git status before cleanup:\n" + (status or "(clean)"))
        report = run(root, apply=args.apply, include_browser_caches=args.include_browser_caches)
    except (OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Cleanup aborted: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
