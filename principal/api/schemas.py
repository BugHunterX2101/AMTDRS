"""Request and response models for the HTTP surface."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    repo_url: str
    commit_sha: str
    goal: str
    target_fqn: str = Field(description="the symbol to migrate; not inferred from the goal text")
    token_budget: int | None = None
    max_tasks: int | None = None
    dry_run: bool = False
    routine: str = Field(
        default="interface_evolution",
        description="'interface_evolution' (default) or 'relocation'. An unrecognised or"
                     " unimplemented name runs as interface_evolution rather than aborting the job.",
    )


class CreateJobResponse(BaseModel):
    job_id: str
    stream: str


class JobCounts(BaseModel):
    tasks: int
    settled: int
    verified: int
    discarded: int


class JobBudget(BaseModel):
    limit: int
    spent: int
    refusals: int


class RateLimitInfo(BaseModel):
    remaining_requests: int | None = None
    remaining_tokens: int | None = None


class RadiusSummary(BaseModel):
    files: int
    tests: int
    unresolved: int


class JobStatusResponse(BaseModel):
    job_id: str
    state: str
    stop_reason: str | None
    baseline_image: str | None
    counts: JobCounts
    budget: JobBudget
    rate_limit: RateLimitInfo
    radius: RadiusSummary | None = None
    pr_url: str | None = None


class ErrorEnvelope(BaseModel):
    error: dict
