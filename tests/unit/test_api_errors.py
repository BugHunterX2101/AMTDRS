"""A `PrincipalError` that reaches the HTTP layer becomes the documented error
envelope (SPEC.md, "Error envelope"), not a bare 500.

Found while wiring `/debug/blast-radius`: `RadiusTooLarge` propagated straight
past every `except PrincipalError` in `routes.py` — that endpoint's own handler
only caught `find_target`'s `TARGET_NOT_FOUND` — and FastAPI's default handling
of an uncaught exception is a 500 with no `code` field at all, on the one
endpoint whose entire job is to report `RADIUS_TOO_LARGE` legibly.

This tests the handler directly against a minimal app rather than the full
`principal.api.app:app`, which requires real settings and a lifespan (model
probe, sandbox probe) this suite has no business depending on.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from principal.api.app import _principal_error_handler
from principal.errors import Code, PrincipalError, RadiusTooLarge


def _app_that_raises(exc: PrincipalError) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(PrincipalError, _principal_error_handler)

    @app.get("/boom")
    def boom():
        raise exc

    return app


def test_radius_too_large_becomes_a_structured_422_not_a_bare_500():
    client = TestClient(_app_that_raises(RadiusTooLarge(84, 40)), raise_server_exceptions=False)
    resp = client.get("/boom")

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "RADIUS_TOO_LARGE"
    assert body["error"]["retryable"] is False
    assert "84" in body["error"]["message"] and "40" in body["error"]["message"]


def test_unmapped_code_defaults_to_400_rather_than_500():
    client = TestClient(
        _app_that_raises(PrincipalError(Code.DEPENDENCY_CYCLE, "cycle among [t1, t2]")),
        raise_server_exceptions=False,
    )
    resp = client.get("/boom")

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "DEPENDENCY_CYCLE"


def test_target_not_found_maps_to_404():
    client = TestClient(
        _app_that_raises(PrincipalError(Code.TARGET_NOT_FOUND, "no symbol named 'x'")),
        raise_server_exceptions=False,
    )
    resp = client.get("/boom")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TARGET_NOT_FOUND"
