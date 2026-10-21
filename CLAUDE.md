# CLAUDE.md

Instructions for AI coding agents working in this repository.

## Behavioral guidelines

Adapted from [andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)
(MIT), which distils Andrej Karpathy's observations on common LLM coding mistakes. The same
text is installed globally as the `karpathy-guidelines` skill.

**Tradeoff:** these bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require
constant clarification.

## Project rules

These are what the guidelines above mean in this codebase.

- **Verify with the project's own commands.** `make test` (pytest), `make lint` (ruff plus
  the import contracts), `make ci` for both. CI also runs on Linux and Windows; a change that
  passes on one is not verified on the other.
- **No model in the accept path.** `principal/gates/` must not import `principal/models/`.
  `lint-imports` enforces it; a change that needs to weaken the contract is a design question
  to raise, not a line to edit.
- **Real over synthetic.** Anything that claims to verify or measure Principal runs on the
  real Nemotron models and the real Nebius sandbox. The in-process `FakeSandbox` is only for
  tests and for `--fake-sandbox`, and its results are labelled NOT LIVE. Never present a
  stand-in result as a platform measurement.
- **Never write credentials to a tracked file.** The Nebius key, project id and GitHub token
  belong in process environment variables only.
- **Say what was and was not checked.** Report a skipped step or a failing test plainly; do
  not round a partial result up to "done".
