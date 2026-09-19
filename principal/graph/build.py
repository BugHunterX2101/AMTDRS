"""Two-pass graph construction. The order is not optional: edges resolve against
the symbol table, so the symbol table has to be complete before any edge is written.

`build` is pure with respect to the job. It takes a snapshot and produces graph
rows; it does not know what goal is being pursued, so the same repository at the
same commit produces the same graph across jobs and the graph can be reused
between benchmark arms.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from principal.db.store import Store, now
from principal.graph.languages import SUPPORTED_SUFFIXES
from principal.graph.parse import ParsedFile, is_test_path, parse_file
from principal.graph.resolve import Resolver

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".ruff_cache", "site-packages", ".eggs",
}
MAX_FILES = 4000


@dataclass(slots=True)
class BuildStats:
    graph_id: str
    files: int
    symbols: int
    import_edges: int
    call_edges: int
    heuristic_edges: int
    test_edges: int
    parse_errors: int


def graph_id_for(repo_url: str, commit_sha: str) -> str:
    return hashlib.sha256(f"{repo_url}@{commit_sha}".encode()).hexdigest()[:24]


def walk(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*"):
        if len(out) >= MAX_FILES:
            break
        if p.is_dir():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix in SUPPORTED_SUFFIXES:
            out.append(p)
    return sorted(out)


def build(store: Store, snapshot: Path, repo_url: str, commit_sha: str, *, rebuild: bool = False) -> BuildStats:
    graph_id = graph_id_for(repo_url, commit_sha)
    if store.graph_exists(graph_id) and not rebuild:
        return _stats_of(store, graph_id)
    if rebuild:
        store.exec("DELETE FROM graph WHERE id = ?", (graph_id,))

    paths = walk(snapshot)
    parsed: dict[str, ParsedFile] = {}
    parse_errors = 0
    for p in paths:
        pf = parse_file(p, snapshot)
        if pf is None:
            continue
        parsed[pf.path] = pf
        if pf.has_error:
            parse_errors += 1

    with store.tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO graph (id, repo_url, commit_sha, snapshot, built_at)"
            " VALUES (?,?,?,?,?)",
            (graph_id, repo_url, commit_sha, str(snapshot), now()),
        )

        # -- pass 1: every definition in the repository ---------------------
        file_ids: dict[str, int] = {}
        for rel, pf in parsed.items():
            sha = hashlib.sha256(pf.source).hexdigest()
            cur = conn.execute(
                "INSERT INTO file (graph_id, path, lang, sha256, is_test) VALUES (?,?,?,?,?)",
                (graph_id, rel, pf.lang, sha, int(pf.is_test)),
            )
            file_ids[rel] = int(cur.lastrowid or 0)

        resolver = Resolver(parsed)
        module_of = {rel: _module_name(rel, pf.lang) for rel, pf in parsed.items()}
        symbol_count = 0
        for rel, pf in parsed.items():
            for d in pf.definitions:
                fqn = ".".join(filter(None, (module_of[rel], d.parent, d.name)))
                cur = conn.execute(
                    "INSERT OR IGNORE INTO symbol (graph_id, fqn, name, kind, file_id, line_start,"
                    " line_end, signature, exported) VALUES (?,?,?,?,?,?,?,?,?)",
                    (graph_id, fqn, d.name, d.kind, file_ids[rel], d.line_start, d.line_end,
                     d.signature, int(d.exported)),
                )
                sid = int(cur.lastrowid or 0)
                if sid:
                    symbol_count += 1
                    resolver.register(rel, d.name, sid)
                    if d.parent:
                        resolver.register(rel, f"{d.parent}.{d.name}", sid)

        # -- pass 2: edges, resolved against the complete table --------------
        import_count = call_count = heuristic_count = 0
        for rel, pf in parsed.items():
            fid = file_ids[rel]
            for imp in pf.imports:
                resolved = resolver.resolve_import(imp, rel)
                resolved_path = resolver.resolve_import_file(imp, rel)
                conn.execute(
                    "INSERT INTO import_edge (graph_id, file_id, symbol_name, local_name,"
                    " source_module, resolved_symbol_id, resolved_file_id, line)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (graph_id, fid, imp.symbol_name, imp.local_name, imp.source_module,
                     resolved, file_ids.get(resolved_path or ""), imp.line),
                )
                import_count += 1

            bindings = resolver.local_bindings(rel)
            aliases = resolver.module_aliases(rel)
            enclosing_cache: dict[str, int | None] = {}
            for call in pf.calls:
                callee_id, confidence = resolver.resolve_callee(call, rel, bindings, aliases)
                if call.enclosing not in enclosing_cache:
                    enclosing_cache[call.enclosing] = (
                        resolver.symbol_ids.get((rel, call.enclosing)) if call.enclosing else None
                    )
                conn.execute(
                    "INSERT INTO call_edge (graph_id, caller_symbol_id, callee_symbol_id,"
                    " callee_name, file_id, line, confidence, dynamic)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (graph_id, enclosing_cache[call.enclosing], callee_id, call.callee_name,
                     fid, call.line, confidence, int(resolver.is_dynamic(call, callee_id))),
                )
                call_count += 1
                if confidence == "heuristic":
                    heuristic_count += 1

        # -- import-derived test edges ---------------------------------------
        # The weaker of the two sources. Coverage edges are added at baseline and
        # supersede these wherever they exist.
        test_count = 0
        for rel, pf in parsed.items():
            if not pf.is_test:
                continue
            for name, sid in resolver.local_bindings(rel).items():
                row = conn.execute(
                    "SELECT file_id FROM symbol WHERE id = ?", (sid,)
                ).fetchone()
                if row is None or row[0] == file_ids[rel]:
                    continue
                conn.execute(
                    "INSERT INTO test_edge (graph_id, test_file_id, symbol_id, nodeid, source)"
                    " VALUES (?,?,?,?,?)",
                    (graph_id, file_ids[rel], sid, rel, "import"),
                )
                test_count += 1
                del name

    return BuildStats(
        graph_id=graph_id, files=len(parsed), symbols=symbol_count, import_edges=import_count,
        call_edges=call_count, heuristic_edges=heuristic_count, test_edges=test_count,
        parse_errors=parse_errors,
    )


def _module_name(rel: str, lang: str) -> str:
    p = Path(rel)
    parts = list(p.parts)
    if lang == "python":
        stem = p.stem
        if stem == "__init__":
            parts = parts[:-1]
        else:
            parts[-1] = stem
        return ".".join(parts)
    parts[-1] = p.stem
    return ".".join(parts)


def _stats_of(store: Store, graph_id: str) -> BuildStats:
    def count(table: str, extra: str = "") -> int:
        r = store.q1(f"SELECT COUNT(*) AS n FROM {table} WHERE graph_id = ? {extra}", (graph_id,))
        return int(r["n"]) if r else 0

    return BuildStats(
        graph_id=graph_id,
        files=count("file"),
        symbols=count("symbol"),
        import_edges=count("import_edge"),
        call_edges=count("call_edge"),
        heuristic_edges=count("call_edge", "AND confidence = 'heuristic'"),
        test_edges=count("test_edge"),
        parse_errors=0,
    )


# ------------------------------------------------------ coverage test edges --


def ingest_coverage(store: Store, graph_id: str, coverage_json: str) -> int:
    """Turn `pytest --cov-context=test` output into test_edge rows.

    Coverage contexts map an executed line to the test that executed it, which
    catches tests that reach a symbol through three layers of indirection. That is
    exactly where the import-derived edges silently miss, and it is why the
    baseline suite is run with contexts enabled: the map is a by-product of a run
    that had to happen anyway.
    """
    try:
        data = json.loads(coverage_json)
    except (json.JSONDecodeError, TypeError):
        return 0

    files = data.get("files", {})
    if not files:
        return 0

    symbols = store.q(
        "SELECT s.id, s.line_start, s.line_end, f.path AS path FROM symbol s"
        " JOIN file f ON f.id = s.file_id WHERE s.graph_id = ?",
        (graph_id,),
    )
    by_path: dict[str, list[tuple[int, int, int]]] = {}
    for r in symbols:
        by_path.setdefault(r["path"], []).append((r["line_start"], r["line_end"], r["id"]))

    test_file_ids = {
        r["path"]: r["id"] for r in store.q(
            "SELECT id, path FROM file WHERE graph_id = ? AND is_test = 1", (graph_id,)
        )
    }

    inserted = 0
    seen: set[tuple[int, int, str]] = set()
    with store.tx() as conn:
        for raw_path, entry in files.items():
            rel = _normalise(raw_path)
            candidates = by_path.get(rel)
            if not candidates:
                continue
            contexts: dict[str, list[str]] = entry.get("contexts", {})
            for line_str, ctxs in contexts.items():
                try:
                    line = int(line_str)
                except (TypeError, ValueError):
                    continue
                for start, end, sid in candidates:
                    if not (start <= line <= end):
                        continue
                    for ctx in ctxs:
                        nodeid = ctx.split("|", 1)[0].strip()
                        if not nodeid or "::" not in nodeid:
                            continue
                        test_path = _normalise(nodeid.split("::", 1)[0])
                        tfid = test_file_ids.get(test_path)
                        if tfid is None:
                            continue
                        key = (tfid, sid, nodeid)
                        if key in seen:
                            continue
                        seen.add(key)
                        conn.execute(
                            "INSERT INTO test_edge (graph_id, test_file_id, symbol_id, nodeid,"
                            " source) VALUES (?,?,?,?,?)",
                            (graph_id, tfid, sid, nodeid, "coverage"),
                        )
                        inserted += 1
    return inserted


def _normalise(path: str) -> str:
    p = path.replace("\\", "/").lstrip("./")
    for prefix in ("work/", "/work/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return p


__all__ = ["build", "BuildStats", "graph_id_for", "ingest_coverage", "is_test_path", "walk"]
