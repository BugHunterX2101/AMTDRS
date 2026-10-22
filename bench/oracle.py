"""An independent oracle, to catch a patch that an arm accepted and should not have.

Every arm's `verified` is a claim: arm A claims it when the winning tests were
green, arms B and C when the product's own classification says VERIFIED. Both
rest on the repository's own tests, and a repository with weak tests can pass
them with a patch that changed behaviour. The scorer cannot see that from inside
the run, so a task may name a test directory the pipeline never saw (it is not in
the tree that is cloned, graphed, tested or mutated), and a claimed-verified
patch is then run against it. A claim the oracle rejects is a false accept.

The patched tree is rebuilt from the winning diffs the run stored, in task order,
and the oracle runs on the host in a scratch copy with a minimal environment: no
Nebius or GitHub credentials reach it. That is a limit worth stating. It is code
produced by a model, run outside the sandbox. It is acceptable for the small
fixture repositories the benchmark uses, and it is the reason this is not a place
to point at an arbitrary repository.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import unidiff

from principal.config import Settings
from principal.db.store import Store
from principal.gates.apply import apply_to_text

_TIMEOUT_S = 300
_KEEP_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")


def patched_files(store: Store, settings: Settings, job_id: str, snapshot: Path) -> dict[str, str]:
    """Repo-relative path -> file text after every winning diff, in task order.

    Raises `DiffUnparseable` when a stored winning diff does not apply, rather than
    leaving that file at its baseline text: an oracle run on a tree that is not the
    one the arm produced would say nothing about the arm.
    """
    files: dict[str, str] = {}
    for task in store.tasks_of(job_id):
        if not task.winning_attempt_id:
            continue
        art = store.q1(
            "SELECT * FROM artifact WHERE attempt_id = ? AND kind = 'diff' ORDER BY rowid DESC LIMIT 1",
            (task.winning_attempt_id,),
        )
        if art is None:
            continue
        diff = (settings.run_dir(job_id) / art["path"]).read_text(encoding="utf-8")
        parsed = list(unidiff.PatchSet(diff))
        if not parsed:
            continue
        current = files.get(task.target_file)
        if current is None:
            current = (snapshot / task.target_file).read_text(encoding="utf-8")
        files[task.target_file] = apply_to_text(current, parsed[0]).new_text
    return files


def oracle_passes(snapshot: Path, patched: dict[str, str], oracle_dir: Path) -> bool:
    """True only if the oracle collected tests and every one passed.

    Collecting nothing is a failure, not a pass: an oracle that quietly stopped
    finding its tests would otherwise report every patch as correct.
    """
    tests = sorted(oracle_dir.glob("test_*.py"))
    if not tests:
        raise FileNotFoundError(f"no test_*.py in oracle directory {oracle_dir}")
    with tempfile.TemporaryDirectory(prefix="principal-oracle-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(snapshot, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for rel, text in patched.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
        for test in tests:
            shutil.copy(test, work / test.name)
        env = {k: os.environ[k] for k in _KEEP_ENV if k in os.environ}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            # Fixed argv: this interpreter, fixed flags, and file names globbed from the
            # oracle directory the maintainer named in tasks.json. No shell, no model text.
            proc = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *[t.name for t in tests]],
                cwd=work, env=env, capture_output=True, timeout=_TIMEOUT_S, check=False,
            )
        except subprocess.TimeoutExpired:
            return False
    return proc.returncode == 0
