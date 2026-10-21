"""The fake sandbox has to behave like the platform it stands in for.

Three properties matter to the tests that lean on it, and each was a way for the
fake to report a failure that belonged to the fake:

  the same command from the same parent is one execution, however often it is
  asked for — otherwise a retried patch "does not apply" to a tree it already
  modified;
  a command that outlives its timeout is killed, not abandoned;
  a cancelled run leaves nothing behind that a later identical run could inherit.
"""

from __future__ import annotations

import asyncio
import time

import pytest


async def test_identical_runs_execute_once(sandbox):
    base = await sandbox.use("python:3.11-slim")
    script = "echo x >> marker.txt; cat marker.txt"

    first = await sandbox.run(base, script, disposable=False)
    second = await sandbox.run(base, script, disposable=False)

    # A second execution would have appended again and printed two lines.
    assert first.stdout.split() == ["x"]
    assert second.stdout.split() == ["x"]
    assert first.image == second.image
    assert first.operation_id != second.operation_id, "each request still gets its own operation id"


async def test_concurrent_identical_runs_share_one_execution(sandbox):
    """Three racing candidates that emit the same diff are the realistic case."""
    base = await sandbox.use("python:3.11-slim")
    script = "echo y >> marker.txt; cat marker.txt"

    results = await asyncio.gather(*(sandbox.run(base, script, disposable=False) for _ in range(3)))

    assert [r.stdout.split() for r in results] == [["y"]] * 3
    assert len({r.image for r in results}) == 1


async def test_a_run_that_outlives_its_timeout_is_reported_and_killed(sandbox):
    base = await sandbox.use("python:3.11-slim")
    started = time.monotonic()
    result = await sandbox.run(
        base, "touch started; sleep 3; touch finished", disposable=False, timeout_s=1,
    )

    assert result.timed_out
    assert result.exit_code == 124
    assert time.monotonic() - started < 3, "the caller should not have waited out the script"

    # The script was killed, so the marker it would have written never appears.
    await asyncio.sleep(3.5)
    image = await sandbox.from_uuid(result.image)
    assert (image.workdir / "started").exists()
    assert not (image.workdir / "finished").exists(), "the timed-out script kept running"


async def test_a_cancelled_run_is_killed_and_leaves_no_debris(sandbox):
    base = await sandbox.use("python:3.11-slim")
    script = "touch started; sleep 3; touch finished"

    task = asyncio.create_task(sandbox.run(base, script, disposable=False))
    await asyncio.sleep(0.7)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(3.5)
    result_dirs = [p for p in sandbox.root.iterdir() if (p / "started").exists() or (p / "finished").exists()]
    assert not result_dirs, "the cancelled run's directory should have been discarded"

    # And the same command asked for again runs cleanly from the parent.
    again = await sandbox.run(base, "touch started; echo ok", disposable=False)
    assert again.exit_code == 0
    assert "ok" in again.stdout


def test_the_fake_labels_its_provenance(sandbox):
    assert sandbox.execution_mode == "local"


# -- the /work and /tmp/ rewrite ------------------------------------------------
#
# The platform has a real /work. A machine running the fake does not (a Linux CI
# user cannot create it, Git-for-Windows bash resolves it somewhere unwritable),
# so the scripts are rewritten to per-run directories before they execute.


def test_standalone_sandbox_paths_are_pointed_at_the_run_directory():
    from principal.sandbox.fake import _localise_paths

    script = "cd /work && echo x > /tmp/cov.json && pytest --cov-report=json:/tmp/cov.json"
    out = _localise_paths(script, "/runs/a/work", "/runs/a/tmp")

    assert out == (
        "cd /runs/a/work && echo x > /runs/a/tmp/cov.json"
        " && pytest --cov-report=json:/runs/a/tmp/cov.json"
    )


def test_a_substituted_path_is_never_rewritten_a_second_time():
    from principal.sandbox.fake import _localise_paths

    # A pytest temp directory on Linux is itself under /tmp/. Two sequential
    # replaces turned the /work path into a mangled one on exactly that machine.
    work = "/tmp/pytest-of-runner/pytest-0/run/work"
    out = _localise_paths("cd /work", work, "/tmp/pytest-of-runner/pytest-0/run/tmp")

    assert out == f"cd {work}"


def test_the_clone_source_is_the_callers_path_not_sandbox_scratch():
    from principal.sandbox.fake import _localise_paths

    # On Linux a pytest repository lives under /tmp/. It is the clone's source, so
    # it must survive; only the clone's destination (/work) is the sandbox's.
    script = "git clone --filter=blob:none /tmp/pytest-of-u/repo /work 2>&1 | tail -3\ncat /tmp/cov.json"
    out = _localise_paths(script, "/runs/a/work", "/runs/a/tmp")

    assert out == (
        "git clone --filter=blob:none /tmp/pytest-of-u/repo /runs/a/work 2>&1 | tail -3\n"
        "cat /runs/a/tmp/cov.json"
    )


@pytest.mark.parametrize("text", [
    "src/workers/cleanup.py",
    "/workspace/project",
    "/workflow/run",
    "https://example.test/tmp/readme",
])
def test_look_alike_paths_are_left_alone(text):
    from principal.sandbox.fake import _localise_paths

    assert _localise_paths(f"cat {text}", "/w", "/t") == f"cat {text}"
