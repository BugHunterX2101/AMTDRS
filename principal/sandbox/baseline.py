"""C0: the green baseline, plus the coverage map.

Everything downstream depends on this phase producing two artifacts: a tagged
green checkpoint and a coverage map. Aborting on an already-red suite is the
cheapest guardrail in the system — a repository whose tests do not pass before
Principal touches it makes every downstream claim meaningless, and finding out at
the start costs one test run that was going to happen anyway.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from principal.errors import Code, PrincipalError
from principal.sandbox.client import SandboxClient
from principal.sandbox.scripts import (
    SENTINEL_COLLECT,
    SENTINEL_COVERAGE,
    SENTINEL_REPORT,
    baseline_script,
    collected_count,
    extract,
)


@dataclass(slots=True)
class Baseline:
    image: str
    tag: str
    coverage_json: str
    report_json: str
    collected: int | None
    duration_ms: int
    passed: int = 0
    failed: int = 0
    operation_id: str | None = None


def baseline_tag(repo_url: str, commit_sha: str) -> str:
    slug = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    return f"principal/{slug}@{commit_sha[:12]}"


async def ensure_snapshot(repo_url: str, commit_sha: str, dest_root: Path) -> Path:
    """A local copy of the tree at this commit, for parsing and for gates 1 and 2.

    Most bad candidates die at the scope or syntax gate, which run against this
    snapshot and never touch Nebius. That is the real reason generous parallelism
    is affordable.
    """
    dest = dest_root / f"{_slug(repo_url)}@{commit_sha[:12]}"
    if (dest / ".principal-ok").exists():
        return dest

    local = Path(repo_url)
    if local.exists() and local.is_dir():
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(local, dest, ignore=shutil.ignore_patterns(".git", ".venv", "node_modules"))
        if not (dest / ".git").exists():
            # Publishing and the gates' local git operations both assume a real
            # repo. A fixture copied straight off disk has no history, so give
            # it one commit to apply diffs and open PRs against, uniformly with
            # the cloned-from-remote path below.
            await _git("-C", str(dest), "init", "--quiet", "-b", "main")
            await _git("-C", str(dest), "config", "user.email", "principal@localhost")
            await _git("-C", str(dest), "config", "user.name", "Principal")
            await _git("-C", str(dest), "add", "-A")
            await _git("-C", str(dest), "commit", "--quiet", "-m", f"snapshot at {commit_sha}")
            # A local fixture has no real commit history, so the caller's
            # commit_sha is not an actual git object. Tagging the one commit
            # with it lets `git checkout <commit_sha>` inside the sandbox script
            # work the same way it would against a real cloned remote.
            await _git("-C", str(dest), "tag", "-f", commit_sha)
        (dest / ".principal-ok").write_text(commit_sha, encoding="utf-8")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    await _git("clone", "--filter=blob:none", "--quiet", repo_url, str(dest))
    await _git("-C", str(dest), "checkout", "--quiet", commit_sha)
    (dest / ".principal-ok").write_text(commit_sha, encoding="utf-8")
    return dest


def clone_source(repo_url: str, snapshot: Path) -> str:
    """What the sandbox's `git clone` should actually point at.

    A remote URL is cloned directly: the sandbox is a microVM with network access
    but no view of this host's filesystem, so a local path would be meaningless
    there.

    A local path must not be. The bundled fixture is plain files inside this
    repository with no history of its own, so `git clone tests/fixtures/mini_repo`
    fails, the following `cd /work` fails with it, and pytest then runs in the
    wrong directory, collects nothing and exits 5 — which surfaces as BASELINE_RED
    and reads as "your repository's tests are broken". `ensure_snapshot` has
    already turned that directory into a real single-commit repo tagged with
    `commit_sha`, which is exactly what the script expects, so clone from there.
    """
    return str(snapshot) if Path(repo_url).is_dir() else repo_url


async def _git(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    text = out.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise PrincipalError(
            Code.BASELINE_INSTALL_FAILED, f"git {' '.join(args[:2])} failed: {text[-400:]}"
        )
    return text


def _slug(repo_url: str) -> str:
    return repo_url.rstrip("/").replace("\\", "/").split("/")[-1].removesuffix(".git") or "repo"


async def build_baseline(
    sandbox: SandboxClient,
    *,
    base_image: str,
    repo_url: str,
    commit_sha: str,
    timeout_s: int = 900,
    install_cmd: str | None = None,
    on_operation=None,
    clone_url: str | None = None,
) -> Baseline:
    """`repo_url` names the repository — it is what the C0 tag is derived from and
    what every downstream artifact refers to. `clone_url` is where the sandbox
    fetches it from, which differs for a local path (see `clone_source`). Keeping
    them separate stops a snapshot directory's name leaking into the tag."""
    image = await sandbox.use(base_image)
    script = baseline_script(clone_url or repo_url, commit_sha, install_cmd=install_cmd)
    tag = baseline_tag(repo_url, commit_sha)

    result = await sandbox.run(
        image, script, disposable=False, timeout_s=timeout_s, on_operation=on_operation, tag=tag
    )

    coverage = extract(result.stdout, SENTINEL_COVERAGE)
    report = extract(result.stdout, SENTINEL_REPORT)
    collected = collected_count(extract(result.stdout, SENTINEL_COLLECT))
    passed, failed = _summary(report)

    if result.image is None:
        raise PrincipalError(Code.BASELINE_INSTALL_FAILED, "baseline run produced no image")

    if result.exit_code != 0 or failed:
        raise PrincipalError(
            Code.BASELINE_RED,
            f"baseline suite is not green ({failed} failing, exit {result.exit_code}). "
            "Principal will not work on a repository whose tests do not pass first.",
            detail_tail=result.stdout[-1500:],
        )

    return Baseline(
        image=result.image,
        tag=tag,
        coverage_json=coverage,
        report_json=report,
        collected=collected,
        duration_ms=result.duration_ms,
        passed=passed,
        failed=failed,
        operation_id=result.operation_id,
    )


def _summary(report_json: str) -> tuple[int, int]:
    try:
        data = json.loads(report_json or "{}")
    except json.JSONDecodeError:
        return (0, 0)
    summary = data.get("summary", {}) or {}
    return int(summary.get("passed", 0) or 0), int(summary.get("failed", 0) or 0) + int(
        summary.get("error", 0) or 0
    )
