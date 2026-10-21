"""The pieces that keep a live run honest when the platform misbehaves.

These pin behaviour that was learned from real runs against the Nebius sandbox:
transient 5xx responses that must not fail a healthy operation, tag rules the
platform enforces, a baseline script that must fail fast, and the refusal to
substitute a local stand-in for a live run.

The only synthetic inputs are the injected SDK errors (a unit test cannot make
the real platform return a 502 on demand) and a few small in-memory sources.
"""

from __future__ import annotations

import sqlite3

import pytest
from contree_sdk.sdk.exceptions import ApiStatusCodeError, ContreeTransportError

from principal.agents.coder import max_tokens_for
from principal.cli import _make_sandbox
from principal.config import Tier
from principal.db.store import Store
from principal.gates.characterise import DEFAULT_MUTATION_FLOOR
from principal.gates.strength import plan_mutants
from principal.sandbox import client as sandbox_client
from principal.sandbox.baseline import baseline_tag
from principal.sandbox.fake import FakeSandbox
from principal.sandbox.scripts import _with_newline, baseline_script


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(sandbox_client.asyncio, "sleep", fake_sleep)
    return slept


def _flaky(failures, result="done"):
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        if calls["n"] <= len(failures):
            raise failures[calls["n"] - 1]
        return result

    return call, calls


# ------------------------------------------------------------------ classification --

def test_server_errors_and_dropped_connections_are_transient():
    assert sandbox_client._is_transient(ApiStatusCodeError(status=500))
    assert sandbox_client._is_transient(ApiStatusCodeError(status=503))
    assert sandbox_client._is_transient(ContreeTransportError(error="reset"))


def test_client_errors_and_script_failures_are_not_transient():
    assert not sandbox_client._is_transient(ApiStatusCodeError(status=400))
    assert not sandbox_client._is_transient(ApiStatusCodeError(status=403))
    assert not sandbox_client._is_transient(ApiStatusCodeError(status=None))
    assert not sandbox_client._is_transient(RuntimeError("exit 1"))


# ---------------------------------------------------------------------- retrying --

async def test_a_transient_failure_is_retried_until_it_succeeds(_no_backoff):
    call, calls = _flaky([ApiStatusCodeError(status=502), ContreeTransportError(error="x")])
    assert await sandbox_client._retrying(call) == "done"
    assert calls["n"] == 3
    assert len(_no_backoff) == 2


async def test_a_non_transient_failure_is_raised_on_the_first_attempt():
    call, calls = _flaky([ApiStatusCodeError(status=403)])
    with pytest.raises(ApiStatusCodeError):
        await sandbox_client._retrying(call)
    assert calls["n"] == 1


async def test_exhausting_the_attempts_raises_the_last_error():
    errors = [ApiStatusCodeError(status=500)] * 10
    call, calls = _flaky(errors)
    with pytest.raises(ApiStatusCodeError):
        await sandbox_client._retrying(call, attempts=3)
    assert calls["n"] == 3


async def test_polling_gets_a_larger_budget_than_a_run():
    # Polling is read-only, so it can afford to outlast a longer outage.
    assert sandbox_client._POLL_ATTEMPTS > sandbox_client._TRANSIENT_ATTEMPTS
    call, calls = _flaky([ApiStatusCodeError(status=500)] * (sandbox_client._POLL_ATTEMPTS - 1))
    assert await sandbox_client._retrying(call, attempts=sandbox_client._POLL_ATTEMPTS) == "done"
    assert calls["n"] == sandbox_client._POLL_ATTEMPTS


# --------------------------------------------------------------------- schema --

def test_a_database_from_an_older_schema_gains_the_missing_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    # A table as an earlier release created it, before later columns existed.
    old.executescript(
        "CREATE TABLE job (id TEXT PRIMARY KEY, repo_url TEXT NOT NULL, "
        "commit_sha TEXT NOT NULL, goal TEXT NOT NULL, state TEXT NOT NULL, "
        "token_budget INTEGER NOT NULL, created_at TEXT NOT NULL);"
    )
    old.commit()
    old.close()

    store = Store(path)
    try:
        columns = {r[1]: r for r in store._conn.execute("PRAGMA table_info(job)")}
    finally:
        store.close()

    # Nullable and defaulted columns are added; the default is carried over.
    assert {"pr_url", "stop_reason", "finished_at", "tunables", "tokens_spent", "routine"} <= set(columns)
    assert columns["routine"][4] == "'interface_evolution'"


def test_reopening_an_up_to_date_database_changes_nothing(tmp_path):
    path = tmp_path / "new.db"
    Store(path).close()
    again = Store(path)
    try:
        assert again._conn.execute("SELECT count(*) FROM job").fetchone()[0] == 0
    finally:
        again.close()


# ------------------------------------------------------------------------ tags --

@pytest.mark.parametrize("url", [
    "https://github.com/BugHunterX2101/principal-weak-repo",
    "https://github.com/BugHunterX2101/principal-weak-repo.git",
    "https://github.com/BugHunterX2101/principal-weak-repo/",
])
def test_baseline_tag_strips_git_suffix_and_lowercases(url):
    sha = "3FD067A7D5B8F0152B54E1922BE821FF1B2F786C"
    assert baseline_tag(url, sha) == "principal/principal-weak-repo:3fd067a7d5b8"


def test_baseline_tag_never_contains_an_at_sign_or_odd_characters():
    tag = baseline_tag("https://example.test/Some Org/My_Repo!v2", "a" * 40)
    assert "@" not in tag
    repo, _, sha = tag.partition(":")
    assert repo.startswith("principal/") and sha == "a" * 12
    assert all(c.isalnum() or c in "/_-" for c in repo)


def test_baseline_tag_falls_back_when_the_name_has_nothing_usable():
    assert baseline_tag("https://example.test/!!!", "b" * 40).startswith("principal/repo:")


# --------------------------------------------------------------------- scripts --

def test_baseline_script_installs_git_before_it_clones():
    script = baseline_script("https://example.test/r.git", "a" * 40)
    assert script.index("apt-get install") < script.index("git clone")


def test_baseline_script_fails_fast_with_a_distinct_exit_code():
    script = baseline_script("https://example.test/r.git", "a" * 40)
    assert script.count("exit 97") == 3
    assert "git clone failed" in script and "git checkout failed" in script


def test_a_diff_without_a_trailing_newline_gets_one():
    assert _with_newline("--- a\n+++ b") == "--- a\n+++ b\n"
    assert _with_newline("--- a\n+++ b\n") == "--- a\n+++ b\n"


# ----------------------------------------------------------------- sandbox choice --

def test_a_live_run_without_credentials_is_refused_not_downgraded(settings):
    bare = settings.model_copy(update={"nebius_api_key": None, "nebius_project_id": None})
    with pytest.raises(SystemExit, match="refusing to run"):
        _make_sandbox(bare, force_fake=False)


def test_one_missing_credential_is_still_refused(settings):
    half = settings.model_copy(update={"nebius_api_key": "k", "nebius_project_id": None})
    with pytest.raises(SystemExit, match="refusing to run"):
        _make_sandbox(half, force_fake=False)


def test_the_local_stand_in_is_used_only_when_asked_for(settings, capsys):
    bare = settings.model_copy(update={"nebius_api_key": None, "nebius_project_id": None})
    assert isinstance(_make_sandbox(bare, force_fake=True), FakeSandbox)
    assert "NOT live-verified" in capsys.readouterr().err


# ---------------------------------------------------------------------- tunables --

def test_the_smallest_tier_gets_a_smaller_output_budget_than_the_others():
    assert max_tokens_for(Tier.NANO) == 3000
    assert max_tokens_for(Tier.SUPER) == 10000


def test_the_configured_strength_floor_matches_the_gate_default(settings):
    assert settings.strength_floor == DEFAULT_MUTATION_FLOOR


# ---------------------------------------------------------------------- mutants --

def _module(n: int) -> str:
    return f"def f{n}(a, b):\n    if a > b:\n        return a + b\n    return a - b\n"


def test_more_files_than_the_cap_are_sampled_not_truncated_to_the_first_few():
    names = [f"m{i:02d}.py" for i in range(12)]
    baseline = {n: "" for n in names}
    patched = {n: _module(i) for i, n in enumerate(names)}

    planned = plan_mutants(baseline, patched, set(names), cap=4)

    assert 0 < len(planned) <= 4
    chosen = {p.path for p in planned}
    assert chosen != set(names[:4]), "sampling must not just take the alphabetical head"


def test_each_file_stays_within_its_share_of_the_budget():
    names = ["a.py", "b.py"]
    big = "".join(_module(i) for i in range(10))
    baseline = {n: "" for n in names}
    patched = {"a.py": big, "b.py": _module(99)}

    planned = plan_mutants(baseline, patched, set(names), cap=6)

    assert len(planned) <= 6
    assert {p.path for p in planned} == {"a.py", "b.py"}, "a large file must not starve the other"


def test_nothing_is_planned_for_unchanged_or_non_python_files():
    src = _module(1)
    assert plan_mutants({"a.py": src}, {"a.py": src}, {"a.py"}, cap=5) == []
    assert plan_mutants({}, {"a.js": "x = 1"}, {"a.js"}, cap=5) == []
    assert plan_mutants({}, {"a.py": src}, {"a.py"}, cap=0) == []
