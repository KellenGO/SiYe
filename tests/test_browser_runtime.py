"""Browser temporary storage must be shared by API and worker processes."""

import asyncio
import os
import sys

import pytest

from base import browser_runtime


@pytest.fixture
def browser_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SIYE_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("TEMP", str(tmp_path / "restricted-system-temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "restricted-system-temp"))
    return tmp_path / "app-data" / ".cache" / "browser-temp"


def test_browser_temp_uses_app_storage_without_changing_profiles(browser_temp, tmp_path):
    profile = tmp_path / "app-data" / "browser_data" / "xhs_user_data_dir"
    profile.mkdir(parents=True)
    marker = profile / "existing-session"
    marker.write_bytes(b"preserve-session")

    assert browser_runtime.configure_browser_temp() == browser_temp
    assert browser_runtime.configure_browser_temp() == browser_temp
    assert browser_temp.is_dir()
    assert os.environ["TEMP"] == os.environ["TMP"] == str(browser_temp)
    assert marker.read_bytes() == b"preserve-session"


def test_non_windows_keeps_existing_temp(monkeypatch, browser_temp):
    monkeypatch.setattr(sys, "platform", "linux")
    previous = os.environ["TEMP"], os.environ["TMP"]

    assert browser_runtime.configure_browser_temp() is None
    assert (os.environ["TEMP"], os.environ["TMP"]) == previous
    assert not browser_temp.exists()


def test_default_browser_temp_uses_application_directory(monkeypatch, browser_temp, tmp_path):
    monkeypatch.delenv("SIYE_DATA_DIR")
    monkeypatch.setattr(browser_runtime, "application_root", lambda: tmp_path / "app")

    directory = browser_runtime.configure_browser_temp()

    assert directory == tmp_path / "app" / ".cache" / "browser-temp"
    assert directory.is_dir()
    assert not browser_temp.exists()


def test_api_startup_prepares_temp_inherited_by_workers(browser_temp):
    from api.main import _prepare_browser_runtime
    from api.services.worker_process import worker_env

    asyncio.run(_prepare_browser_runtime())

    environment = worker_env()
    assert environment["TEMP"] == environment["TMP"] == str(browser_temp)


def test_standalone_worker_prepares_temp_before_reading_request(monkeypatch, browser_temp):
    from aggregate_search import worker

    def read_request():
        assert os.environ["TEMP"] == os.environ["TMP"] == str(browser_temp)
        assert browser_temp.is_dir()
        raise EOFError

    monkeypatch.setattr(worker, "read_request", read_request)
    monkeypatch.setattr(sys, "argv", ["worker.py"])
    with pytest.raises(SystemExit) as result:
        worker.main()
    assert result.value.code == 0
