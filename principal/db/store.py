"""Typed row access over SQLite. No ORM.

`db` depends on nothing, so the schema can be tested without any of the rest.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

SCHEMA = Path(__file__).parent / "schema.sql"


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{ULID()}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------- rows --


@dataclass(slots=True)
class Job:
    id: str
    repo_url: str
    commit_sha: str
    goal: str
    target_fqn: str | None
    state: str
    baseline_image: str | None
    graph_id: str | None
    token_budget: int
    tokens_spent: int
    tunables: dict[str, Any]
    routine: str
    pr_url: str | None
    created_at: str
    finished_at: str | None
    stop_reason: str | None


@dataclass(slots=True)
class Task:
    id: str
    job_id: str
    seq: int
    target_file: str
    instruction: str
    acceptance: str
    declared_removals: list[str]
    depends_on: list[int]
    state: str
    attempts: int
    winning_attempt_id: str | None
    discard_reason: str | None
    kind: str = "refactor"


@dataclass(slots=True)
class Attempt:
    id: str
    task_id: str
    job_id: str
    n: int
    kind: str
    model: str
    temperature: float
    parent_image: str
    result_image: str | None = None
    operation_id: str | None = None
    diff_sha256: str | None = None
    verdict: str | None = None
    gate: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost: float | None = None


@dataclass(slots=True)
class SymbolRow:
    id: int
    fqn: str
    name: str
    kind: str
    file_id: int
    path: str
    line_start: int
    line_end: int
    signature: str | None
    exported: int


@dataclass(slots=True)
class CallEdgeRow:
    caller_symbol_id: int | None
    callee_symbol_id: int | None
    callee_name: str
    file_id: int
    path: str
    line: int
    confidence: str


@dataclass(slots=True)
class Radius:
    files: set[str] = field(default_factory=set)
    tests: list[str] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    symbols: set[int] = field(default_factory=set)


# -------------------------------------------------------------------- store --


# SQLite's variable limit is per statement, so a query built with one
# placeholder per member fails outright once the set grows past it — which a
# depth-2 blast-radius frontier on a large repository genuinely does. Every
# set-valued lookup below chunks instead, and the result is the union of the
# chunks, which is what a set-valued lookup already means to its callers.
IN_CHUNK = 500


def _chunks(values: list[Any], size: int = IN_CHUNK) -> Iterator[tuple]:
    for i in range(0, len(values), size):
        yield tuple(values[i:i + size])


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        # Every event is its own transaction, and there are hundreds to
        # thousands of them per job. At the default `synchronous = FULL` each
        # one costs a full fsync, which dominates write latency for the whole
        # run. Under WAL, NORMAL still survives an application crash — only an
        # OS or power failure can lose the tail — and that is the right trade
        # for an append-only log that is rewritten by no one.
        self._conn.execute("PRAGMA synchronous = NORMAL")
        # Without this a concurrent writer surfaces as an immediate
        # SQLITE_BUSY rather than a short wait, and the caller sees a hard
        # failure where the correct behaviour is to queue behind the lock.
        self._conn.execute("PRAGMA busy_timeout = 5000")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction. Row writes and their matching event share it."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def q1(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    # ------------------------------------------------------------- job ------

    def create_job(
        self,
        *,
        repo_url: str,
        commit_sha: str,
        goal: str,
        target_fqn: str | None,
        token_budget: int,
        tunables: dict[str, Any],
        routine: str = "interface_evolution",
        job_id: str | None = None,
    ) -> Job:
        jid = job_id or new_id()
        self.exec(
            "INSERT INTO job (id, repo_url, commit_sha, goal, target_fqn, state, token_budget,"
            " tunables, routine, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (jid, repo_url, commit_sha, goal, target_fqn, "Ingesting", token_budget,
             json.dumps(tunables), routine, now()),
        )
        job = self.get_job(jid)
        assert job is not None
        return job

    def _job_of(self, r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"], repo_url=r["repo_url"], commit_sha=r["commit_sha"], goal=r["goal"],
            target_fqn=r["target_fqn"], state=r["state"], baseline_image=r["baseline_image"],
            graph_id=r["graph_id"], token_budget=r["token_budget"], tokens_spent=r["tokens_spent"],
            tunables=json.loads(r["tunables"] or "{}"), routine=r["routine"] or "interface_evolution",
            pr_url=r["pr_url"], created_at=r["created_at"], finished_at=r["finished_at"],
            stop_reason=r["stop_reason"],
        )

    def get_job(self, job_id: str) -> Job | None:
        r = self.q1("SELECT * FROM job WHERE id = ?", (job_id,))
        return self._job_of(r) if r else None

    def list_jobs(self, limit: int = 50) -> list[Job]:
        return [
            self._job_of(r)
            for r in self.q("SELECT * FROM job ORDER BY id DESC LIMIT ?", (limit,))
        ]

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.exec(f"UPDATE job SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def add_tokens(self, job_id: str, n: int) -> None:
        self.exec("UPDATE job SET tokens_spent = tokens_spent + ? WHERE id = ?", (n, job_id))

    # ------------------------------------------------------------ task ------

    def create_task(
        self, *, job_id: str, seq: int, target_file: str, instruction: str,
        acceptance: str, depends_on: list[int], declared_removals: list[str] | None = None,
        kind: str = "refactor",
    ) -> Task:
        tid = new_id("t-")
        self.exec(
            "INSERT INTO task (id, job_id, seq, target_file, instruction, acceptance,"
            " declared_removals, depends_on, state, kind) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, job_id, seq, target_file, instruction, acceptance,
             json.dumps(declared_removals or []), json.dumps(depends_on), "pending", kind),
        )
        t = self.get_task(tid)
        assert t is not None
        return t

    def _task_of(self, r: sqlite3.Row) -> Task:
        return Task(
            id=r["id"], job_id=r["job_id"], seq=r["seq"], target_file=r["target_file"],
            instruction=r["instruction"], acceptance=r["acceptance"],
            declared_removals=json.loads(r["declared_removals"] or "[]"),
            depends_on=json.loads(r["depends_on"] or "[]"), state=r["state"],
            attempts=r["attempts"], winning_attempt_id=r["winning_attempt_id"],
            discard_reason=r["discard_reason"], kind=r["kind"] if "kind" in r.keys() else "refactor",
        )

    def get_task(self, task_id: str) -> Task | None:
        r = self.q1("SELECT * FROM task WHERE id = ?", (task_id,))
        return self._task_of(r) if r else None

    def tasks_of(self, job_id: str) -> list[Task]:
        return [self._task_of(r) for r in self.q("SELECT * FROM task WHERE job_id = ? ORDER BY seq", (job_id,))]

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.exec(f"UPDATE task SET {cols} WHERE id = ?", (*fields.values(), task_id))

    def bump_attempts(self, task_id: str) -> int:
        self.exec("UPDATE task SET attempts = attempts + 1 WHERE id = ?", (task_id,))
        r = self.q1("SELECT attempts FROM task WHERE id = ?", (task_id,))
        return int(r["attempts"]) if r else 0

    # --------------------------------------------------------- attempt ------

    def create_attempt(
        self, *, task_id: str, job_id: str, n: int, model: str, temperature: float,
        parent_image: str, kind: str = "coder",
    ) -> Attempt:
        aid = new_id("a-")
        self.exec(
            "INSERT INTO attempt (id, task_id, job_id, n, kind, model, temperature, parent_image)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (aid, task_id, job_id, n, kind, model, temperature, parent_image),
        )
        a = self.get_attempt(aid)
        assert a is not None
        return a

    def _attempt_of(self, r: sqlite3.Row) -> Attempt:
        return Attempt(
            id=r["id"], task_id=r["task_id"], job_id=r["job_id"], n=r["n"], kind=r["kind"],
            model=r["model"], temperature=r["temperature"], parent_image=r["parent_image"],
            result_image=r["result_image"], operation_id=r["operation_id"],
            diff_sha256=r["diff_sha256"], verdict=r["verdict"], gate=r["gate"],
            exit_code=r["exit_code"], duration_ms=r["duration_ms"],
            prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"],
            cost=r["cost"],
        )

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        r = self.q1("SELECT * FROM attempt WHERE id = ?", (attempt_id,))
        return self._attempt_of(r) if r else None

    def attempts_of(self, job_id: str) -> list[Attempt]:
        # One query, not one per attempt: GET /jobs/{id}/tree calls this and the
        # dashboard polls that endpoint every 1.5s, so the previous select-ids-
        # then-fetch-each pattern put 1+N round trips behind the global lock on
        # every poll, blocking the event loop for the whole duration.
        return [
            self._attempt_of(r)
            for r in self.q("SELECT * FROM attempt WHERE job_id = ? ORDER BY rowid", (job_id,))
        ]

    def update_attempt(self, attempt_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.exec(f"UPDATE attempt SET {cols} WHERE id = ?", (*fields.values(), attempt_id))

    def live_operations(self, job_id: str) -> list[tuple[str, str]]:
        """Attempts that registered an operation and never recorded a verdict."""
        rows = self.q(
            "SELECT id, operation_id FROM attempt WHERE job_id = ? AND operation_id IS NOT NULL"
            " AND verdict IS NULL",
            (job_id,),
        )
        return [(r["id"], r["operation_id"]) for r in rows]

    # -------------------------------------------------------- artifact ------

    def put_artifact(
        self, *, job_id: str, kind: str, rel_path: str, content: str,
        base_dir: Path, attempt_id: str | None = None,
    ) -> str:
        dest = base_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        aid = new_id("art-")
        self.exec(
            "INSERT INTO artifact (id, job_id, attempt_id, kind, path, sha256, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (aid, job_id, attempt_id, kind, rel_path, sha256_text(content), now()),
        )
        return aid

    def get_artifact(self, artifact_id: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM artifact WHERE id = ?", (artifact_id,))

    def artifacts_of(self, job_id: str, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return self.q(
                "SELECT * FROM artifact WHERE job_id = ? AND kind = ? ORDER BY rowid", (job_id, kind)
            )
        return self.q("SELECT * FROM artifact WHERE job_id = ? ORDER BY rowid", (job_id,))

    # ----------------------------------------------------------- graph ------

    def graph_exists(self, graph_id: str) -> bool:
        return self.q1("SELECT id FROM graph WHERE id = ?", (graph_id,)) is not None

    def snapshot_of(self, graph_id: str) -> Path | None:
        r = self.q1("SELECT snapshot FROM graph WHERE id = ?", (graph_id,))
        return Path(r["snapshot"]) if r else None

    def files_of_graph(self, graph_id: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM file WHERE graph_id = ? ORDER BY path", (graph_id,))

    def _symbol_of(self, r: sqlite3.Row) -> SymbolRow:
        return SymbolRow(
            id=r["id"], fqn=r["fqn"], name=r["name"], kind=r["kind"], file_id=r["file_id"],
            path=r["path"], line_start=r["line_start"], line_end=r["line_end"],
            signature=r["signature"], exported=r["exported"],
        )

    _SYM_SELECT = (
        "SELECT s.*, f.path AS path FROM symbol s JOIN file f ON f.id = s.file_id"
    )

    def find_symbols(self, graph_id: str, name_or_fqn: str, kind: str | None = None) -> list[SymbolRow]:
        sql = f"{self._SYM_SELECT} WHERE s.graph_id = ? AND (s.fqn = ? OR s.name = ? OR s.fqn LIKE ?)"
        args: tuple = (graph_id, name_or_fqn, name_or_fqn.rsplit(".", 1)[-1], f"%.{name_or_fqn}")
        if kind:
            sql += " AND s.kind = ?"
            args = (*args, kind)
        return [self._symbol_of(r) for r in self.q(sql, args)]

    def _in_query(self, sql_template: str, graph_id: str, members: set) -> list[sqlite3.Row]:
        """Run a query whose WHERE has one `IN (...)` clause, chunked.

        `sql_template` is formatted with a single `{marks}` placeholder. Chunks
        partition the member set, and because every such lookup here filters on
        a column holding at most one of those members per row, the union across
        chunks contains no row twice.
        """
        if not members:
            return []
        out: list[sqlite3.Row] = []
        for chunk in _chunks(sorted(members)):
            marks = ",".join("?" * len(chunk))
            out.extend(self.q(sql_template.format(marks=marks), (graph_id, *chunk)))
        return out

    def symbols_in_files(self, graph_id: str, paths: set[str]) -> list[SymbolRow]:
        return [
            self._symbol_of(r)
            for r in self._in_query(
                f"{self._SYM_SELECT} WHERE s.graph_id = ? AND f.path IN ({{marks}})",
                graph_id, paths,
            )
        ]

    def exported_symbols(self, graph_id: str, paths: set[str] | None = None) -> set[str]:
        if paths is None:
            rows = self.q(
                f"{self._SYM_SELECT} WHERE s.graph_id = ? AND s.exported = 1", (graph_id,)
            )
        else:
            rows = self._in_query(
                f"{self._SYM_SELECT} WHERE s.graph_id = ? AND s.exported = 1"
                " AND f.path IN ({marks})",
                graph_id, paths,
            )
        return {f"{r['path']}::{r['fqn']}" for r in rows}

    def callers_of(self, graph_id: str, symbol_ids: set[int]) -> list[CallEdgeRow]:
        rows = self._in_query(
            "SELECT c.*, f.path AS path FROM call_edge c JOIN file f ON f.id = c.file_id"
            " WHERE c.graph_id = ? AND c.callee_symbol_id IN ({marks})",
            graph_id, symbol_ids,
        )
        return [
            CallEdgeRow(
                caller_symbol_id=r["caller_symbol_id"], callee_symbol_id=r["callee_symbol_id"],
                callee_name=r["callee_name"], file_id=r["file_id"], path=r["path"],
                line=r["line"], confidence=r["confidence"],
            )
            for r in rows
        ]

    def unresolved_matching(self, graph_id: str, names: set[str]) -> list[CallEdgeRow]:
        """Heuristic edges whose callee could not be resolved but whose name matches.

        These are the silent-failure surface: a getattr or registry dispatch that
        reaches the target without a static edge to prove it.
        """
        rows = self._in_query(
            "SELECT c.*, f.path AS path FROM call_edge c JOIN file f ON f.id = c.file_id"
            " WHERE c.graph_id = ? AND c.callee_symbol_id IS NULL AND c.callee_name IN ({marks})",
            graph_id, names,
        )
        return [
            CallEdgeRow(
                caller_symbol_id=r["caller_symbol_id"], callee_symbol_id=None,
                callee_name=r["callee_name"], file_id=r["file_id"], path=r["path"],
                line=r["line"], confidence=r["confidence"],
            )
            for r in rows
        ]

    def importers_of(self, graph_id: str, symbol_ids: set[int]) -> list[sqlite3.Row]:
        return self._in_query(
            "SELECT i.*, f.path AS path FROM import_edge i JOIN file f ON f.id = i.file_id"
            " WHERE i.graph_id = ? AND i.resolved_symbol_id IN ({marks})",
            graph_id, symbol_ids,
        )

    def importers_of_files(self, graph_id: str, paths: set[str]) -> list[sqlite3.Row]:
        """Files that import a module living at one of these paths, whether or not
        an individual symbol inside it resolved."""
        return self._in_query(
            "SELECT DISTINCT src.path AS path, i.line AS line, i.source_module AS source_module"
            " FROM import_edge i"
            " JOIN file src ON src.id = i.file_id"
            " JOIN file tgt ON tgt.id = i.resolved_file_id"
            " WHERE i.graph_id = ? AND tgt.path IN ({marks})",
            graph_id, paths,
        )

    def dynamic_calls_in(self, graph_id: str, paths: set[str]) -> list[CallEdgeRow]:
        """Calls in these files that no static edge could bind to anything."""
        rows = self._in_query(
            "SELECT c.*, f.path AS path FROM call_edge c JOIN file f ON f.id = c.file_id"
            " WHERE c.graph_id = ? AND c.dynamic = 1 AND f.path IN ({marks})",
            graph_id, paths,
        )
        return [
            CallEdgeRow(
                caller_symbol_id=r["caller_symbol_id"], callee_symbol_id=None,
                callee_name=r["callee_name"], file_id=r["file_id"], path=r["path"],
                line=r["line"], confidence=r["confidence"],
            )
            for r in rows
        ]

    def tests_covering(self, graph_id: str, symbol_ids: set[int]) -> list[sqlite3.Row]:
        return self._in_query(
            "SELECT * FROM test_edge WHERE graph_id = ? AND symbol_id IN ({marks})",
            graph_id, symbol_ids,
        )

    def path_of_file(self, file_id: int) -> str:
        r = self.q1("SELECT path FROM file WHERE id = ?", (file_id,))
        return r["path"] if r else ""

    def paths_of_files(self, file_ids: set[int]) -> dict[int, str]:
        """One query for many file ids, for callers resolving a whole column of
        them — `path_of_file` in a loop is a query per row."""
        if not file_ids:
            return {}
        out: dict[int, str] = {}
        for chunk in _chunks(sorted(file_ids)):
            marks = ",".join("?" * len(chunk))
            for r in self.q(f"SELECT id, path FROM file WHERE id IN ({marks})", chunk):
                out[int(r["id"])] = r["path"]
        return out

    def test_flags_of(self, graph_id: str, paths: set[str]) -> dict[str, bool]:
        """Which of these paths are test files, in one query."""
        rows = self._in_query(
            "SELECT path, is_test FROM file WHERE graph_id = ? AND path IN ({marks})",
            graph_id, paths,
        )
        return {r["path"]: bool(r["is_test"]) for r in rows}

    # ----------------------------------------------------------- event ------

    def append_event(self, job_id: str, kind: str, payload: dict[str, Any]) -> int:
        cur = self.exec(
            "INSERT INTO event (job_id, ts, kind, payload) VALUES (?,?,?,?)",
            (job_id, now(), kind, json.dumps(payload, default=str)),
        )
        return int(cur.lastrowid or 0)

    def events_since(self, job_id: str, after_seq: int = 0, limit: int = 1000) -> list[sqlite3.Row]:
        return self.q(
            "SELECT * FROM event WHERE job_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (job_id, after_seq, limit),
        )
