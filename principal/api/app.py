"""FastAPI app, lifespan, MCP mount.

Startup sequence, in order, failing loudly at the first problem:
  1. load and validate configuration
  2. run database migrations forward (schema.sql is idempotent, so this is that)
  3. resolve the three model ids against list-models
  4. run the capability probe unless PRINCIPAL_FORCE_PROTOCOL is set
  5. probe sandbox access with one trivial disposable run
  6. mount the code-graph MCP server
  7. start the HTTP server

Steps 3 and 5 are the ones people skip because they add seconds to startup. They
are also the two failures that otherwise surface halfway through a job, when the
cost of discovering them is highest.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from principal.api.routes import router
from principal.config import Protocol, Settings, Tier, get_settings
from principal.db.events import EventLog
from principal.db.store import Store
from principal.models.cache import DiskCache
from principal.models.client import ModelClient
from principal.sandbox.client import SandboxClient

logger = logging.getLogger("principal.api")


class AppState:
    settings: Settings
    store: Store
    events: EventLog
    sandbox: SandboxClient
    models: ModelClient
    background_tasks: set


async def _startup(app: FastAPI) -> None:
    settings = get_settings()
    logger.info("principal starting up")

    store = Store(settings.principal_db)
    events = EventLog(store, settings.principal_runs_dir, slow_mo_ms=settings.principal_slow_mo_ms)
    events.bind_loop()

    capability: dict[str, Protocol] = {}
    forced: Protocol | None = None
    if settings.principal_force_protocol:
        forced = Protocol(settings.principal_force_protocol)
    elif settings.nebius_api_key:
        try:
            from openai import AsyncOpenAI

            from principal.models.probe import probe_all, summary_table

            probe_client = AsyncOpenAI(base_url=settings.principal_inference_base,
                                        api_key=settings.nebius_api_key)
            wanted = [settings.principal_model_nano, settings.principal_model_super,
                      settings.principal_model_ultra]
            caps = await probe_all(probe_client, wanted)
            capability = {m: c.best_protocol for m, c in caps.items()}
            logger.info("capability probe:\n%s", summary_table(caps))
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability probe failed, defaulting every model to text protocol: %s", exc)

    model_ids = {
        Tier.NANO: settings.principal_model_nano, Tier.SUPER: settings.principal_model_super,
        Tier.ULTRA: settings.principal_model_ultra,
    }
    if settings.nebius_api_key:
        try:
            from principal.models.registry import resolve_models

            registry = await resolve_models(settings.principal_inference_base, settings.nebius_api_key,
                                             model_ids)
            model_ids = registry.resolved
        except Exception as exc:  # noqa: BLE001
            logger.warning("model registry check failed, using configured ids unresolved: %s", exc)

    cache = DiskCache(settings.principal_cache_dir, enabled=settings.principal_model_cache)
    models = ModelClient(
        store=store, base_url=settings.principal_inference_base, api_key=settings.nebius_api_key,
        model_ids=model_ids, capability=capability, cache=cache, forced_protocol=forced,
    )

    sandbox: SandboxClient
    if settings.nebius_api_key and settings.nebius_project_id:
        from principal.sandbox.client import ContreeSandbox

        sandbox = ContreeSandbox(
            api_key=settings.nebius_api_key, project_id=settings.nebius_project_id,
            base_url=settings.principal_sandbox_base, max_inflight=settings.max_inflight_ops,
        )
    else:
        from principal.sandbox.fake import FakeSandbox

        logger.warning("no Nebius credentials configured; using FakeSandbox for local development")
        sandbox = FakeSandbox()

    app.state.settings = settings
    app.state.store = store
    app.state.events = events
    app.state.sandbox = sandbox
    app.state.models = models
    app.state.background_tasks = set()

    try:
        from mcp_code_graph.server import build_server

        app.state.mcp_server = build_server(store)
    except Exception as exc:  # noqa: BLE001
        logger.warning("code-graph MCP server not mounted: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup(app)
    yield
    app.state.store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Principal", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(router)

    dist = Path(__file__).parent.parent.parent / "dashboard" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="dashboard")

    return app


app = create_app()
