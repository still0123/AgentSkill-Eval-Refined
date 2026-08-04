"""Persisted trace, diagnosis, and paired-difference contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from agentskill_eval_contracts.base import FrozenModel


class TraceAvailability(str, Enum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


class FailureLabel(str, Enum):
    TASK_UNDERSTANDING = "TASK_UNDERSTANDING"
    PLANNING = "PLANNING"
    TOOL_SELECTION = "TOOL_SELECTION"
    TOOL_ARGUMENT = "TOOL_ARGUMENT"
    TOOL_RECOVERY = "TOOL_RECOVERY"
    RETRIEVAL = "RETRIEVAL"
    MEMORY = "MEMORY"
    SKILL_CONFLICT = "SKILL_CONFLICT"
    VERIFICATION = "VERIFICATION"
    ENVIRONMENT = "ENVIRONMENT"
    BUDGET = "BUDGET"
    JUDGE = "JUDGE"
    UNKNOWN = "UNKNOWN"


class AttributionRole(str, Enum):
    ROOT_CAUSE = "root_cause"
    CONTRIBUTING_FACTOR = "contributing_factor"
    OBSERVED_SYMPTOM = "observed_symptom"


class TraceCapability(FrozenModel):
    name: str = Field(min_length=1)
    availability: TraceAvailability
    source: str = Field(min_length=1)
    reason: Optional[str] = None

    @model_validator(mode="after")
    def unavailable_capability_requires_reason(self) -> "TraceCapability":
        if self.availability == TraceAvailability.UNAVAILABLE and not self.reason:
            raise ValueError("unavailable trace capabilities require a reason")
        return self


class TraceEvent(FrozenModel):
    sequence_no: int = Field(ge=1)
    occurred_at: datetime
    kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    source: Literal["platform", "runner", "agent", "judge"]
    status: Optional[Literal["started", "completed", "failed", "cancelled"]] = None
    summary: Dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trace timestamps must be timezone-aware")
        return value


class TraceManifest(FrozenModel):
    run_id: UUID
    attempt_id: UUID
    capabilities: Tuple[TraceCapability, ...]
    events: Tuple[TraceEvent, ...] = ()

    @model_validator(mode="after")
    def sequence_and_capability_names_must_be_unique(self) -> "TraceManifest":
        sequences = [event.sequence_no for event in self.events]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("trace sequence numbers must be contiguous and start at one")
        names = [capability.name for capability in self.capabilities]
        if len(names) != len(set(names)):
            raise ValueError("trace capability names must be unique")
        return self


class DiagnosticFinding(FrozenModel):
    label: FailureLabel
    role: AttributionRole
    confidence: float = Field(ge=0, le=1)
    rule_id: str = Field(min_length=1)
    evidence_sequence_nos: Tuple[int, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_sequence_numbers_must_be_positive_unique_and_sorted(
        self,
    ) -> "DiagnosticFinding":
        values = self.evidence_sequence_nos
        if any(value < 1 for value in values):
            raise ValueError("diagnostic evidence sequence numbers must be positive")
        if tuple(sorted(set(values))) != values:
            raise ValueError("diagnostic evidence sequence numbers must be unique and sorted")
        return self


class FailureDiagnosis(FrozenModel):
    run_id: UUID
    attempt_id: UUID
    status: Literal["no_failure", "diagnosed", "abstained"]
    findings: Tuple[DiagnosticFinding, ...] = ()

    @model_validator(mode="after")
    def status_must_match_findings(self) -> "FailureDiagnosis":
        if self.status == "no_failure" and self.findings:
            raise ValueError("successful runs cannot contain failure findings")
        if self.status != "no_failure" and not self.findings:
            raise ValueError("failure diagnoses require at least one finding")
        if self.status == "abstained" and not any(
            finding.label == FailureLabel.UNKNOWN for finding in self.findings
        ):
            raise ValueError("abstained diagnoses require an UNKNOWN finding")
        return self


class EventCountDelta(FrozenModel):
    kind: str = Field(min_length=1)
    control_count: int = Field(ge=0)
    treatment_count: int = Field(ge=0)
    delta: int

    @model_validator(mode="after")
    def delta_must_match_counts(self) -> "EventCountDelta":
        if self.delta != self.treatment_count - self.control_count:
            raise ValueError("event count delta must equal treatment_count - control_count")
        return self


class PairTraceDiff(FrozenModel):
    pair_block_id: UUID
    control_run_id: UUID
    treatment_run_id: UUID
    control_attempt_id: UUID
    treatment_attempt_id: UUID
    sequence_edit_distance: int = Field(ge=0)
    event_count_deltas: Tuple[EventCountDelta, ...]

    @model_validator(mode="after")
    def event_kinds_must_be_unique_and_sorted(self) -> "PairTraceDiff":
        kinds = tuple(item.kind for item in self.event_count_deltas)
        if tuple(sorted(set(kinds))) != kinds:
            raise ValueError("event count delta kinds must be unique and sorted")
        return self
