"""Tavily search, and the two deterministic triggers that decide when to call it.

The triggers are facts the run already recorded, never a model's request:

- planning: a file in the blast radius imports a package the repository does not
  define and the standard library does not provide (`third_party_imports`);
- repair: the failing test crashed inside an installed package, i.e. the deepest
  frame is under site-packages (`library_failure`).

What leaves the machine is a package name, a declared version, an exception class
and a library module path. Never repository source, never a test name, never an
exception message (those carry the repository's own values).

Grounding fails open. No key, a timeout or a 4xx is recorded as
`grounding.skipped` and the job carries on exactly as it would have without it.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from principal.config import Settings
from principal.db.events import EventLog
from principal.db.store import Store

SEARCH_URL = "https://api.tavily.com/search"
# Basic depth costs one credit per search, advanced two; a planner or a repair
# needs a few relevant snippets, not the most exhaustive ranking.
SEARCH_DEPTH = "basic"
MAX_RESULTS = 3
SNIPPET_CHARS = 1200
MAX_PACKAGES = 2

# Test tooling raises inside its own package (a failing `pytest.raises` crashes in
# _pytest). That is the test's failure, not a library's, and a search would spend
# a credit to learn nothing.
_TOOLING = frozenset({"_pytest", "pytest", "pluggy", "pytest_timeout", "pytest_cov", "pytest_jsonreport", "coverage"})

_NODE_BUILTINS = frozenset({
    "assert", "buffer", "child_process", "crypto", "dns", "events", "fs", "http", "https", "net", "os",
    "path", "process", "readline", "stream", "timers", "tls", "url", "util", "worker_threads", "zlib",
})

_SITE = re.compile(r"[\\/](?:site|dist)-packages[\\/](.+?)\.py$")
_EXC = re.compile(r"^[A-Za-z_][\w.]*$")


@dataclass(frozen=True, slots=True)
class Reference:
    title: str
    url: str
    content: str


@dataclass(frozen=True, slots=True)
class Search:
    query: str
    request_id: str | None
    response_time: float | None
    references: tuple[Reference, ...]


class GroundingUnavailable(Exception):
    """The search did not happen or did not answer. Always recoverable."""


@dataclass(slots=True)
class Grounder:
    """One per job: the per-job search cap and the query cache live here."""

    api_key: str
    max_searches: int
    timeout: float = 10.0
    transport: httpx.AsyncBaseTransport | None = None
    searches: int = 0
    _cache: dict[str, Search] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> Grounder:
        return cls(api_key=settings.tavily_api_key, max_searches=settings.tavily_max_searches)

    async def search(self, query: str) -> tuple[Search, bool]:
        """The result, and whether it came from this job's cache."""
        if query in self._cache:
            return self._cache[query], True
        if not self.api_key:
            raise GroundingUnavailable("TAVILY_API_KEY is not set")
        if self.searches >= self.max_searches:
            raise GroundingUnavailable(f"the per-job cap of {self.max_searches} searches is spent")
        # Counted before the request, so a failing endpoint cannot be retried past the cap.
        self.searches += 1
        body = {
            "query": query, "search_depth": SEARCH_DEPTH, "max_results": MAX_RESULTS,
            "include_answer": False, "include_raw_content": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.post(SEARCH_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise GroundingUnavailable(f"Tavily answered HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise GroundingUnavailable(f"Tavily did not answer ({type(exc).__name__})") from exc
        if not isinstance(data, dict):
            raise GroundingUnavailable("Tavily answered with a body that is not a JSON object")

        refs = tuple(
            Reference(title=str(r.get("title") or r["url"]), url=str(r["url"]),
                      content=str(r.get("content") or "")[:SNIPPET_CHARS])
            for r in (data.get("results") or []) if isinstance(r, dict) and r.get("url")
        )
        found = Search(query=query, request_id=data.get("request_id"),
                       response_time=data.get("response_time"), references=refs)
        self._cache[query] = found
        return found, False


async def consult(
    grounder: Grounder | None, events: EventLog, job_id: str, *, stage: str, query: str,
    task_id: str | None = None,
) -> tuple[Reference, ...]:
    """Search, record what was asked and what came back, and never raise."""
    base: dict[str, object] = {"stage": stage, "query": query}
    if task_id is not None:
        base["task_id"] = task_id
    if grounder is None:
        events.emit(job_id, "grounding.skipped", {**base, "reason": "grounding is not configured"})
        return ()
    try:
        found, cached = await grounder.search(query)
    except GroundingUnavailable as exc:
        events.emit(job_id, "grounding.skipped", {**base, "reason": str(exc)})
        return ()
    events.emit(job_id, "grounding.searched", {
        **base, "provider": "tavily", "request_id": found.request_id, "response_time": found.response_time,
        "cached": cached, "results": [{"title": r.title, "url": r.url} for r in found.references],
    })
    return found.references


async def planning_references(
    grounder: Grounder | None, events: EventLog, store: Store, job_id: str, graph_id: str, files: set[str],
) -> tuple[Reference, ...]:
    """References for the planner: the documentation of the (at most MAX_PACKAGES)
    third-party packages the blast radius imports most widely."""
    packages = third_party_imports(store, graph_id, files)[:MAX_PACKAGES]
    if not packages:
        events.emit(job_id, "grounding.skipped", {
            "stage": "plan", "reason": "the blast radius imports no third-party package",
        })
        return ()
    snapshot = store.snapshot_of(graph_id)
    found: list[Reference] = []
    for package, lang in packages:
        version = declared_version(snapshot, package, lang) if snapshot is not None else None
        found += await consult(grounder, events, job_id, stage="plan", query=planning_query(package, lang, version))
    return tuple(found)


# ------------------------------------------------------------------ triggers --


def third_party_imports(store: Store, graph_id: str, files: set[str]) -> list[tuple[str, str]]:
    """(package, language) for every package a file in `files` imports that neither
    the repository nor the standard library provides, most widely imported first."""
    rows = store.q(
        "SELECT e.source_module AS module, f.path AS path, f.lang AS lang FROM import_edge e"
        " JOIN file f ON f.id = e.file_id"
        " WHERE e.graph_id = ? AND e.resolved_file_id IS NULL AND e.resolved_symbol_id IS NULL",
        (graph_id,),
    )
    local = _local_names(store, graph_id)
    importers: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        if r["path"] not in files:
            continue
        pkg = _package_of(r["module"] or "", r["lang"])
        if pkg is not None and pkg not in local:
            importers.setdefault((pkg, r["lang"]), set()).add(r["path"])
    return sorted(importers, key=lambda k: (-len(importers[k]), k))


def _local_names(store: Store, graph_id: str) -> set[str]:
    """Every directory and module stem in the repository: a name the repository
    can define itself, even where import resolution missed it. Over-matching here
    only means a search not made."""
    names: set[str] = set()
    for r in store.q("SELECT path FROM file WHERE graph_id = ?", (graph_id,)):
        for part in r["path"].split("/"):
            names.add(part.split(".", 1)[0])
    return names


def _package_of(module: str, lang: str) -> str | None:
    if not module:
        return None
    if lang == "python":
        if module.startswith("."):
            return None
        top = module.split(".", 1)[0]
        return None if top in sys.stdlib_module_names or top == "__future__" else top
    # TypeScript: relative, absolute, URL and path-alias specifiers are the repository's.
    if module.startswith((".", "/", "~", "#", "@/")) or ":" in module:
        return None
    parts = module.split("/")
    if module.startswith("@"):
        return "/".join(parts[:2]) if len(parts) >= 2 else None
    return None if parts[0] in _NODE_BUILTINS else parts[0]


def declared_version(snapshot: Path, package: str, lang: str) -> str | None:
    """The version the repository pins or floors, when its manifest says so. The
    import name and the distribution name differ for some packages (`yaml` is
    PyYAML); then nothing matches and the query simply carries no version."""
    if lang == "typescript":
        try:
            manifest = json.loads((snapshot / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            spec = (manifest.get(section) or {}).get(package)
            if isinstance(spec, str):
                m = re.search(r"\d[\w.]*", spec)
                return m.group(0) if m else None
        return None

    name = "[-_.]".join(re.escape(p) for p in re.split(r"[-_.]", package))
    pattern = re.compile(rf"(?im)^\s*[\"']?{name}\s*(?:\[[^\]]*\])?\s*(?:==|~=|>=)\s*(\d[\w.]*)")
    for path in [*sorted(snapshot.glob("requirements*.txt")), snapshot / "pyproject.toml"]:
        try:
            m = pattern.search(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if m:
            return m.group(1)
    return None


def planning_query(package: str, lang: str, version: str | None) -> str:
    language = "Python" if lang == "python" else "TypeScript"
    pinned = f" {version}" if version else ""
    return f"{package}{pinned} {language} library API reference, deprecations and breaking changes"


def library_failure(report_json: str | None) -> str | None:
    """A search query for the first failing test that crashed inside an installed
    package, or None. Built from the library's module path and exception class
    only: the exception message carries the repository's own values."""
    try:
        data = json.loads(report_json or "{}")
    except json.JSONDecodeError:
        return None
    for test in data.get("tests") or []:
        if test.get("outcome") not in {"failed", "error"}:
            continue
        stage = test.get("call") or test.get("setup") or {}
        crash = stage.get("crash") or {}
        m = _SITE.search(str(crash.get("path") or ""))
        if not m:
            continue
        module = re.sub(r"[\\/]", ".", m.group(1)).removesuffix(".__init__")
        package = module.split(".", 1)[0]
        exc = str(crash.get("message") or "").split("\n", 1)[0].split(":", 1)[0].strip()
        if package in _TOOLING or not _EXC.match(exc):
            continue
        return f"{exc} raised in {module} ({package} Python library): cause and fix"
    return None
