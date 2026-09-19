"""FakeSandbox: SandboxClient implemented in memory with real image-tree semantics.

Getting content-addressed deduplication right in the fake is what lets the
crash-recovery and deduplication paths be tested without spending money, and
what lets the API run locally with no Nebius credentials at all — against the
mini_repo fixture, using a real subprocess `git` and `pytest` inside THIS
machine's Python rather than a microVM. It is a stand-in for the sandbox
boundary, not a mock of git or pytest: the commands it runs are real.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from principal.sandbox.client import RunResult


@dataclass(slots=True)
class FakeImage:
    uuid: str
    workdir: Path


def _content_hash(parent: str, script: str) -> str:
    # Running the same command twice from the same parent returns the same
    # UUID: the state graph is content-addressed and repeated work is
    # deduplicated for free. This is the property the crash-recovery and cache
    # design depend on, so the fake reproduces it exactly.
    return hashlib.sha256(f"{parent}:{script}".encode()).hexdigest()[:32]


class FakeSandbox:
    def __init__(self, root: Path | None = None):
        import tempfile

        self.root = root or Path(tempfile.mkdtemp(prefix="principal-fake-sandbox-"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._images: dict[str, FakeImage] = {}
        self._op_counter = 0
        self._cancelled: set[str] = set()

    async def use(self, ref: str) -> FakeImage:
        uuid = _content_hash("base", ref)
        if uuid not in self._images:
            d = self.root / uuid
            d.mkdir(parents=True, exist_ok=True)
            self._images[uuid] = FakeImage(uuid=uuid, workdir=d)
        return self._images[uuid]

    async def from_uuid(self, uuid: str) -> FakeImage:
        if uuid in self._images:
            return self._images[uuid]
        d = self.root / uuid
        d.mkdir(parents=True, exist_ok=True)
        img = FakeImage(uuid=uuid, workdir=d)
        self._images[uuid] = img
        return img

    async def run(
        self, image: Any, script: str, *, disposable: bool = False, timeout_s: int = 600,
        attempt_id: str | None = None, on_operation: Callable[[str], None] | None = None,
        tag: str | None = None, env: dict[str, str] | None = None,
    ) -> RunResult:
        del attempt_id
        self._op_counter += 1
        op_id = f"fake-op-{self._op_counter}"
        if on_operation is not None:
            on_operation(op_id)

        parent: FakeImage = image
        result_uuid = _content_hash(parent.uuid, script)
        result_dir = self.root / result_uuid
        if not result_dir.exists():
            import shutil

            shutil.copytree(parent.workdir, result_dir)

        started = time.monotonic()
        try:
            bash = _shell()
            run_env = _local_dev_env(env)
            if bash:
                # scripts.py hardcodes /work as an absolute POSIX path, which is
                # correct for the real sandbox (a Linux microVM). Git-for-Windows
                # bash resolves a bare /work against its own install root
                # (typically C:\Program Files\Git), which this process cannot
                # write to. This substitution is a Windows-local-dev-only shim:
                # it never runs against the real Contree sandbox, which has an
                # actual /work mount and needs no rewriting.
                if os.name == "nt":
                    work_dir = result_dir / "work"
                    work_dir.mkdir(parents=True, exist_ok=True)
                    script = script.replace("/work", _to_msys_path(work_dir))
                    # The same shim, and for the same reason, applies to /tmp:
                    # bash resolves it one way, a native (non-MSYS) python.exe
                    # spawned as a child interprets a bare leading-slash path
                    # relative to the current drive root instead, so the two
                    # halves of one script can silently disagree about where a
                    # file landed. Pointing both at one explicit Windows path
                    # removes the ambiguity.
                    tmp_dir = result_dir / "tmp"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    script = script.replace("/tmp/", _to_msys_path(tmp_dir) + "/")
                # Run through an explicit bash -c rather than
                # create_subprocess_shell, which on Windows goes through cmd.exe
                # and mangles a Program Files path with spaces in `executable=`.
                proc = await asyncio.create_subprocess_exec(
                    bash, "-c", script, cwd=str(result_dir), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, env=run_env,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    script, cwd=str(result_dir), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, env=run_env,
                )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            exit_code = proc.returncode or 0
        except TimeoutError:
            exit_code = 124
            out = b"(fake sandbox) timed out"
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            out = str(exc).encode()

        stdout = out.decode("utf-8", "replace")
        image_uuid = result_uuid if not disposable else None
        if not disposable:
            self._images[result_uuid] = FakeImage(uuid=result_uuid, workdir=result_dir)
        if tag and image_uuid:
            self._images[tag] = self._images[result_uuid]

        return RunResult(
            image=image_uuid, exit_code=exit_code, stdout=stdout, stderr="",
            duration_ms=int((time.monotonic() - started) * 1000), operation_id=op_id,
        )

    async def cancel(self, operation_id: str) -> None:
        self._cancelled.add(operation_id)

    async def tag(self, image: Any, tag: str) -> Any:
        self._images[tag] = image
        return image


def _to_msys_path(path: Path) -> str:
    """C:\\foo\\bar -> C:/foo/bar.

    Deliberately the drive-letter form, not MSYS's /c/foo/bar. Scripts mix bash
    builtins (cd, cat, heredoc redirection) with arguments handed straight to a
    native, non-MSYS pytest.exe. MSYS bash accepts both forms, but a native
    Windows program does not understand /c/... — it reads the leading slash as
    "root of the current drive" and silently looks in the wrong place, so
    "coverage JSON written" can report success while writing nowhere anyone
    reads from. C:/foo/bar is the one spelling both sides agree on.
    """
    return str(path.resolve()).replace("\\", "/")


def _shell() -> str | None:
    import shutil

    return shutil.which("bash") or shutil.which("sh")


def _local_dev_env(extra: dict[str, str] | None) -> dict[str, str]:
    """The real sandbox is a fresh microVM with its own Python. This fake runs
    on the host, so `python`/`pip`/`pytest` must resolve to the interpreter this
    process is running under (typically a project venv), not whatever `python`
    happens to be first on the ambient PATH.
    """
    env = dict(os.environ)
    venv_bin = str(Path(sys.executable).parent)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PIP_ROOT_USER_ACTION", "ignore")
    if extra:
        env.update(extra)
    return env
