"""Spike: does this credential actually have Sandboxes access, separately from
inference, and does content-addressing behave the way the architecture assumes?

Sandbox access is granted per *project*, not per key (docs/FEEDBACK.md, item 3),
so a working `NEBIUS_API_KEY` proves nothing about whether `NEBIUS_PROJECT_ID`
can use Sandboxes at all. This script answers that question directly and cheaply
— one trivial disposable run — before anything downstream discovers it as a bare
403 in the middle of a job.

It then demonstrates the two properties the whole architecture depends on:
running the same command against the same parent image returns the same UUID
(dedup is free), and the operation id can actually be captured for cancellation
(see the `_install_operation_probe` monkeypatch in principal/sandbox/client.py,
and item #1 in docs/FEEDBACK.md for why that should not be necessary).

    python -m spikes.spike_sandbox
"""

from __future__ import annotations

import asyncio
import sys
import time

from principal.config import get_settings


async def main() -> int:
    settings = get_settings()
    if not settings.nebius_api_key:
        print("NEBIUS_API_KEY is not set.", file=sys.stderr)
        return 1
    if not settings.nebius_project_id:
        print("NEBIUS_PROJECT_ID is not set. Sandboxes cannot be probed without it — "
              "this is exactly the trap: inference alone tells you nothing here.", file=sys.stderr)
        return 1

    from principal.sandbox.client import ContreeSandbox

    print(f"probing sandbox access for project {settings.nebius_project_id}")
    print(f"endpoint: {settings.principal_sandbox_base}\n")

    sandbox = ContreeSandbox(
        api_key=settings.sandbox_key, project_id=settings.nebius_project_id,
        base_url=settings.principal_sandbox_base, max_inflight=settings.max_inflight_ops,
    )

    # 1. Basic reachability: one trivial disposable run.
    t0 = time.monotonic()
    try:
        image = await sandbox.use(settings.principal_base_image)
        result = await sandbox.run(image, "echo principal-spike-ok", disposable=True, timeout_s=120)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        print("\nIf this is a 403: Sandboxes are not enabled for this project. "
              "A working inference key does not imply sandbox access — request it "
              "separately in the Token Factory console.")
        return 1

    elapsed = time.monotonic() - t0
    ok = result.exit_code == 0 and "principal-spike-ok" in result.stdout
    print(f"[{'OK' if ok else 'FAIL'}] round trip in {elapsed:.2f}s, exit {result.exit_code}")
    print(f"  operation_id captured: {result.operation_id!r}")
    if not ok:
        return 1

    # 2. Content addressing: same command, same parent -> same image UUID.
    base = await sandbox.use(settings.principal_base_image)
    a = await sandbox.run(base, "echo dedup-check > /tmp/marker.txt", disposable=False, timeout_s=60)
    b = await sandbox.run(base, "echo dedup-check > /tmp/marker.txt", disposable=False, timeout_s=60)
    dedup_ok = a.image is not None and a.image == b.image
    print(f"\n[{'OK' if dedup_ok else 'FAIL'}] content-addressed dedup: "
          f"{a.image} {'==' if dedup_ok else '!='} {b.image}")

    # 3. Cancellation: start a slow run, cancel it, confirm the client accepts
    # the cancellation without raising. (Confirming the *remote* operation
    # actually stopped requires a longer-running probe than a spike should be;
    # this checks the mechanism the orchestrator's candidate race relies on.)
    op_ids: list[str] = []
    slow = asyncio.create_task(
        sandbox.run(base, "sleep 20", disposable=True, timeout_s=60, on_operation=op_ids.append)
    )
    await asyncio.sleep(1.0)
    cancel_ok = True
    if op_ids:
        try:
            await sandbox.cancel(op_ids[0])
        except Exception as exc:  # noqa: BLE001
            cancel_ok = False
            print(f"\n[FAIL] cancel() raised: {exc}")
    slow.cancel()
    print(f"[{'OK' if cancel_ok and op_ids else 'FAIL'}] operation id captured and cancel() accepted "
          f"(op_ids={op_ids})")

    print("\nsandbox access confirmed." if ok and dedup_ok else "\nsandbox probe found problems above.")
    return 0 if (ok and dedup_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
