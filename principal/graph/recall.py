"""Call-site recall: what fraction of a symbol's real consumers the blast radius
actually finds, measured against a hand-labelled ground truth rather than
asserted from the architecture.

The claim that Principal enumerates call sites rather than guessing is the
strongest technical differentiator this system has over a prompt-driven coding
agent, and without a measured number it is marketing rather than evidence. This
module is the instrument, not the number itself — the number comes from calling
`measure_recall` with a ground truth someone actually labelled by reading the
target repository, which `tests/unit/test_call_site_recall.py` does for the
bundled fixture.

One honest consequence, stated in advance rather than discovered later: overall
recall on a real repository will not be 100%, because of the dynamic cases. The
static/heuristic split exists so that a shortfall in overall recall can be
attributed to the part of the design that admits it is a guess, rather than
hiding inside one blended number.
"""

from __future__ import annotations

from dataclasses import dataclass

from principal.graph.radius import BlastRadius


@dataclass(frozen=True, slots=True)
class GroundTruthSite:
    """One real, hand-verified consumer of the target symbol.

    `static` records whether resolving this site requires only reading the
    source — an aliased module import still counts as static, because nothing
    about it depends on what happens at runtime. A registry lookup or a
    `getattr` does, and is `static=False`.
    """

    file: str
    line: int
    static: bool
    note: str = ""


@dataclass(slots=True)
class RecallResult:
    total: int
    found: int
    static_total: int
    static_found: int
    heuristic_total: int
    heuristic_found: int
    missed: list[GroundTruthSite]

    @property
    def recall(self) -> float:
        return self.found / self.total if self.total else 1.0

    @property
    def static_recall(self) -> float:
        return self.static_found / self.static_total if self.static_total else 1.0

    @property
    def heuristic_recall(self) -> float:
        """Not "resolved correctly" — heuristic sites are never resolved with
        certainty by design — but "flagged rather than silently dropped". A
        heuristic site that vanishes from the radius entirely is the actual
        failure mode this metric exists to catch.
        """
        return self.heuristic_found / self.heuristic_total if self.heuristic_total else 1.0

    def to_payload(self) -> dict[str, object]:
        return {
            "recall": self.recall,
            "static_recall": self.static_recall,
            "heuristic_recall": self.heuristic_recall,
            "total": self.total, "found": self.found,
            "static": {"total": self.static_total, "found": self.static_found},
            "heuristic": {"total": self.heuristic_total, "found": self.heuristic_found},
            "missed": [{"file": s.file, "line": s.line, "note": s.note} for s in self.missed],
        }


def measure_recall(radius: BlastRadius, ground_truth: list[GroundTruthSite]) -> RecallResult:
    """Every ground-truth site is looked up by (file, line) only, never by the
    callee name the graph recorded — an aliased call resolves to the target
    under a *different* name (`make_session`, not `create`), and matching on
    name would make every alias look like a miss.
    """
    found_static = {(c["file"], c["line"]) for c in radius.call_sites if c["confidence"] == "static"}
    # A heuristic site is a hit if the radius flagged it at all, whichever of
    # the two lists carried it — some heuristic edges surface as low-confidence
    # call_sites, others only as unresolved/dynamic entries.
    found_heuristic = {(c["file"], c["line"]) for c in radius.call_sites if c["confidence"] == "heuristic"}
    found_heuristic |= {(u["file"], u["line"]) for u in radius.unresolved}

    found = 0
    static_found = 0
    heuristic_found = 0
    missed: list[GroundTruthSite] = []

    for site in ground_truth:
        key = (site.file, site.line)
        hit = key in found_static or key in found_heuristic
        if site.static:
            if key in found_static:
                static_found += 1
                found += 1
            else:
                missed.append(site)
        else:
            if hit:
                heuristic_found += 1
                found += 1
            else:
                missed.append(site)

    static_total = sum(1 for s in ground_truth if s.static)
    heuristic_total = len(ground_truth) - static_total

    return RecallResult(
        total=len(ground_truth), found=found, static_total=static_total, static_found=static_found,
        heuristic_total=heuristic_total, heuristic_found=heuristic_found, missed=missed,
    )
