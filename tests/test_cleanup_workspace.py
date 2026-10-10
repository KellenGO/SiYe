# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Exercise cleanup against isolated files, including real Windows junctions."""

import os
from pathlib import Path
import subprocess

import pytest

from scripts import cleanup_workspace as cleanup


def put(root, name, content=b"keep"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "process_commands", lambda: [])
    return tmp_path


def run(root, **kwargs):
    return cleanup.run(root, tracked=set(), worktrees=set(), commands=[], **kwargs)


def test_preview_then_apply_preserves_dependencies_data_and_candidate(workspace):
    garbage = put(workspace, "build/v1.2.0/package.bin", b"obsolete")
    protected = [put(workspace, name) for name in (
        "data/version-backups/old/library.db", ".cache/cache.json",
        ".venv/Lib/dependency.py", "webui/node_modules/package/index.js",
        "dist/SiYe/SiYe.exe", "build/tools/inno/tool.exe",
        "build/ai-extension/dist/SiYe-Windows-x64.zip",
        "build/ai-extension/dist/SiYe-Windows-x64.zip.sha256",
        "build/ai-extension/published/npm-audit.json",
        "build/future-candidate/package.zip", "build/future-verification.png")]
    preview = run(workspace)
    assert preview["eligible_bytes"] == len(b"obsolete")
    assert garbage.exists()
    assert preview["removed_bytes"] == 0
    result = run(workspace, apply=True)
    assert not garbage.exists()
    assert result["removed_bytes"] == len(b"obsolete")
    assert all(path.read_bytes() == b"keep" for path in protected)
    assert "future-candidate" in result["unknown_build_entries"]
    assert "future-verification.png" in result["unknown_build_entries"]


@pytest.mark.parametrize("guard", ["tracked", "worktree", "nested", "personal", "active"])
def test_protected_artifacts_are_skipped(workspace, guard):
    artifact = put(workspace, "build/v1.2.0/package.bin")
    tracked, worktrees, commands = set(), set(), []
    if guard == "tracked":
        tracked.add(artifact)
    elif guard == "worktree":
        worktrees.add(artifact.parent)
    elif guard == "nested":
        put(workspace, "build/v1.2.0/sub/.git", b"gitdir: elsewhere")
    elif guard == "personal":
        put(workspace, "build/v1.2.0/data/library.db")
    else:
        commands.append(str(artifact.parent).lower().replace("\\", "/"))
    report = cleanup.run(workspace, apply=True, tracked=tracked, worktrees=worktrees, commands=commands)
    assert artifact.exists()
    assert report["items"][0]["status"] == "skipped"


def test_browser_cache_requires_opt_in_and_idle_profile(workspace, monkeypatch):
    base = "browser_data/xhs_user_data_dir/Default/"
    cache = put(workspace, base + "Cache/entry")
    protected = [put(workspace, base + name) for name in (
        "Network/Cookies", "Preferences", "Local Storage/store", "IndexedDB/store",
        "Service Worker/CacheStorage/offline")]
    run(workspace, apply=True)
    assert cache.exists()
    command = str(workspace / "browser_data/xhs_user_data_dir").lower().replace("\\", "/")
    monkeypatch.setattr(cleanup, "process_commands", lambda: [command])
    cleanup.run(workspace, apply=True, include_browser_caches=True, tracked=set(),
                worktrees=set(), commands=[command])
    assert cache.exists()
    monkeypatch.setattr(cleanup, "process_commands", lambda: [])
    run(workspace, apply=True, include_browser_caches=True)
    assert not cache.exists()
    assert all(path.read_bytes() == b"keep" for path in protected)


def test_unavailable_process_inspection_skips_deletion(workspace, monkeypatch):
    path = put(workspace, "agent_runtime/claude.exe")
    monkeypatch.setattr(cleanup, "process_commands", lambda: None)
    result = cleanup.run(workspace, apply=True, tracked=set(), worktrees=set())
    assert path.exists()
    assert "cannot verify" in result["items"][0]["reason"]


def test_permission_failure_is_reported(workspace, monkeypatch):
    path = put(workspace, "agent_runtime/claude.exe")
    def denied(_):
        raise PermissionError("locked")
    monkeypatch.setattr(cleanup, "remove_tree", denied)
    result = run(workspace, apply=True)
    assert path.exists()
    assert result["removed_bytes"] == 0
    assert result["items"][0]["reason"] == "locked"


def test_only_pytest_fixture_databases_are_disposable(workspace):
    fixture = put(workspace, ".tmp_pytest_old/test_store0/library.db")
    uncertain = put(workspace, ".tmp_pytest_unknown/library.db")
    run(workspace, apply=True)
    assert not fixture.exists()
    assert uncertain.read_bytes() == b"keep"


@pytest.mark.parametrize("relative", ["../outside", "data", ".venv", "webui/node_modules",
                                      "dist/SiYe", "browser_data/xhs_user_data_dir"])
def test_inspection_rejects_protected_or_outside_path(workspace, relative):
    with pytest.raises(ValueError):
        cleanup.inspect(cleanup.Candidate(workspace / relative), workspace, set(), set(), [])


def junction(link: Path, target: Path):
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # Native PowerShell creates the junction; no shell deletion follows it.
        script = ("$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                  "-Path $env:SIYE_TEST_LINK -Target $env:SIYE_TEST_TARGET | Out-Null")
        env = dict(os.environ, SIYE_TEST_LINK=str(link), SIYE_TEST_TARGET=str(target))
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                       env=env, check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_junction_target_survives_parent_cleanup(workspace):
    target_file = put(workspace, "webui/node_modules/dependency.js", b"dependency")
    link = workspace / "build/codebase-cleanup/baseline-ui/webui/node_modules"
    junction(link, target_file.parent)
    result = run(workspace, apply=True)
    assert not link.parent.exists()
    assert target_file.read_bytes() == b"dependency"
    assert result["items"][0]["status"] == "removed"


def test_linked_parent_cannot_redirect_cleanup(workspace):
    target_file = put(workspace, "elsewhere/Cache/entry")
    link = workspace / "browser_data/xhs_user_data_dir/Default"
    junction(link, target_file.parent.parent)
    result = run(workspace, apply=True, include_browser_caches=True)
    assert target_file.exists()
    assert "linked parent" in result["items"][0]["reason"]


def test_broken_junction_can_be_unlinked_without_following_target(workspace):
    target = workspace / "external"
    target.mkdir()
    link = workspace / "build/codebase-cleanup/baseline-ui/linked"
    junction(link, target)
    target.rmdir()
    result = run(workspace, apply=True)
    assert not os.path.lexists(link)
    assert result["items"][0]["status"] == "removed"


def test_internal_junction_target_is_removed_as_part_of_disposable_tree(workspace):
    target_file = put(workspace, "build/codebase-cleanup/baseline-ui/target/file")
    link = target_file.parent.parent / "linked"
    junction(link, target_file.parent)
    result = run(workspace, apply=True)
    assert not target_file.exists()
    assert not os.path.lexists(link)
    assert result["items"][0]["status"] == "removed"
