"""Content addressing, which is the property the whole architecture leans on.

If these fail, the claims about paying the install once per job and about
crash recovery being free are no longer true — and the FakeSandbox has stopped
being a faithful stand-in for the real one, so every other test that uses it is
measuring the wrong thing.
"""

from __future__ import annotations


async def test_same_command_from_same_parent_yields_same_image(sandbox):
    """Deduplication is the platform's, not ours. Repeating work is free, which
    is why crash recovery can simply re-run the baseline command."""
    base = await sandbox.use("python:3.11-slim")
    a = await sandbox.run(base, "echo hello > marker.txt", disposable=False)
    b = await sandbox.run(base, "echo hello > marker.txt", disposable=False)
    assert a.image is not None
    assert a.image == b.image


async def test_different_commands_diverge(sandbox):
    base = await sandbox.use("python:3.11-slim")
    a = await sandbox.run(base, "echo one > marker.txt", disposable=False)
    b = await sandbox.run(base, "echo two > marker.txt", disposable=False)
    assert a.image != b.image


async def test_forks_do_not_see_each_others_writes(sandbox):
    """'Failure is free because it is private.' A candidate that corrupts its
    tree corrupts only its own fork — this is what makes racing three candidates
    reasonable rather than reckless."""
    base = await sandbox.use("python:3.11-slim")
    c0 = await sandbox.run(base, "mkdir -p work && echo baseline > work/state.txt", disposable=False)
    parent = await sandbox.from_uuid(c0.image)

    left = await sandbox.run(parent, "echo LEFT > work/state.txt && cat work/state.txt",
                             disposable=False)
    right = await sandbox.run(parent, "cat work/state.txt", disposable=False)

    assert "LEFT" in left.stdout
    assert "baseline" in right.stdout
    assert "LEFT" not in right.stdout


async def test_disposable_run_produces_no_image(sandbox):
    base = await sandbox.use("python:3.11-slim")
    assert (await sandbox.run(base, "echo transient", disposable=True)).image is None


async def test_operation_id_is_reported_to_the_caller(sandbox):
    """Racing candidates means cancelling the losers, and cancelling a local
    task does not cancel remote work. Without the operation id there is no
    handle to cancel with."""
    seen: list[str] = []
    base = await sandbox.use("python:3.11-slim")
    result = await sandbox.run(base, "echo x", disposable=True, on_operation=seen.append)
    assert seen and seen[0] == result.operation_id


async def test_exit_code_is_propagated_faithfully(sandbox):
    """Gate 3's verdict is this number. Swallowing or remapping it would put a
    judgement call where a measurement belongs."""
    base = await sandbox.use("python:3.11-slim")
    assert (await sandbox.run(base, "exit 0", disposable=True)).exit_code == 0
    assert (await sandbox.run(base, "exit 3", disposable=True)).exit_code == 3


async def test_tag_resolves_back_to_the_image(sandbox):
    base = await sandbox.use("python:3.11-slim")
    result = await sandbox.run(base, "echo tagged", disposable=False, tag="principal/demo@abc")
    assert (await sandbox.from_uuid("principal/demo@abc")).uuid == result.image
