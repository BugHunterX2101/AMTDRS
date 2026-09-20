"""The characterisation test never reached the published PR.

`_characterise` writes the generated safety-net test straight to a sandbox
filesystem via a shell script and re-baselines the job onto that image — that
is what gate 3 needs to run it against every subsequent candidate. Neither
step produces a diff. `open_pr` builds the PR branch from a pristine,
uncharacterised checkout and applies diffs to it; without a diff, a job that
proved a generated test was good enough to trust would still ship a PR that
does not contain it — the exact thing rule 3 of characterise.py's own design
("frozen before any refactoring starts") was supposed to guarantee happened.

These pin `_new_file_diff` (the diff itself must be real and applicable) and
`_characterisation_diff` (rediscovering it after the fact must work, and must
stay silent when characterisation never ran for the job at all).
"""

from __future__ import annotations

from pathlib import Path

import unidiff

from principal.config import Settings
from principal.db.store import Store
from principal.orchestrator.job import _characterisation_diff, _new_file_diff
from tests.conftest import FIXTURE_SHA, MINI_REPO

TEST_SOURCE = (
    "from src.auth.internal import _normalise_ttl\n\n\n"
    "def test_normalise_ttl_is_stable():\n"
    "    assert _normalise_ttl(5) == _normalise_ttl(5)\n"
)


def test_new_file_diff_parses_as_a_pure_addition():
    text = _new_file_diff("tests/principal_characterisation/test_x.py", TEST_SOURCE)
    patch = unidiff.PatchSet(text)
    assert len(patch) == 1
    f = patch[0]
    assert f.is_added_file
    assert f.path == "tests/principal_characterisation/test_x.py"
    added = "".join(line.value for hunk in f for line in hunk if line.is_added)
    assert added == TEST_SOURCE


def test_new_file_diff_handles_content_with_no_trailing_newline():
    text = _new_file_diff("x.py", "def f(): pass")
    patch = unidiff.PatchSet(text)
    assert len(patch) == 1
    assert patch[0].is_added_file


async def test_characterisation_diff_reconstructs_the_real_test_file(
    store: Store, settings: Settings, snapshot: Path,
):
    from principal.graph.build import build

    stats = build(store, snapshot, str(MINI_REPO), FIXTURE_SHA)
    graph_id = stats.graph_id

    job = store.create_job(
        repo_url=str(MINI_REPO), commit_sha=FIXTURE_SHA, goal="g",
        target_fqn="src.auth.internal._normalise_ttl", token_budget=100000, tunables={},
    )

    test_path = "tests/principal_characterisation/test_characterise_src_auth_internal__normalise_ttl.py"
    sid = store.q1(
        "SELECT id FROM symbol WHERE graph_id = ? AND fqn = ?",
        (graph_id, "src.auth.internal._normalise_ttl"),
    )["id"]
    sha = "0" * 64
    cur = store.exec(
        "INSERT INTO file (graph_id, path, lang, sha256, is_test) VALUES (?,?,?,?,1)",
        (graph_id, test_path, "python", sha),
    )
    tfid = cur.lastrowid
    store.exec(
        "INSERT INTO test_edge (graph_id, test_file_id, symbol_id, nodeid, source)"
        " VALUES (?,?,?,?,'characterisation')",
        (graph_id, tfid, sid, f"{test_path}::test_normalise_ttl_is_stable"),
    )
    store.put_artifact(
        job_id=job.id, kind="characterisation", rel_path=test_path.replace("/", "_") + ".py",
        content=TEST_SOURCE, base_dir=settings.run_dir(job.id),
    )

    diff = _characterisation_diff(store, settings, job.id, graph_id, job.target_fqn)
    assert diff is not None
    patch = unidiff.PatchSet(diff)
    assert patch[0].path == test_path
    added = "".join(line.value for hunk in patch[0] for line in hunk if line.is_added)
    assert added == TEST_SOURCE


async def test_characterisation_diff_is_none_when_the_job_never_ran_it(
    store: Store, settings: Settings, snapshot: Path,
):
    """The overwhelmingly common case — a well-tested target never triggers
    characterisation at all — must cost nothing and add nothing to the PR."""
    from principal.graph.build import build

    stats = build(store, snapshot, str(MINI_REPO), FIXTURE_SHA)
    graph_id = stats.graph_id
    job = store.create_job(
        repo_url=str(MINI_REPO), commit_sha=FIXTURE_SHA, goal="g",
        target_fqn="src.auth.session.create", token_budget=100000, tunables={},
    )

    assert _characterisation_diff(store, settings, job.id, graph_id, job.target_fqn) is None


async def test_characterisation_diff_is_none_for_an_empty_target_fqn(store: Store, settings: Settings):
    assert _characterisation_diff(store, settings, "job-1", "graph-1", "") is None
