"""Persisted contracts for independent confirmation and locked-test evaluation."""

from __future__ import annotations

import random
from datetime import datetime
from enum import Enum
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest
from agentskill_eval_contracts.optimizer import (
    CandidateEvaluation,
    SearchCaseResult,
    SearchEvaluationStage,
)


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
    INVALID = "INVALID"


class PairClassification(str, Enum):
    WIN = "WIN"
    TIE_POSITIVE = "TIE_POSITIVE"
    TIE_NEGATIVE = "TIE_NEGATIVE"
    LOSS = "LOSS"
    INVALID = "INVALID"


class FinalCaseComparison(FrozenModel):
    case_id: str = Field(min_length=1)
    independence_group: str = Field(min_length=1)
    base_pass_rate: float = Field(ge=0, le=1)
    winner_pass_rate: float = Field(ge=0, le=1)
    base_mean_score: float = Field(ge=0, le=1)
    winner_mean_score: float = Field(ge=0, le=1)
    classification: PairClassification


class FinalEvaluationJob(FrozenModel):
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
    decision_rule_version: Optional[Literal["ase/final-decision/v0.4"]] = None
    min_absolute_gain: Optional[float] = Field(default=None, ge=0, le=1)
    max_loss_cases: Optional[int] = Field(default=None, ge=0)
    max_token_overhead_ratio: Optional[float] = Field(default=None, ge=0)
    min_independent_groups: Optional[int] = Field(default=None, ge=1)
    token_overhead_ratio: Optional[float]
    win_count: int = Field(ge=0)
    tie_positive_count: int = Field(ge=0)
    tie_negative_count: int = Field(ge=0)
    loss_count: int = Field(ge=0)
    invalid_count: int = Field(default=0, ge=0)
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
            + self.invalid_count
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
        if len(expected_cases) != len(set(expected_cases)):
            raise ValueError("final case IDs must be unique")
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
        if self.decision_rule_version is not None:
            self._validate_decision_semantics()
        return self

    def _validate_decision_semantics(self) -> None:
        gates = (
            self.min_absolute_gain,
            self.max_loss_cases,
            self.max_token_overhead_ratio,
            self.min_independent_groups,
        )
        if any(item is None for item in gates):
            raise ValueError("v0.4 final decision requires complete gate inputs")

        base_rows: Dict[str, list[SearchCaseResult]] = {item.case_id: [] for item in self.cases}
        winner_rows: Dict[str, list[SearchCaseResult]] = {item.case_id: [] for item in self.cases}
        for evaluation in self.base_evaluations:
            for result in evaluation.results:
                base_rows[result.case_id].append(result)
        for evaluation in self.winner_evaluations:
            for result in evaluation.results:
                winner_rows[result.case_id].append(result)

        counts = {item: 0 for item in PairClassification}
        grouped: Dict[str, list[FinalCaseComparison]] = {}
        for comparison in self.cases:
            base = base_rows[comparison.case_id]
            winner = winner_rows[comparison.case_id]
            base_pass = sum(item.passed for item in base) / len(base)
            winner_pass = sum(item.passed for item in winner) / len(winner)
            base_score = sum(item.score for item in base) / len(base)
            winner_score = sum(item.score for item in winner) / len(winner)
            if any(item.outcome == "invalid" for item in (*base, *winner)):
                classification = PairClassification.INVALID
            elif winner_pass > base_pass:
                classification = PairClassification.WIN
            elif winner_pass < base_pass:
                classification = PairClassification.LOSS
            elif winner_pass >= 0.5:
                classification = PairClassification.TIE_POSITIVE
            else:
                classification = PairClassification.TIE_NEGATIVE
            observed = (
                comparison.base_pass_rate,
                comparison.winner_pass_rate,
                comparison.base_mean_score,
                comparison.winner_mean_score,
            )
            expected = (base_pass, winner_pass, base_score, winner_score)
            if any(abs(left - right) > 1e-12 for left, right in zip(observed, expected)):
                raise ValueError("final case aggregates do not match evaluations")
            if comparison.classification != classification:
                raise ValueError("final case classification does not match evaluations")
            counts[classification] += 1
            grouped.setdefault(comparison.independence_group, []).append(comparison)

        declared_counts = {
            PairClassification.WIN: self.win_count,
            PairClassification.TIE_POSITIVE: self.tie_positive_count,
            PairClassification.TIE_NEGATIVE: self.tie_negative_count,
            PairClassification.LOSS: self.loss_count,
            PairClassification.INVALID: self.invalid_count,
        }
        if counts != declared_counts:
            raise ValueError("final classification counts do not match cases")
        if self.independent_group_count != len(grouped):
            raise ValueError("final independent-group count does not match cases")

        group_base = [
            sum(item.base_pass_rate for item in rows) / len(rows) for rows in grouped.values()
        ]
        group_winner = [
            sum(item.winner_pass_rate for item in rows) / len(rows) for rows in grouped.values()
        ]
        base_pass_rate = sum(group_base) / len(group_base)
        winner_pass_rate = sum(group_winner) / len(group_winner)
        if (
            abs(self.base_pass_rate - base_pass_rate) > 1e-12
            or abs(self.winner_pass_rate - winner_pass_rate) > 1e-12
            or abs(self.absolute_gain - (winner_pass_rate - base_pass_rate)) > 1e-12
        ):
            raise ValueError("final report aggregates do not match cases")

        effects = [
            sum(item.winner_pass_rate - item.base_pass_rate for item in rows) / len(rows)
            for rows in grouped.values()
        ]
        rng = random.Random(self.bootstrap_seed)
        draws = sorted(
            sum(rng.choice(effects) for _ in effects) / len(effects)
            for _ in range(self.bootstrap_resamples)
        )
        expected_ci = (
            draws[int(0.025 * (self.bootstrap_resamples - 1))],
            draws[int(0.975 * (self.bootstrap_resamples - 1))],
        )
        if (
            abs(self.gain_ci_low - expected_ci[0]) > 1e-12
            or abs(self.gain_ci_high - expected_ci[1]) > 1e-12
        ):
            raise ValueError("final gain interval does not match cases")

        base_tokens = sum(item.total_tokens for item in self.base_evaluations)
        winner_tokens = sum(item.total_tokens for item in self.winner_evaluations)
        overhead = None if base_tokens == 0 else (winner_tokens - base_tokens) / base_tokens
        if overhead != self.token_overhead_ratio:
            raise ValueError("final token overhead does not match evaluations")

        assert self.max_loss_cases is not None
        assert self.max_token_overhead_ratio is not None
        assert self.min_independent_groups is not None
        assert self.min_absolute_gain is not None
        if self.invalid_count:
            decision = FinalDecision.INVALID
        elif self.loss_count > self.max_loss_cases or (
            overhead is not None and overhead > self.max_token_overhead_ratio
        ):
            decision = FinalDecision.REGRESSION
        elif self.independent_group_count < self.min_independent_groups:
            decision = FinalDecision.DESCRIPTIVE_ONLY
        elif self.absolute_gain > 0 and self.gain_ci_low >= self.min_absolute_gain:
            decision = FinalDecision.CONFIRMED
        else:
            decision = FinalDecision.NOT_CONFIRMED
        if self.decision != decision:
            raise ValueError("final decision does not match frozen gates")


class LockedTestReceipt(FrozenModel):
    optimization_job_id: UUID
    final_evaluation_job_id: UUID
    dataset_sha256: HexDigest
    evaluator_sha256: HexDigest
    consumed_at: datetime
