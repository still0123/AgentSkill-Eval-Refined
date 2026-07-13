"""Contracts for auditable observed-Agent and process-integration evidence."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest
from agentskill_eval_contracts.snapshots import (
    AgentSnapshot,
    EnvironmentFingerprint,
    RunnerSnapshot,
)


class RealEvidenceClass(str, Enum):
    OBSERVED_AGENT = "observed_agent"
    PROCESS_INTEGRATION = "process_integration"


class RealRunMode(str, Enum):
    SMOKE = "smoke"
    EVIDENCE = "evidence"


class RealEvidenceStatus(str, Enum):
    PREFLIGHTED = "PREFLIGHTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"


class ExecutableSnapshot(FrozenModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: HexDigest


class RealPreflightReport(FrozenModel):
    schema_version: Literal["ase/real-preflight/v1alpha1"] = "ase/real-preflight/v1alpha1"
    config_sha256: HexDigest
    dataset_version_id: UUID
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_sha256: HexDigest
    case_ids: Tuple[str, ...] = Field(min_length=2)
    skill_sha256: HexDigest
    runner: ExecutableSnapshot
    agent: ExecutableSnapshot
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    simulated: bool
    evidence_class: RealEvidenceClass
    smoke_runs: int = Field(ge=4)
    evidence_runs: int = Field(ge=12)
    estimated_input_tokens_per_run: int = Field(ge=1)
    estimated_output_tokens_per_run: int = Field(ge=1)
    estimated_cost_per_run_microusd: int = Field(ge=1)
    secret_env_names: Tuple[str, ...]
    checked_at: datetime

    @model_validator(mode="after")
    def evidence_class_matches_simulation(self) -> "RealPreflightReport":
        expected = self.evidence_class == RealEvidenceClass.PROCESS_INTEGRATION
        if self.simulated != expected:
            raise ValueError("process integration must be simulated; observed Agent must be real")
        return self


class RealAttemptEvidence(FrozenModel):
    schema_version: Literal["ase/real-attempt-evidence/v1alpha1"] = (
        "ase/real-attempt-evidence/v1alpha1"
    )
    experiment_id: UUID
    run_id: UUID
    attempt_id: UUID
    simulated: bool
    evidence_class: RealEvidenceClass
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    real_run_confirmed: bool
    environment: EnvironmentFingerprint
    final_message_sha256: Optional[HexDigest] = None
    capability_unavailable: Tuple[str, ...]
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def confirmation_matches_class(self) -> "RealAttemptEvidence":
        observed = self.evidence_class == RealEvidenceClass.OBSERVED_AGENT
        if self.simulated == observed or self.real_run_confirmed != observed:
            raise ValueError("attempt simulation/confirmation flags contradict evidence class")
        return self


class RealEvidenceRunManifest(FrozenModel):
    schema_version: Literal["ase/real-evidence-run/v1alpha1"] = "ase/real-evidence-run/v1alpha1"
    experiment_id: UUID
    mode: RealRunMode
    status: RealEvidenceStatus
    config_sha256: HexDigest
    preflight_sha256: HexDigest
    simulated: bool
    evidence_class: RealEvidenceClass
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    real_run_confirmed: bool
    max_cost_microusd: int = Field(ge=1)
    max_agent_runs: int = Field(ge=1)
    planned_runs: int = Field(ge=1)
    completed_runs: int = Field(ge=0)
    invalid_runs: int = Field(ge=0)
    observed_or_reserved_cost_microusd: int = Field(ge=0)
    started_at: datetime
    completed_at: Optional[datetime] = None
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "RealEvidenceRunManifest":
        terminal = self.status in {
            RealEvidenceStatus.COMPLETED,
            RealEvidenceStatus.BUDGET_EXHAUSTED,
            RealEvidenceStatus.FAILED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal real-evidence status requires completed_at")
        if self.completed_runs > self.planned_runs or self.planned_runs > self.max_agent_runs:
            raise ValueError("real-evidence run counts exceed authorization")
        observed = self.evidence_class == RealEvidenceClass.OBSERVED_AGENT
        if self.simulated == observed or self.real_run_confirmed != observed:
            raise ValueError("run simulation/confirmation flags contradict evidence class")
        return self


class RealCaseEvidence(FrozenModel):
    case_id: str = Field(min_length=1)
    independence_group: str = Field(min_length=1)
    baseline_pass_rate: float = Field(ge=0, le=1)
    treatment_pass_rate: float = Field(ge=0, le=1)
    absolute_gain: float = Field(ge=-1, le=1)
    classification: str = Field(pattern=r"^(win|tie_positive|tie_negative|loss)$")


class RealExperimentReport(FrozenModel):
    schema_version: Literal["ase/real-experiment-report/v1alpha1"] = (
        "ase/real-experiment-report/v1alpha1"
    )
    run: RealEvidenceRunManifest
    dataset_version_id: UUID
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_sha256: HexDigest
    runner_snapshot: RunnerSnapshot
    agent_snapshot: AgentSnapshot
    skill_sha256: HexDigest
    baseline_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    treatment_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    absolute_gain: Optional[float] = Field(default=None, ge=-1, le=1)
    wtl: Dict[str, int]
    invalid_runs: int = Field(ge=0)
    token_summary: Dict[str, JsonValue]
    latency_summary: Dict[str, JsonValue]
    cost_summary: Dict[str, JsonValue]
    cases: Tuple[RealCaseEvidence, ...]
    attempt_evidence_paths: Tuple[str, ...]
    capability_unavailable: Tuple[str, ...]
    replay_bundle_path: str = Field(min_length=1)
    replay_bundle_sha256: HexDigest
    simulated: bool
    evidence_class: RealEvidenceClass
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    real_run_confirmed: bool
    inference_note: str = Field(min_length=1)
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def report_matches_run(self) -> "RealExperimentReport":
        if self.run.status != RealEvidenceStatus.COMPLETED:
            raise ValueError("real experiment report requires a completed run")
        for field in ("simulated", "evidence_class", "provider", "model", "real_run_confirmed"):
            if getattr(self, field) != getattr(self.run, field):
                raise ValueError(f"report {field} must match run manifest")
        return self
