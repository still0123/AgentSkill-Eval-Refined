"""Persisted contracts for independent confirmation and locked-test evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest
from agentskill_eval_contracts.optimizer import CandidateEvaluation, SearchEvaluationStage


class FinalEvaluationStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FinalEvaluationStage(str, Enum):
    VALIDATION_CONFIRM = "validation_confirm"
    LOCKED_TEST = "locked_test"


class FinalDecision(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    REGRESSION = "REGRESSION"
    DESCRIPTIVE_ONLY = "DESCRIPTIVE_ONLY"


class PairClassification(str, Enum):
    WIN = "WIN"
    TIE_POSITIVE = "TIE_POSITIVE"
    TIE_NEGATIVE = "TIE_NEGATIVE"
    LOSS = "LOSS"


class FinalCaseComparison(FrozenModel):
    case_id: str = Field(min_length=1)
    independence_group: str = Field(min_length=1)
    base_pass_rate: float = Field(ge=0, le=1)
    winner_pass_rate: float = Field(ge=0, le=1)
    base_mean_score: float = Field(ge=0, le=1)
    winner_mean_score: float = Field(ge=0, le=1)
    classification: PairClassification


class FinalEvaluationJob(FrozenModel):
    schema_version: Literal["ase/final-evaluation-job/v1alpha1"] = (
        "ase/final-evaluation-job/v1alpha1"
    )
    id: UUID
    optimization_job_id: UUID
    optimization_job_sha256: HexDigest
    status: FinalEvaluationStatus
    stage: FinalEvaluationStage
    spec_sha256: HexDigest
    dataset_sha256: HexDigest
    evaluator_sha256: HexDigest
    base_skill_sha256: HexDigest
    winner_skill_sha256: HexDigest
    winner_candidate_id: UUID
    repeats: int = Field(ge=1)
    simulated: bool
    created_at: datetime
    completed_at: Optional[datetime] = None
    decision: Optional[FinalDecision] = None

    @model_validator(mode="after")
    def completion_is_consistent(self) -> "FinalEvaluationJob":
        if self.status == FinalEvaluationStatus.COMPLETED:
            if self.completed_at is None or self.decision is None:
                raise ValueError("completed final evaluation requires time and decision")
        elif self.completed_at is not None or self.decision is not None:
            raise ValueError("non-completed final evaluation cannot carry a decision")
        return self


class FinalEvaluationReport(FrozenModel):
    schema_version: Literal["ase/final-evaluation-report/v1alpha1"] = (
        "ase/final-evaluation-report/v1alpha1"
    )
    job: FinalEvaluationJob
    base_evaluations: Tuple[CandidateEvaluation, ...] = Field(min_length=1)
    winner_evaluations: Tuple[CandidateEvaluation, ...] = Field(min_length=1)
    cases: Tuple[FinalCaseComparison, ...] = Field(min_length=1)
    base_pass_rate: float = Field(ge=0, le=1)
    winner_pass_rate: float = Field(ge=0, le=1)
    absolute_gain: float = Field(ge=-1, le=1)
    gain_ci_low: float = Field(ge=-1, le=1)
    gain_ci_high: float = Field(ge=-1, le=1)
    bootstrap_resamples: int = Field(ge=100)
    bootstrap_seed: int
    token_overhead_ratio: Optional[float]
    win_count: int = Field(ge=0)
    tie_positive_count: int = Field(ge=0)
    tie_negative_count: int = Field(ge=0)
    loss_count: int = Field(ge=0)
    independent_group_count: int = Field(ge=1)
    decision: FinalDecision
    decision_reason: str = Field(min_length=1)
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def report_matches_completed_job(self) -> "FinalEvaluationReport":
        if self.job.status != FinalEvaluationStatus.COMPLETED:
            raise ValueError("final report requires a completed job")
        if self.job.decision != self.decision:
            raise ValueError("report decision must match job decision")
        if len(self.base_evaluations) != self.job.repeats:
            raise ValueError("base evaluations must match repeats")
        if len(self.winner_evaluations) != self.job.repeats:
            raise ValueError("winner evaluations must match repeats")
        classified = (
            self.win_count
            + self.tie_positive_count
            + self.tie_negative_count
            + self.loss_count
        )
        if classified != len(self.cases):
            raise ValueError("W/T/L counts must match case comparisons")
        expected_stage = (
            SearchEvaluationStage.VALIDATION_CONFIRM
            if self.job.stage == FinalEvaluationStage.VALIDATION_CONFIRM
            else SearchEvaluationStage.LOCKED_TEST
        )
        evaluations = (*self.base_evaluations, *self.winner_evaluations)
        expected_cases = tuple(item.case_id for item in self.cases)
        for evaluation in evaluations:
            if evaluation.stage != expected_stage:
                raise ValueError("evaluation stage must match final job stage")
            if evaluation.dataset_sha256 != self.job.dataset_sha256:
                raise ValueError("evaluation dataset must match final job")
            if evaluation.evaluator_sha256 != self.job.evaluator_sha256:
                raise ValueError("evaluation evaluator must match final job")
            if evaluation.simulated != self.job.simulated:
                raise ValueError("evaluation simulation flag must match final job")
            if evaluation.case_ids != expected_cases:
                raise ValueError("all final evaluations require the same ordered cases")
        return self


class LockedTestReceipt(FrozenModel):
    schema_version: Literal["ase/locked-test-receipt/v1alpha1"] = (
        "ase/locked-test-receipt/v1alpha1"
    )
    optimization_job_id: UUID
    final_evaluation_job_id: UUID
    dataset_sha256: HexDigest
    evaluator_sha256: HexDigest
    consumed_at: datetime
