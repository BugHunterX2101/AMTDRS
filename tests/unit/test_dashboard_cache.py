"""The dashboard's pages must be revalidated on every visit; its hashed assets
may be cached for good.

Found after the ROI-slider fix was deployed: a browser that had loaded the
landing page before the deploy kept showing the broken sliders. The pages were
served with no Cache-Control at all, so the browser was free to reuse the old
index.html heuristically from its Last-Modified date, and the old index.html
names the old stylesheet. Vite gives every file under assets/ a content hash in
its name, so those can be cached forever; the pages that name them cannot.

Like test_api_errors.py, this mounts the class on a minimal app rather than
importing the full app, which needs real settings and a lifespan.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from principal.api.app import DashboardFiles


def _client(tmp_path) -> TestClient:
    (tmp_path / "index.html").write_text("<!doctype html><title>landing</title>")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "index.html").write_text("<!doctype html><title>console</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "landing-AbC123.css").write_text("body{}")
    app = FastAPI()
    app.mount("/", DashboardFiles(directory=str(tmp_path), html=True), name="dashboard")
    return TestClient(app)


def test_pages_are_revalidated_on_every_visit(tmp_path):
    client = _client(tmp_path)
    for path in ("/", "/index.html", "/app/"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-cache", path


def test_hashed_assets_are_cached_for_good(tmp_path):
    resp = _client(tmp_path).get("/assets/landing-AbC123.css")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_a_revalidated_page_that_has_not_changed_is_a_304_with_the_same_policy(tmp_path):
    client = _client(tmp_path)
    etag = client.get("/").headers["etag"]
    resp = client.get("/", headers={"if-none-match": etag})
    assert resp.status_code == 304
    assert resp.headers["cache-control"] == "no-cache"
