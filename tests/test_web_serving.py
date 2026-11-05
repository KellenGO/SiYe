from fastapi.testclient import TestClient
from fastapi import HTTPException

from api import main


def _client_with_dist(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<html><body><div id="root"></div></body></html>', encoding="utf-8"
    )
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setattr(main, "WEBUI_DIR", dist)
    return TestClient(main.app)


def test_production_serves_index_assets_and_spa_routes(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)

    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").text == "console.log('ok')"
    assert client.get("/accounts").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404
    assert main._frontend_file("../index.html") is None
    try:
        main._serve_frontend("../private")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("path traversal must not reach SPA fallback")


def test_api_paths_are_not_consumed_by_spa_fallback(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)

    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "root" not in response.text


def test_missing_production_build_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "WEBUI_DIR", tmp_path / "missing-dist")
    client = TestClient(main.app)

    response = client.get("/")
    assert response.status_code == 200
    assert "npm run build" in response.json()["note"]
    assert client.get("/accounts").status_code == 404


def test_index_is_not_cached_but_hashed_assets_are(monkeypatch, tmp_path):
    """`index.html` 必须 no-cache，`assets/*` 可以长缓存。

    这是"改了代码但界面没变"的根因：index.html 里写的是带 hash 的资源名，
    它一旦被浏览器缓存住，重新构建后用户仍然加载旧 bundle —— 刷新页面也不一定
    管用（FileResponse 默认带 ETag/Last-Modified，会走协商缓存）。
    """
    client = _client_with_dist(monkeypatch, tmp_path)
    index = client.get("/")
    assert index.status_code == 200
    assert "no-cache" in index.headers.get("cache-control", "")

    spa = client.get("/accounts")
    assert spa.status_code == 200
    assert "no-cache" in spa.headers.get("cache-control", "")
