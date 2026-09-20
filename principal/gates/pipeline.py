"""The Verdict type and the total-function contract every gate obeys.

gates.evaluate is total. It returns a Verdict for every input, including
malformed diffs, empty diffs, timeouts and internal exceptions. It never raises.
Callers therefore never need a try block, which removes the class of bug where an
exception in a gate is caught upstream and treated as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VerdictKind(str, Enum):
    OK = "ok"
    SCOPE = "scope"
    SYNTAX = "syntax"
    RED = "red"
    BEHAVIOUR = "behaviour"
    SECURITY = "security"
    ERROR = "error"
    TIMEOUT = "timeout"
    # Gate 5 is differential: it compares findings before against findings after.
    # With no baseline scan there is nothing to subtract, so the gate has no
    # opinion — which is not the same as passing, and must not be recorded as one.
    INCONCLUSIVE = "inconclusive"


@dataclass(slots=True)
class Verdict:
    kind: VerdictKind
    gate: str
    reason: str = ""
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.kind is VerdictKind.OK

    @staticmethod
    def ok_() -> Verdict:
        return Verdict(VerdictKind.OK, gate="")

    @staticmethod
    def fail(kind: VerdictKind, gate: str, reason: str, **detail: object) -> Verdict:
        return Verdict(kind, gate, reason, detail)
