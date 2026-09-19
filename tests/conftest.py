"""Shared fixtures.

Every fixture here is hermetic: a temporary database, a temporary runs
directory, an in-process sandbox. Nothing in this suite reaches Nebius, needs a
credential or costs money, which is what lets it run on every push.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from principal.config import Settings
from principal.db.events import EventLog
from principal.db.store import Store

ROOT = Path(__file__).resolve().parent.parent
MINI_REPO = ROOT / "tests" / "fixtures" / "mini_repo"

# The commit the fixture snapshot is tagged with. It is not a real git object —
# ensure_snapshot tags the fixture's single commit with it so that a `git
# checkout <sha>` inside a sandbox script behaves the same as it would against a
# genuinely cloned remote.
FIXTURE_SHA = "deadbeef"
TARGET_FQN = "src.auth.session.create"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        principal_db=tmp_path / "principal.db",
        principal_runs_dir=tmp_path / "runs",
        principal_snapshots_dir=tmp_path / "snapshots",
        principal_cache_dir=tmp_path / "cache",
        nebius_api_key="",
        nebius_project_id="",
        principal_model_cache=False,
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    s = Store(settings.principal_db)
    yield s
    s.close()


@pytest.fixture
def events(store: Store, settings: Settings) -> EventLog:
    log = EventLog(store, settings.principal_runs_dir)
    try:
        log.bind_loop(asyncio.get_running_loop())
    except RuntimeError:
        pass
    return log


@pytest.fixture
def sandbox():
    from principal.sandbox.fake import FakeSandbox

    return FakeSandbox()


@pytest.fixture
async def snapshot(settings: Settings) -> Path:
    """A git-initialised copy of the mini repo, as the gates and the graph see it."""
    from principal.sandbox.baseline import ensure_snapshot

    return await ensure_snapshot(str(MINI_REPO), FIXTURE_SHA, settings.principal_snapshots_dir)


@pytest.fixture
async def graph(store: Store, snapshot: Path):
    """The parsed code graph of the mini repo."""
    from principal.graph.build import build

    return build(store, snapshot, str(MINI_REPO), FIXTURE_SHA)


def diff_block(body: str) -> str:
    """Wrap a unified diff in the protocol delimiters the coder agent emits."""
    return f"<<<DIFF\n{body.rstrip()}\nDIFF>>>"
