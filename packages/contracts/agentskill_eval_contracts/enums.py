"""Enumerations shared across AgentSkill-Eval manifests."""

from __future__ import annotations

from enum import Enum


class ExperimentStatus(str, Enum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class VariantRole(str, Enum):
    BASELINE = "baseline"
    TREATMENT = "treatment"
    PLACEBO = "placebo"
    CANDIDATE = "candidate"
    CONTROL = "control"


class ExecutionStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    GRADING = "GRADING"
    PERSISTING = "PERSISTING"
    RETRY_WAIT = "RETRY_WAIT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    COMPLETED = "COMPLETED"
    INFRA_FAILED = "INFRA_FAILED"
    CANCELLED = "CANCELLED"


class EvaluationOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INVALID = "invalid"


class AttemptStatus(str, Enum):
    CLAIMED = "CLAIMED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FENCED = "FENCED"
    CANCELLED = "CANCELLED"


class ArtifactSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"
