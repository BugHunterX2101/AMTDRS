"""Command line entry point.

Four verbs, and `doctor` is the one that matters most to anyone running this for
the first time. The three things that break a fresh setup — an inference key that
works, a project id that actually has Sandboxes enabled, and model ids that
resolve — fail at different layers and at different times, and without `doctor`
you discover the second one halfway through a job. Checking them takes about
fifteen seconds and turns "it did not work" into a line number.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys

from principal.config import Settings, Tier, get_settings


def main(argv: list[str] | None = None) -> int:
    # A Windows console defaults to a legacy code page, which turns every
    # non-ASCII character in this program's output into a question mark. Judges
    # should not have to decode mojibake to read a diagnostic.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(
        prog="principal",
        description="Principal — autonomous technical debt remediation swarm.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the HTTP API and dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p_serve.add_argument("--reload", action="store_true")

    p_doctor = sub.add_parser("doctor", help="check credentials, models and sandbox access")
    p_doctor.add_argument("--json", action="store_true", dest="as_json")

    p_run = sub.add_parser("run", help="run one job headlessly and print its event stream")
    p_run.add_argument("--repo", required=True, help="git URL or a local path")
    p_run.add_argument("--commit", required=True)
    p_run.add_argument("--goal", required=True)
    p_run.add_argument("--target", default=None, help="fully qualified name of the symbol to change")
    p_run.add_argument("--dry-run", action="store_true", help="stop after planning; touches no sandbox")
    p_run.add_argument("--fake-sandbox", action="store_true",
                       help="force the local in-process sandbox even if credentials are present")

    p_radius = sub.add_parser("radius", help="print the blast radius of a symbol and exit")
    p_radius.add_argument("--repo", required=True)
    p_radius.add_argument("--commit", default="HEAD")
    p_radius.add_argument("--target", required=True)

    args = parser.parse_args(argv)

    if args.command == "serve":
        return _serve(args)
    if args.command == "doctor":
        return asyncio.run(_doctor(as_json=args.as_json))
    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "radius":
        return asyncio.run(_radius(args))
    return 2


def _serve(args) -> int:
    import uvicorn

    uvicorn.run("principal.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


# --------------------------------------------------------------------- doctor --

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"


async def _doctor(*, as_json: bool) -> int:
    settings = get_settings()
    checks: list[dict[str, object]] = []

    def record(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    record("python", _OK if sys.version_info >= (3, 11) else _FAIL,
           f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} (need >= 3.11)")

    _check_imports(record)
    _check_database(record, settings)

    if not settings.nebius_api_key:
        record("nebius_api_key", _WARN,
               "unset — Principal will run against the in-process FakeSandbox with no inference. "
               "Set NEBIUS_API_KEY in .env for the real thing.")
    else:
        record("nebius_api_key", _OK, f"set ({_masked(settings.nebius_api_key)})")
        await _check_inference(record, settings)

    if not settings.nebius_project_id:
        record("nebius_project_id", _WARN,
               "unset — Sandboxes are authorised per project, so inference working tells you "
               "nothing about sandbox access. Set NEBIUS_PROJECT_ID to enable real runs.")
    elif settings.sandbox_key:
        await _check_sandbox(record, settings)

    if not settings.github_token or not settings.principal_fork_repo:
        record("github", _WARN,
               "GITHUB_TOKEN or PRINCIPAL_FORK_REPO unset — jobs run to completion and produce a "
               "verification report, but no pull request is opened.")
    else:
        record("github", _OK, f"publishing to fork {settings.principal_fork_repo}")

    worst = _FAIL if any(c["status"] == _FAIL for c in checks) else _OK
    if as_json:
        print(json.dumps({"status": worst, "checks": checks}, indent=2))
    else:
        width = max(len(str(c["check"])) for c in checks)
        for c in checks:
            mark = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}[str(c["status"])]
            print(f"  [{mark}] {str(c['check']):<{width}}  {c['detail']}")
        print()
        print("doctor: " + ("problems found" if worst == _FAIL else "ready"))
    return 1 if worst == _FAIL else 0


def _check_imports(record) -> None:
    for mod, why in (
        ("tree_sitter", "source parsing"),
        ("tree_sitter_python", "Python grammar"),
        ("openai", "Token Factory inference client"),
        ("contree_sdk", "Token Factory Sandboxes client"),
        ("fastapi", "HTTP API"),
    ):
        try:
            __import__(mod)
            record(f"import {mod}", _OK, why)
        except ImportError as exc:
            record(f"import {mod}", _FAIL, f"{why} — {exc}. Run: pip install -e '.[dev]'")


def _check_database(record, settings: Settings) -> None:
    try:
        from principal.db.store import Store

        store = Store(settings.principal_db)
        n = len(store.q("SELECT name FROM sqlite_master WHERE type = 'table'"))
        store.close()
        record("database", _OK, f"{settings.principal_db} — {n} tables, migrations applied")
    except Exception as exc:  # noqa: BLE001
        record("database", _FAIL, f"{settings.principal_db}: {exc}")


async def _check_inference(record, settings: Settings) -> None:
    """Two separate questions, asked separately on purpose: can we reach the
    endpoint at all, and does each configured model id exist on it. A typo in a
    model id is otherwise a 404 in the middle of a run."""
    try:
        from principal.models.registry import resolve_models

        wanted = {t: settings.model_id(t) for t in Tier}
        registry = await resolve_models(
            settings.principal_inference_base, settings.nebius_api_key, wanted
        )
        for tier, model_id in registry.resolved.items():
            configured = wanted[tier]
            if model_id == configured:
                record(f"model {tier.value}", _OK, model_id)
            else:
                record(f"model {tier.value}", _WARN, f"{configured} resolved to {model_id}")
    except Exception as exc:  # noqa: BLE001
        record("inference", _FAIL,
               f"{settings.principal_inference_base}: {exc}")


async def _check_sandbox(record, settings: Settings) -> None:
    """One trivial disposable run. Sandbox access is granted per project and is
    the single most common reason a correctly configured key still cannot do any
    work, so it is worth the two seconds to find out now."""
    try:
        from principal.sandbox.client import ContreeSandbox

        sandbox = ContreeSandbox(
            api_key=settings.sandbox_key, project_id=settings.nebius_project_id,
            base_url=settings.principal_sandbox_base, max_inflight=settings.max_inflight_ops,
        )
        image = await sandbox.use(settings.principal_base_image)
        result = await sandbox.run(image, "echo principal-doctor", disposable=True, timeout_s=120)
        if result.exit_code == 0 and "principal-doctor" in result.stdout:
            record("sandboxes", _OK,
                   f"{settings.principal_sandbox_base} — round trip in {result.duration_ms} ms")
        else:
            record("sandboxes", _FAIL,
                   f"unexpected result: exit {result.exit_code}: {result.stdout[:200]}")
    except Exception as exc:  # noqa: BLE001
        record("sandboxes", _FAIL,
               f"{exc}. A 403 here means Sandboxes are not enabled for project "
               f"{settings.nebius_project_id}; inference working does not imply sandbox access.")


def _masked(secret: str) -> str:
    return secret[:4] + "..." + secret[-4:] if len(secret) > 12 else "***"


# ------------------------------------------------------------------------ run --

async def _run(args) -> int:
    from principal.db.events import EventLog
    from principal.db.store import Store
    from principal.models.cache import DiskCache
    from principal.models.client import ModelClient
    from principal.orchestrator.job import run_job

    settings = get_settings()
    store = Store(settings.principal_db)
    events = EventLog(store, settings.principal_runs_dir, slow_mo_ms=settings.principal_slow_mo_ms)
    events.bind_loop()

    sandbox = _make_sandbox(settings, force_fake=args.fake_sandbox)
    models = ModelClient(
        store=store, base_url=settings.principal_inference_base, api_key=settings.nebius_api_key,
        model_ids={t: settings.model_id(t) for t in Tier}, capability={},
        cache=DiskCache(settings.principal_cache_dir, enabled=settings.principal_model_cache),
    )

    job = store.create_job(
        repo_url=args.repo, commit_sha=args.commit, goal=args.goal, target_fqn=args.target,
        token_budget=settings.token_budget_default, tunables=settings.recorded_tunables(),
    )
    print(f"job {job.id}")

    printer = asyncio.create_task(_print_events(events, job.id))
    try:
        if args.dry_run:
            from principal.orchestrator.job import _ingest_and_baseline, _map, _plan

            await _ingest_and_baseline(job.id, store, events, sandbox, settings)
            graph_id = await _map(job.id, store, events, settings)
            await _plan(job.id, store, events, models, graph_id, settings)
        else:
            await run_job(job.id, store=store, events=events, sandbox=sandbox, models=models,
                          settings=settings)
    finally:
        await asyncio.sleep(0.1)
        printer.cancel()
        # Awaiting the cancelled task matters: cancel() only schedules the
        # CancelledError, and the drain in _print_events' handler runs at the
        # next await point. Without this, that drain ran after store.close()
        # below and died on a closed database, losing the tail of every run.
        with contextlib.suppress(asyncio.CancelledError):
            await printer

    final = store.get_job(job.id)
    assert final is not None
    print(f"\nstate       {final.state}")
    print(f"stop reason {final.stop_reason or '-'}")
    if final.pr_url:
        print(f"pull request {final.pr_url}")
    store.close()
    # A job that correctly declines to ship is a success. Only an abort — the
    # system failing to do its job — is a non-zero exit.
    return 1 if final.state == "Aborted" else 0


async def _print_events(events, job_id: str) -> None:
    """Print events live, then drain whatever the live subscriber missed.

    The drain is bounded by the highest seq already printed. Replaying from zero
    would print the whole run a second time, which is exactly the shape of bug
    that makes a demo look broken while nothing is actually wrong.
    """

    def show(ev) -> None:
        print(f"  {ev.seq:>4}  {ev.kind:<28} {json.dumps(ev.payload, default=str)[:140]}")

    queue = events.subscribe(job_id)
    seen = 0
    try:
        while True:
            ev = await queue.get()
            seen = max(seen, ev.seq)
            show(ev)
    except asyncio.CancelledError:
        for ev in events.replay(job_id, after_seq=seen):
            show(ev)
        raise
    finally:
        events.unsubscribe(job_id, queue)


def _make_sandbox(settings: Settings, *, force_fake: bool):
    if not force_fake and settings.nebius_api_key and settings.nebius_project_id:
        from principal.sandbox.client import ContreeSandbox

        return ContreeSandbox(
            api_key=settings.sandbox_key, project_id=settings.nebius_project_id,
            base_url=settings.principal_sandbox_base, max_inflight=settings.max_inflight_ops,
        )
    from principal.sandbox.fake import FakeSandbox

    return FakeSandbox()


# --------------------------------------------------------------------- radius --

async def _radius(args) -> int:
    from principal.db.store import Store
    from principal.graph.build import build as build_graph
    from principal.graph.radius import blast_radius, find_target
    from principal.sandbox.baseline import ensure_snapshot

    settings = get_settings()
    store = Store(settings.principal_db)
    snapshot = await ensure_snapshot(args.repo, args.commit, settings.principal_snapshots_dir)
    stats = build_graph(store, snapshot, args.repo, args.commit)
    target = find_target(store, stats.graph_id, args.target)
    radius = blast_radius(store, stats.graph_id, target, max_depth=settings.radius_max_depth,
                          cap=settings.radius_file_cap)
    print(json.dumps(radius.to_payload(), indent=2, default=str))
    store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
