"""The only holder of the GitHub credential.

Called exactly once, from the Publishing state, after the integration gate. It
takes the job and reads everything else from the store, so there is no call
signature that allows opening a PR from unverified data. The token is scoped to
a fork, never the upstream repository — not paranoia about the agent, which
cannot reach the token anyway, but insurance against a bug in this file writing
to a repository anyone cares about.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import httpx

from principal.config import Settings
from principal.db.store import Job
from principal.errors import Code, PrincipalError

GITHUB_API = "https://api.github.com"


async def _run(*args: str, cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    text = out.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise PrincipalError(Code.PUBLISH_FAILED, f"{' '.join(args[:2])} failed: {text[-800:]}")
    return text


async def open_pr(
    *, job: Job, title: str, body: str, diffs: list[str], settings: Settings,
) -> str | None:
    if not settings.github_token or not settings.principal_fork_repo:
        # Publishing is disabled, not broken. The job still succeeded: a
        # verification report exists, it just was not pushed anywhere.
        return None

    from principal.sandbox.baseline import ensure_snapshot

    snapshot = await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)
    with tempfile.TemporaryDirectory(prefix="principal-pr-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(snapshot, work, ignore=shutil.ignore_patterns(".principal-ok"))

        branch = f"principal/{job.id.lower()}"
        await _run("git", "checkout", "-b", branch, cwd=work)

        for i, diff_text in enumerate(diffs):
            patch_path = Path(tmp) / f"p{i}.diff"
            patch_path.write_text(diff_text, encoding="utf-8")
            await _run("git", "apply", "--whitespace=nowarn", str(patch_path), cwd=work)

        await _run("git", "add", "-A", cwd=work)
        await _run("git", "commit", "--quiet", "-m", title, cwd=work)

        fork_url = f"https://x-access-token:{settings.github_token}@github.com/{settings.principal_fork_repo}.git"
        await _run("git", "remote", "add", "principal-fork", fork_url, cwd=work)
        await _run("git", "push", "--force", "principal-fork", f"HEAD:{branch}", cwd=work)

    return await _create_pr(job, title, body, branch, settings)


async def _create_pr(job: Job, title: str, body: str, branch: str, settings: Settings) -> str:
    fork_owner = settings.principal_fork_repo.split("/", 1)[0]
    upstream = job.repo_url.rstrip("/").removesuffix(".git")
    upstream_owner_repo = "/".join(upstream.split("/")[-2:])

    async with httpx.AsyncClient(
        base_url=GITHUB_API, headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
        }, timeout=30.0,
    ) as client:
        repo_resp = await client.get(f"/repos/{upstream_owner_repo}")
        default_branch = "main"
        if repo_resp.status_code < 400:
            default_branch = repo_resp.json().get("default_branch", "main")

        resp = await client.post(
            f"/repos/{upstream_owner_repo}/pulls",
            json={
                "title": title, "body": body, "draft": True,
                "head": f"{fork_owner}:{branch}", "base": default_branch,
            },
        )
        if resp.status_code >= 400:
            raise PrincipalError(Code.PUBLISH_FAILED, f"GitHub API {resp.status_code}: {resp.text[:400]}")
        return resp.json()["html_url"]
