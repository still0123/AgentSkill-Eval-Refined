"""Logical run, physical attempt, and state-transition contracts."""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, computed_field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest, sha256_text, stable_sha256
from agentskill_eval_contracts.enums import AttemptStatus, EvaluationOutcome, ExecutionStatus
from agentskill_eval_contracts.experiment import SCHEMA_VERSION, SchemaVersion
from agentskill_eval_contracts.snapshots import EnvironmentFingerprint


class RunPlanFingerprint(FrozenModel):
    case_sha256: HexDigest
    grader_sha256: HexDigest
    platform_compiled_prompt_sha256: HexDigest
    upstream_config_sha256: HexDigest
    image_digest: Optional[str] = None

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def plan_sha256(self) -> str:
        return stable_sha256(self.model_dump(mode="json", exclude={"plan_sha256"}))


ALLOWED_RUN_TRANSITIONS: Dict[ExecutionStatus, FrozenSet[ExecutionStatus]] = {
    ExecutionStatus.CREATED: frozenset({ExecutionStatus.QUEUED, ExecutionStatus.CANCEL_REQUESTED}),
    ExecutionStatus.QUEUED: frozenset({ExecutionStatus.LEASED, ExecutionStatus.CANCEL_REQUESTED}),
    ExecutionStatus.LEASED: frozenset(
        {
            ExecutionStatus.PREPARING,
            ExecutionStatus.RETRY_WAIT,
            ExecutionStatus.CANCEL_REQUESTED,
            ExecutionStatus.INFRA_FAILED,
        }
    ),
    ExecutionStatus.PREPARING: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.RETRY_WAIT,
            ExecutionStatus.CANCEL_REQUESTED,
            ExecutionStatus.INFRA_FAILED,
        }
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.GRADING,
            ExecutionStatus.RETRY_WAIT,
            ExecutionStatus.CANCEL_REQUESTED,
            ExecutionStatus.INFRA_FAILED,
        }
    ),
    ExecutionStatus.GRADING: frozenset(
        {
            ExecutionStatus.PERSISTING,
            ExecutionStatus.RETRY_WAIT,
            ExecutionStatus.CANCEL_REQUESTED,
            ExecutionStatus.INFRA_FAILED,
        }
    ),
    ExecutionStatus.PERSISTING: frozenset(
        {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.RETRY_WAIT,
            ExecutionStatus.CANCEL_REQUESTED,
            ExecutionStatus.INFRA_FAILED,
        }
    ),
    ExecutionStatus.RETRY_WAIT: frozenset(
        {ExecutionStatus.QUEUED, ExecutionStatus.CANCEL_REQUESTED, ExecutionStatus.INFRA_FAILED}
    ),
    ExecutionStatus.CANCEL_REQUESTED: frozenset({ExecutionStatus.CANCELLED}),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.INFRA_FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
}


def validate_run_transition(current: ExecutionStatus, target: ExecutionStatus) -> None:
    """Raise when a requested logical run transition is not allowed."""
    if target not in ALLOWED_RUN_TRANSITIONS[current]:
        raise ValueError(f"invalid run transition: {current.value} -> {target.value}")


class Run(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    id: UUID
    experiment_id: UUID
    pair_block_id: UUID
    variant_id: UUID
    execution_status: ExecutionStatus = ExecutionStatus.CREATED
    evaluation_outcome: Optional[EvaluationOutcome] = None
    final_score: Optional[float] = Field(default=None, ge=0, le=1)
    lease_generation: int = Field(default=0, ge=0)
    active_attempt_id: Optional[UUID] = None
    active_grading_run_id: Optional[UUID] = None
    run_plan_fingerprint: RunPlanFingerprint
    selected_attempt_sha256: Optional[HexDigest] = None
    max_attempts: int = Field(default=3, ge=1)
    queued_at: Optional[AwareDatetime] = None
    finished_at: Optional[AwareDatetime] = None

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def idempotency_key(self) -> str:
        return sha256_text(f"{self.pair_block_id}{self.variant_id}")

    @model_validator(mode="after")
    def terminal_state_must_have_consistent_outcome(self) -> "Run":
        if self.execution_status == ExecutionStatus.COMPLETED:
            if self.evaluation_outcome is None or self.finished_at is None:
                raise ValueError("COMPLETED runs require an outcome and finished_at")
        elif self.execution_status == ExecutionStatus.INFRA_FAILED:
            if self.evaluation_outcome != EvaluationOutcome.INVALID or self.finished_at is None:
                raise ValueError("INFRA_FAILED runs require invalid outcome and finished_at")
        elif self.execution_status == ExecutionStatus.CANCELLED:
            if self.evaluation_outcome is not None or self.finished_at is None:
                raise ValueError("CANCELLED runs require finished_at and no evaluation outcome")
        elif self.finished_at is not None:
            raise ValueError("non-terminal runs cannot have finished_at")

        if self.evaluation_outcome == EvaluationOutcome.INVALID and self.final_score is not None:
            raise ValueError("invalid runs cannot have a final score")
        return self


class RunAttempt(FrozenModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    id: UUID
    run_id: UUID
    attempt_no: int = Field(ge=1)
    lease_generation: int = Field(ge=1)
    fencing_token: UUID
    status: AttemptStatus = AttemptStatus.CLAIMED
    worker_id: Optional[str] = None
    claimed_at: AwareDatetime
    finished_at: Optional[AwareDatetime] = None
    sandbox_ref: Dict[str, JsonValue] = Field(default_factory=dict)
    observed_fingerprint: Optional[EnvironmentFingerprint] = None
    error_code: Optional[str] = None
    error_detail: Dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def terminal_attempt_must_have_finished_at(self) -> "RunAttempt":
        terminal = {
            AttemptStatus.COMPLETED,
            AttemptStatus.FAILED,
            AttemptStatus.FENCED,
            AttemptStatus.CANCELLED,
        }
        if self.status in terminal and self.finished_at is None:
            raise ValueError("terminal attempts require finished_at")
        if self.status not in terminal and self.finished_at is not None:
            raise ValueError("non-terminal attempts cannot have finished_at")
        if self.finished_at is not None and self.finished_at < self.claimed_at:
            raise ValueError("finished_at cannot be earlier than claimed_at")
        if self.status == AttemptStatus.FAILED and not self.error_code:
            raise ValueError("FAILED attempts require error_code")
        return self
