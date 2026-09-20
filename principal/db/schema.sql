-- Principal: one SQLite file holding two unrelated things.
--   The code graph, which is derived and disposable.
--   The run record, which is the evidence and must never be lost.
-- Blobs live on disk under runs/<job_id>/ and this file holds only their index,
-- so a run directory can be zipped and published as-is.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================ code graph =====

CREATE TABLE IF NOT EXISTS graph (
  id          TEXT PRIMARY KEY,           -- repo_url@commit_sha, content addressed
  repo_url    TEXT NOT NULL,
  commit_sha  TEXT NOT NULL,
  snapshot    TEXT NOT NULL,              -- local path of the parsed snapshot
  built_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file (
  id        INTEGER PRIMARY KEY,
  graph_id  TEXT NOT NULL REFERENCES graph(id) ON DELETE CASCADE,
  path      TEXT NOT NULL,
  lang      TEXT NOT NULL CHECK (lang IN ('python', 'typescript')),
  sha256    TEXT NOT NULL,
  is_test   INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS file_graph_path ON file(graph_id, path);

CREATE TABLE IF NOT EXISTS symbol (
  id          INTEGER PRIMARY KEY,
  graph_id    TEXT NOT NULL REFERENCES graph(id) ON DELETE CASCADE,
  fqn         TEXT NOT NULL,
  name        TEXT NOT NULL,              -- last segment, for name-based resolution
  kind        TEXT NOT NULL,              -- function | class | method | const | type
  file_id     INTEGER NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  line_start  INTEGER NOT NULL,
  line_end    INTEGER NOT NULL,
  signature   TEXT,
  exported    INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS symbol_fqn_file ON symbol(graph_id, fqn, file_id);
CREATE INDEX IF NOT EXISTS symbol_name ON symbol(graph_id, name);

CREATE TABLE IF NOT EXISTS import_edge (
  id                 INTEGER PRIMARY KEY,
  graph_id           TEXT NOT NULL REFERENCES graph(id) ON DELETE CASCADE,
  file_id            INTEGER NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  symbol_name        TEXT NOT NULL,
  local_name         TEXT NOT NULL,       -- the name as bound in this file
  source_module      TEXT NOT NULL,
  resolved_symbol_id INTEGER REFERENCES symbol(id) ON DELETE SET NULL,
  -- The module a specifier names, even when no individual symbol resolved.
  -- `from src.auth import session` binds a module, not a symbol, and a file that
  -- does that can still reach the target through dynamic dispatch. Without this
  -- column such a file is invisible to the blast radius, which is precisely the
  -- silent miss the whole design exists to avoid.
  resolved_file_id   INTEGER REFERENCES file(id) ON DELETE SET NULL,
  line               INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS import_resolved ON import_edge(graph_id, resolved_symbol_id);
CREATE INDEX IF NOT EXISTS import_resolved_file ON import_edge(graph_id, resolved_file_id);
CREATE INDEX IF NOT EXISTS import_file ON import_edge(graph_id, file_id);

CREATE TABLE IF NOT EXISTS call_edge (
  id               INTEGER PRIMARY KEY,
  graph_id         TEXT NOT NULL REFERENCES graph(id) ON DELETE CASCADE,
  caller_symbol_id INTEGER REFERENCES symbol(id) ON DELETE CASCADE,
  callee_symbol_id INTEGER REFERENCES symbol(id) ON DELETE CASCADE,
  callee_name      TEXT NOT NULL,         -- kept even when resolution failed
  file_id          INTEGER NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  line             INTEGER NOT NULL,
  -- Dynamic dispatch cannot be resolved statically. A getattr call, a string-keyed
  -- registry or a re-exported alias produces a heuristic edge, not a static one.
  -- Heuristic edges are included in the blast radius AND listed separately in the
  -- PR risk section, which is the honest version of "call sites are enumerated
  -- exactly": they are exact for the static subset and flagged for the rest.
  confidence       TEXT NOT NULL CHECK (confidence IN ('static', 'heuristic')),
  -- Set only when the call is genuinely dispatched at runtime: getattr, a
  -- registry lookup, or a call on a value rather than a name. An unresolved
  -- builtin is not dynamic dispatch, and flooding the risk list with `int` and
  -- `isinstance` is how a real warning gets ignored.
  dynamic          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS call_callee ON call_edge(graph_id, callee_symbol_id);
CREATE INDEX IF NOT EXISTS call_name ON call_edge(graph_id, callee_name);

CREATE TABLE IF NOT EXISTS test_edge (
  id           INTEGER PRIMARY KEY,
  graph_id     TEXT NOT NULL REFERENCES graph(id) ON DELETE CASCADE,
  test_file_id INTEGER NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  symbol_id    INTEGER NOT NULL REFERENCES symbol(id) ON DELETE CASCADE,
  nodeid       TEXT NOT NULL,             -- pytest node id, runnable directly
  -- 'coverage' edges come from --cov-context=test at baseline and catch tests that
  -- exercise a symbol through three layers of indirection, which is exactly where
  -- the import-derived version silently misses. 'characterisation' edges are the
  -- Characteriser's generated tests, registered here after they pass on C0 and
  -- clear the mutation floor, so gate 3's existing selection logic picks them up
  -- with no new code: they are a source of test_edge like any other.
  source       TEXT NOT NULL CHECK (source IN ('import', 'coverage', 'characterisation'))
);
CREATE INDEX IF NOT EXISTS test_symbol ON test_edge(graph_id, symbol_id);

-- ============================================================= run record ====

CREATE TABLE IF NOT EXISTS job (
  id             TEXT PRIMARY KEY,        -- ULID, sorts by time
  repo_url       TEXT NOT NULL,
  commit_sha     TEXT NOT NULL,
  goal           TEXT NOT NULL,
  target_fqn     TEXT,
  state          TEXT NOT NULL,
  baseline_image TEXT,                    -- Contree image UUID for C0
  graph_id       TEXT,
  token_budget   INTEGER NOT NULL,
  tokens_spent   INTEGER NOT NULL DEFAULT 0,
  tunables       TEXT,                    -- JSON, recorded so arms stay comparable
  -- Which routine planned this job: 'interface_evolution' or 'relocation'.
  -- Recorded so a published per-category result can be traced back to the run
  -- that produced it, and so a blended average is never the only number on offer.
  routine        TEXT NOT NULL DEFAULT 'interface_evolution',
  pr_url         TEXT,
  created_at     TEXT NOT NULL,
  finished_at    TEXT,
  stop_reason    TEXT
);

CREATE TABLE IF NOT EXISTS task (
  id                 TEXT PRIMARY KEY,
  job_id             TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  seq                INTEGER NOT NULL,
  target_file        TEXT NOT NULL,
  instruction        TEXT NOT NULL,
  acceptance         TEXT NOT NULL,
  -- Symbols the plan intends to remove or rename in this file. A rename looks
  -- like a removal plus an addition, which is exactly what interface evolution
  -- does, so the behaviour gate needs a declared set to compare against rather
  -- than inferring intent from prose. An undeclared removal is the agent that
  -- made tests pass by deleting the thing under test.
  declared_removals  TEXT NOT NULL DEFAULT '[]',
  depends_on         TEXT,                -- JSON array of task seqs
  state              TEXT NOT NULL,
  attempts           INTEGER NOT NULL DEFAULT 0,
  winning_attempt_id TEXT,
  discard_reason     TEXT,
  -- 'refactor' is the task the plan exists to accomplish. 'cleanup' is a task
  -- CleanupPlanning emitted from what the refactor itself orphaned — same Coder,
  -- same gates, same candidate race, distinguished only so the reporter and the
  -- dashboard can tell "asked for" from "swept up after".
  kind               TEXT NOT NULL DEFAULT 'refactor'
);
CREATE INDEX IF NOT EXISTS task_job ON task(job_id);

CREATE TABLE IF NOT EXISTS attempt (
  id                TEXT PRIMARY KEY,
  task_id           TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  job_id            TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  n                 INTEGER NOT NULL,
  kind              TEXT NOT NULL DEFAULT 'coder',   -- coder | repair
  model             TEXT NOT NULL,
  temperature       REAL NOT NULL,
  -- parent_image and result_image are enough to reconstruct the entire branch
  -- tree of a job after the fact, from the database alone. The dashboard's branch
  -- view, the audit trail and the traceability claim all fall out of these two.
  parent_image      TEXT NOT NULL,
  result_image      TEXT,
  operation_id      TEXT,                 -- written BEFORE the await, for recovery
  diff_sha256       TEXT,
  verdict           TEXT,                 -- ok | scope | syntax | red | error | timeout
  gate              TEXT,
  exit_code         INTEGER,
  duration_ms       INTEGER,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  cost              REAL
);
CREATE INDEX IF NOT EXISTS attempt_task ON attempt(task_id);
CREATE INDEX IF NOT EXISTS attempt_job ON attempt(job_id);

CREATE TABLE IF NOT EXISTS artifact (
  id         TEXT PRIMARY KEY,
  job_id     TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  attempt_id TEXT REFERENCES attempt(id) ON DELETE SET NULL,
  kind       TEXT NOT NULL,   -- diff | test_log | model_call | coverage | api_snapshot | report
  path       TEXT NOT NULL,   -- relative to runs/<job_id>/
  sha256     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifact_job ON artifact(job_id);

-- Append only, monotonically sequenced, never updated. Everything the dashboard
-- renders, every trace file published with the submission and every claim in the
-- PR body reads from here. One rule that is easy to break and expensive to fix
-- later: no code path writes to job, task or attempt without writing a matching
-- event in the same transaction. If those two disagree, the trace stops being
-- evidence.
CREATE TABLE IF NOT EXISTS event (
  seq     INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id  TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL,
  kind    TEXT NOT NULL,
  payload TEXT NOT NULL       -- JSON
);
CREATE INDEX IF NOT EXISTS event_job_seq ON event(job_id, seq);
-- GET /jobs/{id} looks up the latest event of one kind ("radius.computed") and
-- the dashboard polls it every 1.5s. On (job_id, seq) alone that is a reverse
-- scan over every event the job has emitted, because the kind is not in the
-- index — and the event it wants is written early, so the scan runs to the far
-- end each time. With kind in the index it is a seek.
CREATE INDEX IF NOT EXISTS event_job_kind_seq ON event(job_id, kind, seq);
