"""What a run's result *means*, in one place.

The job state machine says where a run stopped (Done, NoPR, NoSafetyNet, Aborted).
It does not say what that tells a reviewer, and the difference matters: a run can
stop in NoPR because a gate found a definite defect, because nothing could be
checked, or because the platform fell over, and those are three different
statements about the code. This module turns the signals a run leaves behind into
one of four outcomes and says why.

  VERIFIED    every gate that ran came back clean and nothing is left open. A draft
              pull request is offered.
  UNVERIFIED  Principal could not establish that the change is safe, and found no
              definite defect. Either tests pass but caveats remain open (a draft
              pull request is still offered, flagged for a human), or nothing could
              be checked or no candidate passed, and the repository is unchanged.
  FAILED      a gate found a definite defect in the change. Nothing is offered.
  ERROR       the run did not complete for a reason that says nothing about the
              change: the platform, a timeout, a crash. It is not evidence either
              way and is never counted against the patch.

The classification is a pure function of plain data. It does not read the
database, call a model or look at the clock, so the same signals always give the
same answer and the whole table is testable without a sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Written by the scheduler in front of any task discarded because the platform,
# not the patch, ended the evaluation. Read back here so an inconclusive run is
# never reported as a failed one.
INFRA_DISCARD_PREFIX = "infrastructure (inconclusive, not a verdict on the patch)"

TERMINAL_STATES = frozenset({"Done", "NoPR", "NoSafetyNet", "Aborted"})

# The most caveats listed in a headline. The full list is always in `reasons`.
_HEADLINE_REASONS = 3


class Outcome(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    ERROR = "ERROR"


@dataclass(slots=True)
class Signals:
    """Everything the classifier reads. Plain data, so it can be built from a live
    run, from a stored one, or by hand in a test."""

    state: str
    stop_reason: str | None = None
    pr_url: str | None = None
    verified_tasks: int = 0
    discarded_tasks: int = 0
    infrastructure_discards: int = 0
    impact: dict[str, Any] | None = None
    strength: dict[str, Any] | None = None
    security: dict[str, Any] | None = None
    integration: dict[str, Any] | None = None
    uncovered: list[str] = field(default_factory=list)
    characterisation_only: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OutcomeReport:
    outcome: Outcome
    cause: str
    headline: str
    reasons: list[str]
    pr_offered: bool
    repository_changed: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value, "cause": self.cause, "headline": self.headline,
            "reasons": self.reasons, "pr_offered": self.pr_offered,
            "repository_changed": self.repository_changed,
        }


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def classify(s: Signals) -> OutcomeReport | None:
    """None while the run is still going: an unfinished run has no outcome yet,
    and a placeholder would be read as one."""
    if not is_terminal(s.state):
        return None

    if s.state == "Aborted":
        return _error(s)
    if s.state == "NoSafetyNet":
        return _unverified(
            "no_safety_net",
            [s.stop_reason or "the target had no test coverage and a generated safety net could"
             " not be established"],
            "Principal declined: there was no reliable test to measure the change against.",
        )
    if s.state == "NoPR":
        return _classify_no_pr(s)
    return _classify_done(s)


# ---------------------------------------------------------------------- Done --


def _classify_done(s: Signals) -> OutcomeReport:
    caveats = open_caveats(s)
    pr = bool(s.pr_url)
    if not caveats:
        return OutcomeReport(
            Outcome.VERIFIED, "clean",
            "Verified: the tests pass and every check that ran came back clean.",
            [], pr_offered=pr, repository_changed=pr,
        )
    shown = "; ".join(caveats[:_HEADLINE_REASONS])
    more = f" (+{len(caveats) - _HEADLINE_REASONS} more)" if len(caveats) > _HEADLINE_REASONS else ""
    return OutcomeReport(
        Outcome.UNVERIFIED, "caveats",
        f"Unverified: the tests pass, but {len(caveats)} item(s) need a human decision: {shown}{more}.",
        caveats, pr_offered=pr, repository_changed=pr,
    )


def open_caveats(s: Signals) -> list[str]:
    """What remains unverified on a run whose tests passed. Every entry is
    something a reviewer would otherwise have to discover for themselves."""
    out: list[str] = []

    for f in _findings(s.impact, "warn"):
        out.append(f["detail"])

    st = s.strength or {}
    if st.get("below_floor"):
        detected, survived = st.get("detected") or 0, st.get("survived") or 0
        out.append(
            f"the tests detected {detected} of {detected + survived} injected faults in the changed"
            f" lines (score {_pct(st.get('score'))}, floor {_pct(st.get('floor'))}): the suite may not"
            " notice a real mistake there"
        )

    real_discards = s.discarded_tasks - s.infrastructure_discards
    if real_discards > 0:
        out.append(f"{real_discards} planned task(s) were not verified and were left out, so the"
                   " migration is partial")
    if s.infrastructure_discards:
        out.append(f"{s.infrastructure_discards} planned task(s) could not be evaluated because the"
                   " platform failed, so the migration is partial")

    for path in s.uncovered:
        out.append(f"{path} was modified and no test covers it")
    for path in s.characterisation_only:
        out.append(f"{path} is verified only by a generated test, not a pre-existing one")
    return out


# ---------------------------------------------------------------------- NoPR --


def _classify_no_pr(s: Signals) -> OutcomeReport:
    reason = s.stop_reason or "the run stopped without producing a verified change"

    if reason == "cancelled":
        return _unverified("cancelled", ["the run was cancelled before it finished"],
                           "Unverified: the run was cancelled, so nothing was checked to completion.")

    # A definite defect outranks everything below it: it is the one thing that is
    # known rather than missing.
    blocks = _findings(s.impact, "block")
    defects = [f for f in blocks if f.get("kind") != "uncheckable"]
    if defects:
        return _failed("impact", [f["detail"] for f in defects],
                       "Failed: the patch is incompatible with code outside the files it changed.")
    if (s.integration or {}).get("pass") is False:
        return _failed("integration", _integration_reasons(s.integration or {}, reason),
                       "Failed: the patches do not pass the checks together.")
    introduced = (s.security or {}).get("introduced") or []
    if introduced:
        return _failed(
            "security",
            [f"{x.get('rule')}: {x.get('message')} ({x.get('path')}:{x.get('line')})" for x in introduced[:5]],
            "Failed: the change introduces security findings that were not there before.",
        )

    uncheckable = [f for f in blocks if f.get("kind") == "uncheckable"]
    if uncheckable:
        return _unverified("uncheckable", [f["detail"] for f in uncheckable],
                           "Unverified: a dependency of the change could not be checked, so it was not offered.")

    # Nothing was ever judged because the platform failed: that is not a verdict on
    # the patch and must not read like one.
    if s.discarded_tasks and s.infrastructure_discards >= s.discarded_tasks and not s.verified_tasks:
        return OutcomeReport(
            Outcome.ERROR, "infrastructure",
            "Error: the sandbox could not evaluate the candidates. This is not evidence for or against"
            " the change; retry when the platform is healthy.",
            [reason], pr_offered=False, repository_changed=False,
        )

    return _unverified(
        "no_candidate" if not s.verified_tasks else "incomplete",
        [reason],
        "Unverified: no change was verified, so the repository was left unchanged.",
    )


def _integration_reasons(integration: dict[str, Any], fallback: str) -> list[str]:
    failing = integration.get("failing") or []
    if failing:
        return [f"failing: {', '.join(str(f) for f in failing[:5])}"]
    if integration.get("removed_undeclared"):
        return [f"removed without being declared: {', '.join(integration['removed_undeclared'][:5])}"]
    if integration.get("coverage_ok") is False:
        return ["line coverage dropped on a touched file"]
    if integration.get("test_count_ok") is False:
        return [f"the collected test count changed: {integration.get('baseline_collected')} ->"
                f" {integration.get('integration_collected')}"]
    return [fallback]


# --------------------------------------------------------------------- Error --


def _error(s: Signals) -> OutcomeReport:
    return OutcomeReport(
        Outcome.ERROR, "aborted",
        "Error: the run did not complete. This says nothing about whether the change is safe.",
        [s.stop_reason or "the run aborted"], pr_offered=False, repository_changed=False,
    )


def _unverified(cause: str, reasons: list[str], headline: str) -> OutcomeReport:
    return OutcomeReport(Outcome.UNVERIFIED, cause, headline, reasons,
                         pr_offered=False, repository_changed=False)


def _failed(cause: str, reasons: list[str], headline: str) -> OutcomeReport:
    return OutcomeReport(Outcome.FAILED, cause, headline, reasons,
                         pr_offered=False, repository_changed=False)


# ------------------------------------------------------------------- helpers --


def _findings(impact: dict[str, Any] | None, severity: str) -> list[dict[str, Any]]:
    return [f for f in (impact or {}).get("findings", []) if f.get("severity") == severity]


def _pct(value: Any) -> str:
    return f"{value:.0%}" if isinstance(value, (int, float)) else "n/a"
