"""Artifact-only rendering. Used when the Reporter's model call fails, so the PR
is never blocked on prose it could not get."""

from __future__ import annotations

import re
from typing import Any

from principal.db.store import Task
from principal.outcome import Outcome, OutcomeReport


def render_verification_block(
    verdict: OutcomeReport, impact: dict[str, Any] | None, strength: dict[str, Any] | None,
) -> str:
    """What was and was not checked, in plain markdown, from recorded results only.

    It is placed above the model-written description, and nothing in it is
    generated: a reviewer should be able to trust this section even when every
    other line of the body came from a model.
    """
    lines = [f"## Verification: {verdict.outcome.value}", "", f"> {verdict.headline}"]

    if verdict.reasons:
        lines += ["", "**Needs a human decision before merge**"]
        lines += [f"- {r}" for r in verdict.reasons]

    lines += ["", "**What was checked**", "", "| Check | Result |", "|---|---|",
              "| Tests (every accepted patch together, in a sandbox) | passed |"]
    lines.append(f"| Blast radius | {_impact_line(impact)} |")
    lines.append(f"| Test strength | {_strength_line(strength)} |")

    if strength and strength.get("survivors"):
        lines += ["", "**Faults the tests did not catch** (a gap to inspect, not proof of a bug)"]
        for s in strength["survivors"][:5]:
            lines.append(f"- `{s['file']}:{s['line']}` — {s['description']}")
    if strength and strength.get("status") == "measured":
        lines += ["", f"_{strength.get('disclaimer', '')}_"]

    if verdict.outcome is not Outcome.VERIFIED:
        lines += ["", "_This is a draft. Principal does not merge._"]
    return "\n".join(lines)


def _impact_line(impact: dict[str, Any] | None) -> str:
    if not impact:
        return "not run"
    expected, actual = len(impact.get("expected_files") or []), len(impact.get("actual_files") or [])
    sites = impact.get("expected_call_sites") or 0
    unchanged = len(impact.get("call_sites_unchanged") or [])
    warns = impact.get("warnings") or 0
    text = (f"predicted {expected} file(s), patch changes {actual}; "
            f"{sites} known call site(s), {sites - unchanged} updated")
    if impact.get("unexpected_files"):
        text += f"; **outside the radius: {', '.join(impact['unexpected_files'])}**"
    return text + (f"; {warns} warning(s)" if warns else "; no warnings")


def _strength_line(strength: dict[str, Any] | None) -> str:
    if not strength:
        return "not run"
    status = strength.get("status")
    if status == "measured":
        detected, survived = strength.get("detected") or 0, strength.get("survived") or 0
        return (f"{detected} of {detected + survived} injected faults in the changed lines were detected"
                f" ({strength.get('score', 0):.0%}, floor {strength.get('floor', 0):.0%})")
    return f"not measured — {strength.get('reason') or status}"


def safe_link(title: str, url: str) -> str:
    """A markdown link to a web page whose title the page's author chose. The title
    loses anything that could close the link, start a new block or, on GitHub,
    mention a user; a URL that is not plain http(s) is shown as text, never as a link."""
    text = " ".join(re.sub(r"[\[\]()<>`*_|\\@]", "", str(title)).split())[:120] or "untitled"
    if not re.fullmatch(r"https?://[^\s()<>\[\]`]+", str(url)):
        return f"{text} (unlinked: not an http(s) URL)"
    return f"[{text}]({url})"


def render_references_block(grounding: dict[str, Any] | None) -> str:
    """The web pages the planner and repairer were shown, or "" when there were none.
    Recorded, not generated, and labelled as context: no gate read any of them."""
    searches = [s for s in (grounding or {}).get("searches") or [] if s.get("results")]
    if not searches:
        return ""
    lines = ["**External references consulted** (Tavily web search; shown to the models as context, "
             "never read by a gate)", ""]
    seen: set[str] = set()
    for s in searches:
        for r in s["results"]:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            lines.append(f"- {safe_link(r['title'], r['url'])} — {s['stage']}")
    return "\n".join(lines)


def render_fallback_body(
    goal: str, verified: list[Task], discarded: list[Task], uncovered: list[str], *,
    characterisation_only: list[str] | None = None, characterisation_result: dict | None = None,
    relocated_symbols: list[str] | None = None, unresolved_call_sites: list[dict] | None = None,
) -> str:
    asked = [t for t in verified if t.kind != "cleanup"]
    cleanup = [t for t in verified if t.kind == "cleanup"]

    lines = [f"Automated migration: {goal}", "", "## Verified changes"]
    for t in asked:
        lines.append(f"- `{t.target_file}`: {t.instruction}")
    if cleanup:
        lines += ["", "## Cleanup (dead code this refactor orphaned, not part of the requested change)"]
        for t in cleanup:
            lines.append(f"- `{t.target_file}`: {t.instruction}")
    if discarded:
        lines += ["", "## Not migrated"]
        for t in discarded:
            lines.append(f"- `{t.target_file}`: {t.discard_reason or 'unknown reason'}")
    if uncovered:
        lines += ["", "## Risk: modified with no test coverage"]
        for f in uncovered:
            lines.append(f"- `{f}`")
    if characterisation_only:
        lines += ["", "## Risk: verified only by a generated test, not a pre-existing one"]
        mutation = (characterisation_result or {}).get("mutation") or {}
        score = mutation.get("score")
        note = f" (mutation score {score:.2f})" if isinstance(score, (int, float)) else ""
        for f in characterisation_only:
            lines.append(f"- `{f}`{note}")
    if relocated_symbols:
        lines += ["", "## Relocated symbols (moved, not removed — check the new import path)"]
        for s in relocated_symbols:
            lines.append(f"- `{s}`")
    if unresolved_call_sites:
        lines += ["", "## Call sites the graph could not resolve statically"]
        for s in unresolved_call_sites:
            lines.append(f"- `{s['file']}:{s['line']}` — {s['name']} ({s['reason']})")
    lines += ["", "---", "_Generated by Principal. Every claim above is backed by a sandbox operation id",
              "and a test log in the job's published trace._"]
    return "\n".join(lines)
