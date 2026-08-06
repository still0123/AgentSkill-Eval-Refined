"""Contracts for auditable observed-Agent and process-integration evidence."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest, stable_sha256
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
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ExecutableSnapshot(FrozenModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: HexDigest


class RealPreflightReport(FrozenModel):
    config_sha256: HexDigest
    dataset_version_id: UUID
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_sha256: HexDigest
    case_ids: Tuple[str, ...] = Field(min_length=1)
    skill_sha256: HexDigest
    baseline_skill_sha256: Optional[HexDigest] = None
    runner: ExecutableSnapshot
    agent: ExecutableSnapshot
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    simulated: bool
    evidence_class: RealEvidenceClass
    smoke_runs: int = Field(ge=2)
    evidence_runs: int = Field(ge=6)
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
    reused_runs: int = Field(default=0, ge=0)
    observed_or_reserved_cost_microusd: int = Field(ge=0)
    started_at: datetime
    completed_at: Optional[datetime] = None
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "RealEvidenceRunManifest":
        terminal = self.status in {
            RealEvidenceStatus.COMPLETED,
            RealEvidenceStatus.BUDGET_EXHAUSTED,
            RealEvidenceStatus.CANCELLED,
            RealEvidenceStatus.FAILED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal real-evidence status requires completed_at")
        if (
            self.completed_runs + self.invalid_runs > self.planned_runs
            or self.planned_runs > self.max_agent_runs
        ):
            raise ValueError("real-evidence run counts exceed authorization")
        if (
            self.status == RealEvidenceStatus.COMPLETED
            and self.completed_runs + self.invalid_runs != self.planned_runs
        ):
            raise ValueError("completed real evidence requires every planned run")
        if self.reused_runs > self.completed_runs + self.invalid_runs:
            raise ValueError("reused runs cannot exceed terminal runs")
        if self.observed_or_reserved_cost_microusd > self.max_cost_microusd:
            raise ValueError("real-evidence cost exceeds authorization")
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
    classification: str = Field(pattern=r"^(win|tie_positive|tie_negative|loss|invalid)$")

    @model_validator(mode="after")
    def result_is_consistent(self) -> "RealCaseEvidence":
        if abs(self.absolute_gain - (self.treatment_pass_rate - self.baseline_pass_rate)) > 1e-12:
            raise ValueError("real case gain does not match pass rates")
        if self.classification == "invalid":
            return self
        if self.treatment_pass_rate > self.baseline_pass_rate:
            expected = "win"
        elif self.treatment_pass_rate < self.baseline_pass_rate:
            expected = "loss"
        elif self.treatment_pass_rate >= 0.5:
            expected = "tie_positive"
        else:
            expected = "tie_negative"
        if self.classification != expected:
            raise ValueError("real case classification does not match pass rates")
        return self


class RealExperimentReport(FrozenModel):
    run: RealEvidenceRunManifest
    preflight: Optional[RealPreflightReport] = None
    dataset_version_id: UUID
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_sha256: HexDigest
    runner_snapshot: RunnerSnapshot
    agent_snapshot: AgentSnapshot
    skill_sha256: HexDigest
    baseline_skill_sha256: Optional[HexDigest] = None
    baseline_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    treatment_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    absolute_gain: Optional[float] = Field(default=None, ge=-1, le=1)
    wtl: Dict[str, int]
    invalid_runs: int = Field(ge=0)
    token_summary: Dict[str, JsonValue]
    latency_summary: Dict[str, JsonValue]
    cost_summary: Dict[str, JsonValue]
    cases: Tuple[RealCaseEvidence, ...] = Field(min_length=1)
    attempt_evidence_paths: Tuple[str, ...]
    capability_unavailable: Tuple[str, ...]
    replay_bundle_path: str = Field(min_length=1)
    replay_bundle_sha256: HexDigest
    simulated: bool
    evidence_class: RealEvidenceClass
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    real_run_confirmed: bool
    statistics_semantics_version: Optional[Literal["ase/statistics/v0.4"]] = None
    primary_estimand: Literal["assignment_based_conservative"] = "assignment_based_conservative"
    valid_block_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    inference_ready: bool = False
    capability_baseline_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    capability_treatment_pass_rate: Optional[float] = Field(default=None, ge=0, le=1)
    capability_absolute_gain: Optional[float] = Field(default=None, ge=-1, le=1)
    inference_note: str = Field(min_length=1)
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def report_matches_run(self) -> "RealExperimentReport":
        if self.run.status != RealEvidenceStatus.COMPLETED:
            raise ValueError("real experiment report requires a completed run")
        for field in ("simulated", "evidence_class", "provider", "model", "real_run_confirmed"):
            if getattr(self, field) != getattr(self.run, field):
                raise ValueError(f"report {field} must match run manifest")
        if self.statistics_semantics_version is not None:
            self._validate_statistics_semantics()
        return self

    def _validate_statistics_semantics(self) -> None:
        if self.preflight is None:
            raise ValueError("v0.4 report requires typed preflight evidence")
        preflight = self.preflight
        if (
            self.run.config_sha256 != preflight.config_sha256
            or self.run.preflight_sha256
            != stable_sha256(preflight.model_dump(mode="json"))
        ):
            raise ValueError("report preflight identity does not match run manifest")
        if (
            self.run.simulated != preflight.simulated
            or self.run.evidence_class != preflight.evidence_class
            or self.run.provider != preflight.provider
            or self.run.model != preflight.model
        ):
            raise ValueError("report preflight execution identity does not match run")
        if (
            self.dataset_version_id != preflight.dataset_version_id
            or self.dataset_name != preflight.dataset_name
            or self.dataset_version != preflight.dataset_version
            or self.dataset_sha256 != preflight.dataset_sha256
        ):
            raise ValueError("report dataset identity does not match preflight")
        if (
            self.runner_snapshot.version != preflight.runner.version
            or self.runner_snapshot.binary_sha256 != preflight.runner.sha256
            or self.runner_snapshot.config.get("agent_executable_sha256")
            != preflight.agent.sha256
            or self.agent_snapshot.engine_version != preflight.agent.version
            or self.agent_snapshot.model != preflight.model
            or self.skill_sha256 != preflight.skill_sha256
            or self.baseline_skill_sha256 != preflight.baseline_skill_sha256
        ):
            raise ValueError("report executable or Skill identity does not match preflight")
        if self.invalid_runs != self.run.invalid_runs:
            raise ValueError("report invalid runs must match run manifest")
        if len(self.attempt_evidence_paths) != self.run.planned_runs:
            raise ValueError("report must reference every planned attempt")
        if len(self.attempt_evidence_paths) != len(set(self.attempt_evidence_paths)):
            raise ValueError("report attempt evidence paths must be unique")
        for value in self.attempt_evidence_paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or str(path) != value:
                raise ValueError("report attempt evidence path is unsafe")
        expected_keys = {"win", "tie_positive", "tie_negative", "loss", "invalid"}
        if set(self.wtl) != expected_keys:
            raise ValueError("v0.4 W/T/L requires explicit invalid count")
        counts = {key: 0 for key in expected_keys}
        for case in self.cases:
            counts[case.classification] += 1
        if counts != self.wtl:
            raise ValueError("report W/T/L counts do not match cases")
        if self.invalid_runs and self.inference_ready:
            raise ValueError("invalid observations cannot support confirmatory inference")
        if self.valid_block_ratio is None:
            raise ValueError("v0.4 statistics require valid_block_ratio")
        if (self.invalid_runs == 0) != (self.valid_block_ratio == 1):
            raise ValueError("valid_block_ratio contradicts invalid run count")
        case_ids = tuple(item.case_id for item in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("real report case IDs must be unique")
        if set(case_ids) != set(preflight.case_ids):
            raise ValueError("real report cases do not match preflight")

        capability = (
            self.capability_baseline_pass_rate,
            self.capability_treatment_pass_rate,
            self.capability_absolute_gain,
        )
        if any(item is None for item in capability):
            raise ValueError("v0.4 statistics require complete valid-only sensitivity")
        assert self.capability_baseline_pass_rate is not None
        assert self.capability_treatment_pass_rate is not None
        assert self.capability_absolute_gain is not None
        if (
            abs(
                self.capability_absolute_gain
                - (self.capability_treatment_pass_rate - self.capability_baseline_pass_rate)
            )
            > 1e-12
        ):
            raise ValueError("valid-only sensitivity gain does not match pass rates")

        grouped: Dict[str, list[RealCaseEvidence]] = {}
        for case in self.cases:
            grouped.setdefault(case.independence_group, []).append(case)
        baseline = [
            sum(item.baseline_pass_rate for item in rows) / len(rows) for rows in grouped.values()
        ]
        treatment = [
            sum(item.treatment_pass_rate for item in rows) / len(rows) for rows in grouped.values()
        ]
        baseline_rate = sum(baseline) / len(baseline)
        treatment_rate = sum(treatment) / len(treatment)
        if (
            self.baseline_pass_rate is None
            or self.treatment_pass_rate is None
            or self.absolute_gain is None
            or abs(self.baseline_pass_rate - baseline_rate) > 1e-12
            or abs(self.treatment_pass_rate - treatment_rate) > 1e-12
            or abs(self.absolute_gain - (treatment_rate - baseline_rate)) > 1e-12
        ):
            raise ValueError("report assignment-based estimates do not match cases")
