# Demo video — script and shot list

**Hard constraints from the rules:** under 3:00 · uploaded to YouTube and publicly visible · shows the project functioning (for a non-physical project, *the key application modules in action*) · **no third-party trademarks, no copyrighted music**.

Judges are not required to watch past three minutes, so the target is **2:45** and the thesis lands in the first fifteen seconds.

---

## The rule that quietly disqualifies people

> *must not include third party trademarks, or copyrighted music or other material unless the Entrant has permission to use such material.*

Two practical consequences:

- **Use no music at all**, or a track you can name the license for. Voiceover over silence is safer and, for a technical demo, better — every second of music is a second not explaining something.
- **Clean your screen before recording.** Close every unrelated tab. Browser chrome showing logged-in third-party services, a Slack notification, a bookmarks bar full of logos, an IDE splash screen — these are third-party trademarks on screen. Record in a fresh browser profile with no extensions and no bookmarks bar.
- Naming Nebius and NVIDIA is fine and expected — you are identifying the platforms you built on, which is nominative use. Do not restyle their logos or imply endorsement.

## Setup before you hit record

```bash
# Legible event pacing — the fan-out is the point, and at full speed it is a blur.
export PRINCIPAL_SLOW_MO_MS=250

# Deterministic run from a recorded trace if the network is unreliable on the day.
# export PRINCIPAL_REPLAY=runs/<job-id>
```

- Terminal at ~16pt, high contrast, window ~1600×900. Do not record a 4K screen scaled down; text becomes mush at YouTube's bitrate.
- Do a full dry run first and **keep the recording** as your fallback.
- Record at 1080p60 if you can — the fork tree animating is worth the frame rate.

---

## Shot list — 2:45

### 0:00–0:15 · The claim (cold open, no title card)

> *"This is a refactor that touches seven files. Watch — it will not open a pull request unless the tests pass. That guarantee is the whole project."*

**On screen:** the dashboard, idle, job form filled in. Do not open with a logo. The first frame should already be the product.

### 0:15–0:40 · The problem is width, and we can measure it

Run, live:

```bash
principal radius --repo tests/fixtures/mini_repo \
  --commit deadbeef --target src.auth.session.create
```

> *"Before touching anything, Principal maps the blast radius with tree-sitter. Seven files, nine call sites, two test files. Including an aliased import that grep would miss — and two dynamic dispatches it honestly reports it cannot prove, because a risk list that hides things is worse than no risk list."*

**On screen:** highlight `unresolved` — **exactly two entries**, not a wall of builtins. This is a small moment that tells a judge someone did the work.

### 0:40–1:05 · Baseline, and the refusal to start on red

> *"First, a verified green baseline in a Token Factory Sandbox — clone, install, run the whole suite. If the suite is already red, Principal aborts here. And because sandbox images are content-addressed, this install is paid once per job — every candidate forks from this one image instead of reinstalling."*

**On screen:** baseline events streaming; the `C0` node appearing at the root of the fork tree.

### 1:05–1:35 · The fan-out — the money shot

> *"Nemotron 3 Ultra plans the work. Then each task becomes a race: three independent candidates from Nemotron 3 Nano, in parallel, each in its own sandbox fork."*

**On screen:** the fork tree branching three ways per task. Let this breathe — it is the single most persuasive image in the video. `SLOW_MO_MS=250` exists for this shot.

> *"Two cheap gates first — scope and syntax — and they never touch a sandbox. That ordering is why fan-out is affordable: most bad candidates die for free."*

### 1:35–2:05 · First green wins, and nothing scores it

**On screen:** one candidate goes green; the others visibly grey out as they are cancelled.

> *"First to pass wins. There is no scoring function and no model in the accept path — the decision is a pytest exit code. That's enforced in CI: the gates package is forbidden from importing the models package."*

Cut to the terminal for two seconds:

```bash
make lint   # import-linter: "No model in the accept path" — KEPT
```

> *"A guarantee a machine checks, not a claim in a README."*

**Optional, ~8s, only if you have the room:** click a losing candidate to open the evidence drawer — its diff and its failing test output are preserved. *"Failures are kept, not hidden."*

### 2:05–2:30 · The two endings

> *"All patches applied together, full suite, one final run. Green — so a draft pull request, with an evidence table: what changed, which tests ran, coverage delta, and what a human still needs to check."*

**On screen:** the draft PR body.

Then — and do not skip this, it is the most credible thirty seconds in the video — switch to the NoPR run:

> *"And when it can't verify the change, this is what happens instead. No pull request. A report saying exactly where it stopped. NoPR is a success state: a tool that sometimes ships an unverified refactor is worth less than no tool at all."*

**On screen:** the NoPR report.

### 2:30–2:45 · Close

> *"Principal. Built on Nebius Token Factory — Sandboxes for verification, Nemotron 3 Nano, Super and Ultra for generation, repair and planning. Apache 2.0, and it runs with no credentials against a bundled fixture, so you can check all of this yourself in about two minutes."*

**On screen:** the repository URL, held still and readable for the full four seconds.

---

## What to cut if you run long

In this order:
1. The evidence-drawer moment (0:08)
2. The `make lint` cut (0:06) — but keep the *sentence*, it is the strongest claim in the video
3. Trim the baseline section to a single sentence (0:10)

**Never cut the NoPR ending.** Every other submission will show a success. Showing a correct refusal is what distinguishes a verification system from a code generator, and it is precisely what the judging criteria call *demonstrated understanding of the problem*.

## Before you upload

- [ ] Under 3:00
- [ ] Visibility set to **Public** (or Unlisted). Verify the link plays in a private window with no account signed in — a Private video fails judging silently.
- [ ] No music, or music whose license you can produce
- [ ] No third-party logos, notifications or browser chrome on screen
- [ ] Title includes the project and track, e.g. *"Principal — Autonomous Technical Debt Remediation Swarm | Nebius x NVIDIA Global AI Hackathon"*
- [ ] Description contains the repository URL and the license
- [ ] Captions on — judges may watch muted
- [ ] Link added to the Devpost submission form
