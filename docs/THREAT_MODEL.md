# Threat model

Principal executes model-generated code against a real repository and opens pull
requests. That is a supply-chain attack surface, and the honest way to present it
is a threat matrix with the residual risk named rather than a paragraph claiming
the sandbox makes it safe.

The governing idea: **model output is untrusted input, exactly like a request
body from the internet.** Everything below follows from that.

## Trust boundaries

```
┌─ UNTRUSTED ──────────────────────┐
│ repo source, comments, docstrings│───┐
│ model output                     │   │  context pack
│ test stdout and stack traces     │   │
└──────────────────────────────────┘   ▼
                             ┌─ ORCHESTRATOR ─────────────────┐
                             │ deterministic, no model in the │
                             │ accept path                    │
                             │   gates ──► job state machine  │
                             └───────┬────────────────┬───────┘
                          verified   │                │ after integration only
                          patch only ▼                ▼
              ┌─ SANDBOX ──────────────┐      ┌─ CREDENTIALS ──────┐
              │ separate kernel        │      │ GitHub token,      │
              │ filesystem isolation   │      │ fork-scoped        │
              │ repository tests run   │      │ Token Factory keys │
              └────────────────────────┘      └────────────────────┘
```

Two crossings do the work:

1. **Nothing reaches the sandbox without passing a gate.**
2. **Nothing reaches GitHub except from the job state machine, after integration.**

Both are enforced by the import-linter contracts in `pyproject.toml`, so a
violation is a failed CI job rather than a code-review miss.

## Threat matrix

| Threat | Vector | Mitigation | Where | Residual |
|---|---|---|---|---|
| Prompt injection from repo content | A docstring instructing the agent to edit tests | Instructions carry no privileged channel. The agent's only output is a diff, and the diff is gated regardless of what motivated it | `gates/scope.py`, fixture `injected_instruction.diff` | **Low** — injection can waste a candidate, not pass a gate |
| Agent edits tests to pass | Diff touching a test file | Rejected at gate 1 before any sandbox is spent, plus the test-count invariant at integration | `gates/scope.py`, `gates/behaviour.py` | **Low** |
| Agent deletes the code under test | Removing a symbol so nothing fails | Public API delta compared against the plan's *declared* removals | `gates/behaviour.py::check_api_delta` | **Medium** — see below |
| Dependency tampering | Editing a manifest or lockfile to change what is installed | `BLOCKED_MANIFESTS` rejected at gate 1 | `gates/scope.py` | **Low** |
| Secret exfiltration | A test run phoning home | No credentials are ever placed in a sandbox | `sandbox/` | **Medium** — see below |
| Malicious repository code | Arbitrary execution during the test run | Separate kernel per command, filesystem isolation — the platform's core guarantee | Token Factory Sandboxes | **Low** |
| Writing to the upstream repository | A bug in the publisher | Fine-grained token scoped to a fork, draft PR only, exactly one call site | `publish/github.py` | **Low** |
| Path traversal in a diff header | `../../etc/passwd` as a file header | Paths resolved and compared against the snapshot root before the diff is treated as a diff | `diffs.py::path_traversal`, fixture `path_traversal.diff` | **Low** |
| Runaway spend | A retry loop consuming the credit budget | Budget reserved before every call; hard caps on attempts, repairs and forks | `orchestrator/budget.py` | **Low** |
| Supply chain via base image | A tampered base image | Pinned by tag, not digest | `config.py::principal_base_image` | **Medium** — see below |
| Swallowed exception in a gate | `except Exception: pass` turning a failure into a pass | Gates never raise; CI greps `gates/` and `publish/` for bare and swallowed excepts | `.github/workflows/ci.yml` | **Low** |

## The residual risks, stated plainly

Every verification system has a boundary. Naming ours is more credible than
implying there isn't one.

### 1. An untested private symbol can be deleted without any gate objecting

This is the one the design cannot fully close. The API delta check covers
*exported* symbols. The coverage floor catches anything a test touched. But a
**private helper with no test coverage** can be removed and no gate will object,
because by construction nothing observes it.

Principal's response is not to hide this:

- Files in the blast radius with no covering tests are listed explicitly in the
  PR body under "what a human must check".
- `select_tests` falls back to the **full suite** rather than an empty selection
  when no covering test is found — a file with no discoverable test must never
  pass a gate by default.
- The mini_repo fixture contains exactly this case (`src/auth/internal.py`) so
  the behaviour is exercised rather than assumed.

### 2. Network egress during sandbox runs is not disabled

The design called for `network=False` on attempt runs, which would make the
secret-exfiltration mitigation structural. **That parameter does not exist** in
`contree-sdk` 0.3.6 — verified against the installed package, and written up as
item 6 in [`FEEDBACK.md`](FEEDBACK.md).

So the honest statement is: generated code runs in an isolated microVM with a
separate kernel, **and** it has network access. No credential is ever placed in
a sandbox, which bounds what exfiltration could achieve, but "no egress" is a
claim this project cannot currently make. It becomes true the day the platform
ships the flag.

### 3. The base image is pinned by tag, not digest

`python:3.11-slim` is a moving target. A tampered or simply changed upstream
image would alter the baseline environment underneath a run. Pinning by digest
is the fix, it is cheap, and it is deliberately deferred rather than forgotten:
tag-pinning keeps the fixture and CI working across Python patch releases during
active development. `PRINCIPAL_BASE_IMAGE` accepts a digest today
(`python@sha256:...`) for anyone who wants it now.

## Why prompt injection is structurally weak here

Worth stating explicitly, because it is the question a security-minded reviewer
asks first.

Injection matters when instructions and data share a channel **and the model can
act**. Here the model cannot act at all:

1. It emits text.
2. A deterministic parser extracts a diff, or rejects the output.
3. Deterministic gates decide, with no model in the accept path.

The worst an injected instruction achieves is a candidate that fails a gate,
costing one cheap completion. `injected_instruction.diff` in the adversarial
corpus is exactly this: a docstring telling the agent to update the tests, and a
diff that obeys. It is rejected for touching a test file — the motivation is
irrelevant to the verdict.

That argument is stronger than a filter, in the same way a scoped token is
stronger than a careful caller.

## The adversarial corpus

Every safety claim above has a fixture in `tests/fixtures/diffs/`, asserted in
`tests/unit/test_adversarial_corpus.py`:

| Fixture | Required verdict |
|---|---|
| `edits_test_file.diff` | `GATE_SCOPE` |
| `edits_two_files.diff` | `GATE_SCOPE` |
| `edits_manifest.diff` | `GATE_SCOPE` |
| `path_traversal.diff` | `GATE_SCOPE` |
| `injected_instruction.diff` | `GATE_SCOPE` |
| `truncated.diff` | `GATE_SYNTAX` |
| `truncated_midhunk.diff` | `DIFF_UNPARSEABLE` |
| `two_blocks.txt` | `DIFF_UNPARSEABLE` |
| `unfenced.txt` | `DIFF_UNPARSEABLE` |
| `correct.diff` | **`ok`** |

The last row matters as much as the others. A gate suite that only proves things
get rejected is satisfied by a gate that rejects everything — which would be
perfectly safe and perfectly useless.

## Scope of trust

Principal is trusted to write code, **not** to decide what to write or whether to
ship it. Everything it produces is reviewable, reversible and gated behind a
human approving a draft pull request.
