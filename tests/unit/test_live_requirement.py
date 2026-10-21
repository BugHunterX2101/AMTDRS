"""The hackathon requires a runtime call to Token Factory with an NVIDIA model.

The credential-less demo path is deliberate, but a deployment that is meant to
be live must not be able to degrade into it quietly. These tests pin that down:
a rejected key is an error rather than "unreachable", strict mode refuses to
start without both backends, and /healthz reports verified rather than merely
configured.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from principal.api.app import _startup
from principal.api.routes import healthz
from principal.config import Settings, Tier
from principal.errors import ConfigMissing
from principal.models import registry as registry_module
from principal.models.registry import resolve_models

WANTED = {Tier.NANO: "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"}


def _serve(monkeypatch: pytest.MonkeyPatch, status: int, ids: list[str]) -> None:
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(status, json={"data": [{"id": i} for i in ids]})
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(registry_module.httpx, "AsyncClient", factory)


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_key_is_an_error_not_unreachable(monkeypatch, status):
    _serve(monkeypatch, status, [])
    with pytest.raises(httpx.HTTPStatusError):
        await resolve_models("https://example.invalid/v1/", "bad", WANTED)


async def test_answering_registry_is_verified(monkeypatch):
    _serve(monkeypatch, 200, ["nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"])
    registry = await resolve_models("https://example.invalid/v1/", "ok", WANTED)
    assert registry.verified
    assert registry.resolved[Tier.NANO] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


async def test_unreachable_registry_passes_ids_through_unverified(monkeypatch):
    _serve(monkeypatch, 503, [])
    registry = await resolve_models("https://example.invalid/v1/", "ok", WANTED)
    assert not registry.verified
    assert registry.resolved == WANTED


@pytest.mark.parametrize("missing", ["NEBIUS_API_KEY", "NEBIUS_PROJECT_ID"])
async def test_require_live_refuses_to_start_without_credentials(settings, store, missing):
    updates = {"principal_require_live": True}
    if missing == "NEBIUS_PROJECT_ID":
        updates["nebius_api_key"] = "key"
    strict = settings.model_copy(update=updates)
    with pytest.raises(ConfigMissing) as exc:
        await _startup(FastAPI(), strict, store)
    assert exc.value.detail["variable"] == missing


async def test_default_startup_keeps_the_credentialless_demo(settings, store):
    app = FastAPI()
    await _startup(app, settings, store)
    assert app.state.backends == {"inference_verified": False, "sandbox_verified": False}


async def test_healthz_reports_unverified_for_the_demo(settings, store):
    app = FastAPI()
    await _startup(app, settings, store)
    body = await healthz(SimpleNamespace(app=app))
    assert body["status"] == "ok"
    assert body["sandbox"] == "FakeSandbox"
    assert body["live"] is False
    assert body["inference_verified"] is False
    assert body["sandbox_verified"] is False


async def test_healthz_is_live_only_when_both_backends_verified():
    app = FastAPI()
    app.state.settings = Settings(nebius_api_key="k", nebius_project_id="p")
    app.state.sandbox = object()
    app.state.backends = {"inference_verified": True, "sandbox_verified": False}
    assert (await healthz(SimpleNamespace(app=app)))["live"] is False
    app.state.backends["sandbox_verified"] = True
    assert (await healthz(SimpleNamespace(app=app)))["live"] is True
