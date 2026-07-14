"""Persisted contracts for leakage-safe benchmark-guided Skill search."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest


class OptimizationJobStatus(str, Enum):
    CREATED = "CREATED"
    SEARCHING = "SEARCHING"
    VALIDATING = "VALIDATING"
    FROZEN = "FROZEN"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SkillCandidateStatus(str, Enum):
    CREATED = "CREATED"
    LINTED = "LINTED"
    SCREENED = "SCREENED"
    PROMOTED = "PROMOTED"
    FULL_VALIDATED = "FULL_VALIDATED"
    FROZEN = "FROZEN"
    ELIMINATED = "ELIMINATED"
    REJECTED = "REJECTED"


class CandidateOrigin(str, Enum):
    ORIGINAL = "original"
    MANUAL = "manual"
    RANDOM = "random"
    SEARCH = "search"


class SearchEvaluationStage(str, Enum):
    SUBSET = "validation_subset"
    FULL = "validation_search_full"
    REGRESSION_DEV = "regression_dev"
    VALIDATION_CONFIRM = "validation_confirm"
    LOCKED_TEST = "locked_test"


ALLOWED_SKILL_CANDIDATE_TRANSITIONS = {
    None: {SkillCandidateStatus.CREATED},
    SkillCandidateStatus.CREATED: {
        SkillCandidateStatus.LINTED,
        SkillCandidateStatus.REJECTED,
    },
    SkillCandidateStatus.LINTED: {
        SkillCandidateStatus.SCREENED,
        SkillCandidateStatus.REJECTED,
    },
    SkillCandidateStatus.SCREENED: {
        SkillCandidateStatus.PROMOTED,
        SkillCandidateStatus.ELIMINATED,
    },
    SkillCandidateStatus.PROMOTED: {
        SkillCandidateStatus.FULL_VALIDATED,
        SkillCandidateStatus.REJECTED,
    },
    SkillCandidateStatus.FULL_VALIDATED: {
        SkillCandidateStatus.FROZEN,
        SkillCandidateStatus.ELIMINATED,
    },
    SkillCandidateStatus.FROZEN: set(),
    SkillCandidateStatus.ELIMINATED: set(),
    SkillCandidateStatus.REJECTED: set(),
}


class SkillLintResult(FrozenModel):
    name: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)


class SearchCaseResult(FrozenModel):
    case_id: str = Field(min_length=1)
    passed: bool
    score: float = Field(ge=0, le=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cost_microusd: Optional[int] = Field(default=None, ge=0)


class CandidateEvaluation(FrozenModel):
    stage: SearchEvaluationStage
    dataset_sha256: HexDigest
    evaluator_sha256: HexDigest
    case_ids: Tuple[str, ...] = Field(min_length=1)
    results: Tuple[SearchCaseResult, ...] = Field(min_length=1)
    pass_rate: float = Field(ge=0, le=1)
    mean_score: float = Field(ge=0, le=1)
    total_tokens: int = Field(ge=0)
    total_latency_ms: int = Field(ge=0)
    total_cost_microusd: Optional[int] = Field(default=None, ge=0)
    simulated: bool
    evaluated_at: datetime

    @model_validator(mode="after")
    def cases_and_aggregates_match(self) -> "CandidateEvaluation":
        if tuple(item.case_id for item in self.results) != self.case_ids:
            raise ValueError("result case order must match case_ids")
        size = len(self.results)
        expected_pass_rate = sum(item.passed for item in self.results) / size
        expected_score = sum(item.score for item in self.results) / size
        if abs(self.pass_rate - expected_pass_rate) > 1e-12:
            raise ValueError("pass_rate does not match results")
        if abs(self.mean_score - expected_score) > 1e-12:
            raise ValueError("mean_score does not match results")
        if self.total_tokens != sum(
            item.input_tokens + item.output_tokens for item in self.results
        ):
            raise ValueError("total_tokens does not match results")
        if self.total_latency_ms != sum(item.latency_ms for item in self.results):
            raise ValueError("total_latency_ms does not match results")
        observed_costs = [item.cost_microusd for item in self.results]
        if any(value is None for value in observed_costs):
            if self.total_cost_microusd is not None:
                raise ValueError("partial cost observations require null total cost")
        elif self.total_cost_microusd != sum(
            value for value in observed_costs if value is not None
        ):
            raise ValueError("total_cost_microusd does not match results")
        return self


class SkillCandidateTransition(FrozenModel):
    sequence: int = Field(ge=1)
    from_status: Optional[SkillCandidateStatus]
    to_status: SkillCandidateStatus
    occurred_at: datetime
    actor: str = Field(min_length=1)
    input_sha256: HexDigest
    output_sha256: HexDigest
    reason: Optional[str] = None


class SkillCandidate(FrozenModel):
    schema_version: Literal["ase/skill-candidate/v1alpha1"] = (
        "ase/skill-candidate/v1alpha1"
    )
    id: UUID
    job_id: UUID
    name: str = Field(min_length=1)
    origin: CandidateOrigin
    parent_id: Optional[UUID] = None
    mutation_ids: Tuple[str, ...] = ()
    content_sha256: HexDigest
    content_bytes: int = Field(ge=1)
    status: SkillCandidateStatus
    lint_results: Tuple[SkillLintResult, ...] = ()
    evaluations: Tuple[CandidateEvaluation, ...] = ()
    pareto_dominated_by: Tuple[UUID, ...] = ()
    elimination_reason: Optional[str] = None
    transitions: Tuple[SkillCandidateTransition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def lifecycle_is_valid(self) -> "SkillCandidate":
        previous: Optional[SkillCandidateStatus] = None
        for sequence, transition in enumerate(self.transitions, start=1):
            if transition.sequence != sequence or transition.from_status != previous:
                raise ValueError("candidate transition history is not contiguous")
            if transition.to_status not in ALLOWED_SKILL_CANDIDATE_TRANSITIONS[previous]:
                raise ValueError(f"illegal candidate transition {previous}->{transition.to_status}")
            previous = transition.to_status
        if previous != self.status:
            raise ValueError("last transition must equal candidate status")
        if (
            self.status in {SkillCandidateStatus.ELIMINATED, SkillCandidateStatus.REJECTED}
            and not self.elimination_reason
        ):
            raise ValueError("eliminated/rejected candidate requires a reason")
        if self.status == SkillCandidateStatus.FROZEN and not any(
            item.stage == SearchEvaluationStage.FULL for item in self.evaluations
        ):
            raise ValueError("frozen winner requires full validation")
        return self


class OptimizationJob(FrozenModel):
    schema_version: Literal["ase/optimization-job/v1alpha1"] = (
        "ase/optimization-job/v1alpha1"
    )
    id: UUID
    status: OptimizationJobStatus
    spec_sha256: HexDigest
    base_skill_sha256: HexDigest
    validation_dataset_sha256: HexDigest
    evaluator_sha256: HexDigest
    candidate_ids: Tuple[UUID, ...]
    evaluation_budget: int = Field(ge=1)
    evaluations_used: int = Field(ge=0)
    simulated: bool
    locked_test_accessed: Literal[False] = False
    frozen_winner_id: Optional[UUID] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    metadata: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def frozen_job_has_winner(self) -> "OptimizationJob":
        if self.status == OptimizationJobStatus.FROZEN and self.frozen_winner_id is None:
            raise ValueError("frozen optimization job requires frozen_winner_id")
        if self.evaluations_used > self.evaluation_budget:
            raise ValueError("evaluation budget exceeded")
        return self
