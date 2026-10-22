"""The independent oracle that turns "the arm said verified" into a checked claim.

Runs the real oracle suite (tests/fixtures/weak_repo_oracle) in a scratch copy of
the real weak_repo fixture, so what is asserted here is the behaviour the
benchmark will rely on: an unchanged tree passes, a behaviour-preserving patch
passes, a behaviour-changing patch fails, and an oracle that finds no tests
fails rather than passing everything.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from bench.oracle import oracle_passes, patched_files
from principal.errors import DiffUnparseable

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
WEAK_REPO = FIXTURES / "weak_repo"
ORACLE = FIXTURES / "weak_repo_oracle"
PRICING = "src/pricing.py"


def _pricing() -> str:
    return (WEAK_REPO / PRICING).read_text(encoding="utf-8")


def _diff(path: str, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{path}", f"b/{path}",
    ))


def test_the_unchanged_repository_passes_its_own_oracle():
    assert oracle_passes(WEAK_REPO, {}, ORACLE)


def test_a_behaviour_preserving_patch_passes():
    patched = {PRICING: _pricing().replace("def calc_total(items, tax=TAX_RATE):",
                                           "def calc_total(items: list, tax: float = TAX_RATE) -> float:")}
    assert patched[PRICING] != _pricing()
    assert oracle_passes(WEAK_REPO, patched, ORACLE)


def test_a_patch_that_changes_the_tax_rate_is_rejected():
    patched = {PRICING: _pricing().replace("TAX_RATE = 0.2", "TAX_RATE = 0.25")}
    assert not oracle_passes(WEAK_REPO, patched, ORACLE)


def test_an_oracle_that_collects_no_tests_fails_instead_of_passing_everything(tmp_path):
    (tmp_path / "test_empty.py").write_text("# no tests here\n", encoding="utf-8")
    assert not oracle_passes(WEAK_REPO, {}, tmp_path)


def test_an_oracle_directory_without_test_files_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        oracle_passes(WEAK_REPO, {}, tmp_path)


def _job_with_winner(store, settings, *, diff: str, with_winner: bool = True):
    job = store.create_job(repo_url="x", commit_sha="y", goal="g", target_fqn=None,
                           token_budget=1, tunables={})
    task = store.create_task(job_id=job.id, seq=1, target_file=PRICING, instruction="i",
                             acceptance="a", depends_on=[])
    attempt = store.create_attempt(task_id=task.id, job_id=job.id, n=0, model="m", temperature=0.0,
                                   parent_image="img")
    store.put_artifact(job_id=job.id, kind="diff", rel_path="winner.diff", content=diff,
                       base_dir=settings.run_dir(job.id), attempt_id=attempt.id)
    if with_winner:
        store.update_task(task.id, winning_attempt_id=attempt.id)
    return job


def test_the_patched_tree_is_rebuilt_from_the_stored_winning_diff(store, settings):
    new = _pricing().replace("TAX_RATE = 0.2", "TAX_RATE = 0.25")
    job = _job_with_winner(store, settings, diff=_diff(PRICING, _pricing(), new))
    assert patched_files(store, settings, job.id, WEAK_REPO) == {PRICING: new}


def test_a_task_without_a_winning_attempt_contributes_nothing(store, settings):
    new = _pricing().replace("TAX_RATE = 0.2", "TAX_RATE = 0.25")
    job = _job_with_winner(store, settings, diff=_diff(PRICING, _pricing(), new), with_winner=False)
    assert patched_files(store, settings, job.id, WEAK_REPO) == {}


def test_a_winning_diff_that_does_not_apply_is_an_error_not_a_silent_baseline(store, settings):
    stale = _diff(PRICING, "TAX_RATE = 0.9\n", "TAX_RATE = 0.8\n")
    job = _job_with_winner(store, settings, diff=stale)
    with pytest.raises(DiffUnparseable):
        patched_files(store, settings, job.id, WEAK_REPO)
