"""Pydantic models for every agent's input and output.

Every agent is a pure function from a typed input to a validated output, with no
conversation history and no memory between calls.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class PlannedTask(BaseModel):
    seq: int
    target_file: str
    instruction: str = Field(description="what to change in this file, one sentence")
    acceptance: str = Field(description="how a reviewer would know it is done")
    declared_removals: list[str] = Field(
        default_factory=list,
        description="exported symbol names this task intentionally removes or renames",
    )
    depends_on: list[int] = Field(default_factory=list, description="seqs of tasks this depends on")


class Plan(BaseModel):
    tasks: list[PlannedTask]
    unhandled: list[str] = Field(
        default_factory=list,
        description="call sites deliberately not addressed, with reasons",
    )

    @field_validator("tasks")
    @classmethod
    def _non_empty(cls, v: list[PlannedTask]) -> list[PlannedTask]:
        if not v:
            raise ValueError("plan must contain at least one task")
        return v


class CoderOutput(BaseModel):
    """Not actually parsed as JSON — the Coder always uses the text protocol and
    diffparse.py extracts this shape from a sentinel block. Kept here as the
    documented contract and for tests that exercise the parser in isolation."""

    diff: str


class RepairKind(str, Enum):
    MISSED_CALL_SITE = "missed_call_site"     # feeds back into call-site recall
    SIGNATURE_MISMATCH = "signature_mismatch"
    IMPORT_ERROR = "import_error"
    TEST_EXPECTATION = "test_expectation"     # the test asserts the old behaviour
    OTHER = "other"


class RepairOutput(BaseModel):
    diff: str
    kind: RepairKind


class ReportedRisk(BaseModel):
    kind: str
    detail: str


class ReportOutput(BaseModel):
    title: str
    body: str
    risks: list[ReportedRisk] = Field(default_factory=list)
