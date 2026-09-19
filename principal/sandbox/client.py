"""Contree wrapper.

Contree has one primitive and it is not the one the PRD assumed. An image handle
plus a command produces a new image. That is the checkpoint, the fork and the
result, all at once:

    Baseline C0          the image returned by the run that installed deps and passed
    Forking a candidate  calling run() on the C0 image again
    Checkpoint           any image UUID from a run with disposable=False
    Discarding           doing nothing; the image is simply never referenced again
    Rollback             using the parent UUID, which never changed

Discarding costs nothing because nothing was mutated. There is no undo path in
Principal because there is no mutation to undo.

`run` is the only place an operation id is created, and it writes that id to the
attempt row BEFORE awaiting. A version of this function that awaits first and
records afterwards looks identical and loses every in-flight operation on restart.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from principal.errors import SandboxForbidden, SandboxOpFailed, SandboxTimeout

# Set by run() so the SDK's operation start can be observed without the SDK
# exposing it. The alternative is losing the id, and with it cancellation and
# crash recovery.
_OP_SINK: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "principal_op_sink", default=None
)


@dataclass(slots=True)
class RunResult:
    image: str | None
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    operation_id: str | None = None
    cost: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@runtime_checkable
class SandboxClient(Protocol):
    async def use(self, ref: str) -> Any: ...
    async def from_uuid(self, uuid: str) -> Any: ...
    async def run(
        self, image: Any, script: str, *, disposable: bool = False, timeout_s: int = 600,
        attempt_id: str | None = None, on_operation: Callable[[str], None] | None = None,
        tag: str | None = None, env: dict[str, str] | None = None,
    ) -> RunResult: ...
    async def cancel(self, operation_id: str) -> None: ...
    async def tag(self, image: Any, tag: str) -> Any: ...


class ContreeSandbox:
    """The real client. Every sandbox side effect in the system goes through here."""

    def __init__(self, api_key: str, project_id: str, base_url: str | None = None,
                 max_inflight: int = 24):
        from contree_sdk import Contree
        from contree_sdk.auth import IAMAuth
        from contree_sdk.config import ContreeConfig

        auth = IAMAuth(token=api_key, project_id=project_id)
        if base_url:
            auth.base_url = base_url
        self._client = Contree(ContreeConfig(auth=auth))
        self._sem = asyncio.Semaphore(max_inflight)
        self._install_operation_probe()

    def _install_operation_probe(self) -> None:
        original = self._client._start_operation

        async def probed(request):  # noqa: ANN001
            op = await original(request)
            sink = _OP_SINK.get()
            if sink is not None:
                sink(str(op))
            return op

        self._client._start_operation = probed  # type: ignore[method-assign]

    async def use(self, ref: str) -> Any:
        try:
            return await self._client.images.oci(ref)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    async def from_uuid(self, uuid: str) -> Any:
        try:
            return await self._client.images.use(uuid)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    async def run(
        self, image: Any, script: str, *, disposable: bool = False, timeout_s: int = 600,
        attempt_id: str | None = None, on_operation: Callable[[str], None] | None = None,
        tag: str | None = None, env: dict[str, str] | None = None,
    ) -> RunResult:
        del attempt_id  # the caller binds it into on_operation
        op_holder: dict[str, str] = {}

        def sink(op: str) -> None:
            op_holder["id"] = op
            if on_operation is not None:
                on_operation(op)

        token = _OP_SINK.set(sink)
        started = time.monotonic()
        try:
            async with self._sem:
                prepared = image.run(
                    shell=script,
                    disposable=disposable,
                    timeout=timeout_s,
                    tag=tag,
                    env=env or {},
                )
                try:
                    done = await asyncio.wait_for(prepared, timeout=timeout_s + 60)
                except TimeoutError as exc:
                    await self._cancel_quietly(op_holder.get("id"))
                    raise SandboxTimeout(timeout_s + 60) from exc
                except asyncio.CancelledError:
                    # A losing candidate in the race. Cancelling the coroutine has to
                    # cancel its operation too, or the job holds slots it no longer uses.
                    await self._cancel_quietly(op_holder.get("id"))
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise _translate(exc) from exc
        finally:
            _OP_SINK.reset(token)

        result = done.result
        return RunResult(
            image=str(done.uuid) if done.uuid else None,
            exit_code=int(result.exit_code),
            stdout=_as_text(result.stdout),
            stderr=_as_text(result.stderr),
            duration_ms=int((time.monotonic() - started) * 1000),
            operation_id=op_holder.get("id"),
            cost=float(getattr(result, "cost", 0.0) or 0.0),
        )

    async def cancel(self, operation_id: str) -> None:
        from uuid import UUID

        await self._client._cancel_operation(UUID(operation_id))

    async def _cancel_quietly(self, operation_id: str | None) -> None:
        if not operation_id:
            return
        try:
            await self.cancel(operation_id)
        except Exception:  # noqa: BLE001, S110
            pass

    async def tag(self, image: Any, tag: str) -> Any:
        # Untagged unreferenced images are eligible for collection under the beta
        # retention policy. A baseline that disappears between the benchmark run
        # and the demo recording would be an unpleasant surprise.
        try:
            return await image.tag_as(tag)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    async def probe(self) -> str:
        """One trivial disposable run. A 403 here exits at startup rather than
        halfway through a job, when the cost of discovering it is highest."""
        image = await self.use("alpine:3.20")
        result = await self.run(image, "echo principal-probe", disposable=True, timeout_s=120)
        if not result.ok:
            raise SandboxOpFailed(f"probe exited {result.exit_code}: {result.stderr[:200]}")
        return result.stdout.strip()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    read = getattr(value, "read", None)
    if callable(read):
        data = read()
        return data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    return str(value)


def _translate(exc: Exception) -> Exception:
    from contree_sdk.sdk.exceptions import (
        ApiTimeoutError,
        ForbiddenError,
        OperationTimedOutError,
    )

    if isinstance(exc, ForbiddenError):
        return SandboxForbidden(str(exc))
    if isinstance(exc, (ApiTimeoutError, OperationTimedOutError)):
        return SandboxTimeout(0)
    if isinstance(exc, (SandboxForbidden, SandboxTimeout, SandboxOpFailed)):
        return exc
    return SandboxOpFailed(f"{type(exc).__name__}: {exc}")
