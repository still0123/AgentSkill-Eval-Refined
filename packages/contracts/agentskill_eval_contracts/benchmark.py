"""Audit contracts for automatic benchmark reconstruction and publication."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest, stable_sha256


class BenchmarkJobStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BenchmarkCandidateStatus(str, Enum):
    INGESTED = "INGESTED"
    RECONSTRUCTED = "RECONSTRUCTED"
    VERIFIED = "VERIFIED"
    DEDUPED = "DEDUPED"
    REVIEWED = "REVIEWED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


ALLOWED_CANDIDATE_TRANSITIONS = {
    None: {BenchmarkCandidateStatus.INGESTED},
    BenchmarkCandidateStatus.INGESTED: {
        BenchmarkCandidateStatus.RECONSTRUCTED,
        BenchmarkCandidateStatus.REJECTED,
    },
    BenchmarkCandidateStatus.RECONSTRUCTED: {
        BenchmarkCandidateStatus.VERIFIED,
        BenchmarkCandidateStatus.REJECTED,
    },
    BenchmarkCandidateStatus.VERIFIED: {
        BenchmarkCandidateStatus.DEDUPED,
        BenchmarkCandidateStatus.REJECTED,
    },
    BenchmarkCandidateStatus.DEDUPED: {
        BenchmarkCandidateStatus.REVIEWED,
        BenchmarkCandidateStatus.REJECTED,
    },
    BenchmarkCandidateStatus.REVIEWED: {
        BenchmarkCandidateStatus.PUBLISHED,
        BenchmarkCandidateStatus.REJECTED,
    },
    BenchmarkCandidateStatus.PUBLISHED: set(),
    BenchmarkCandidateStatus.REJECTED: set(),
}


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CommandEvidence(FrozenModel):
    variant: Literal["before", "after", "mutation", "alternative"]
    repeat_index: int = Field(ge=1)
    argv: Tuple[str, ...] = Field(min_length=1)
    exit_code: Optional[int] = None
    timed_out: bool = False
    duration_ms: int = Field(ge=0)
    stdout_sha256: HexDigest
    stderr_sha256: HexDigest


class QualityGateResult(FrozenModel):
    name: str = Field(min_length=1, max_length=120)
    passed: bool
    detail: str = Field(min_length=1, max_length=2000)
    evidence_sha256: Optional[HexDigest] = None


class CandidateProvenance(FrozenModel):
    source_type: Literal["git_history"] = "git_history"
    repository_url: str = Field(min_length=1)
    fork_lineage: str = Field(min_length=1)
    provenance_family: Optional[str] = Field(default=None, min_length=1)
    license_spdx: str = Field(min_length=1)
    license_sha256: HexDigest
    before_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    after_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    after_committed_at: datetime
    issue_url: Optional[str] = None
    reference_patch_sha256: HexDigest
    generator_profile: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    verifier_profile: str = Field(min_length=1)
    verifier_version: str = Field(min_length=1)
    contamination_risk: Literal["low", "medium", "high", "unknown"]
    synthetic: bool = False


class CandidateTransition(FrozenModel):
    sequence: int = Field(ge=1)
    from_status: Optional[BenchmarkCandidateStatus]
    to_status: BenchmarkCandidateStatus
    occurred_at: datetime
    actor: str = Field(min_length=1)
    input_sha256: HexDigest
    output_sha256: HexDigest
    reason: Optional[str] = None


class BenchmarkCandidate(FrozenModel):
    schema_version: Literal["ase/benchmark-candidate/v1alpha1"] = "ase/benchmark-candidate/v1alpha1"
    id: UUID
    job_id: UUID
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    task: str = Field(min_length=1)
    category: Literal[
        "positive", "negative", "distractor", "complex", "robustness"
    ] = "positive"
    tags: Tuple[str, ...] = ()
    target_split: str = Field(min_length=1)
    status: BenchmarkCandidateStatus
    fixture_sha256: Optional[HexDigest] = None
    oracle_sha256: Optional[HexDigest] = None
    grader_sha256: Optional[HexDigest] = None
    artifact_sha256: Dict[str, HexDigest] = Field(default_factory=dict)
    provenance: Optional[CandidateProvenance] = None
    command_evidence: Tuple[CommandEvidence, ...] = ()
    quality_gates: Tuple[QualityGateResult, ...] = ()
    duplicate_of: Optional[UUID] = None
    review_decision: Optional[ReviewDecision] = None
    reviewer: Optional[str] = None
    rejection_reasons: Tuple[str, ...] = ()
    transitions: Tuple[CandidateTransition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "BenchmarkCandidate":
        if self.transitions[-1].to_status != self.status:
            raise ValueError("last transition must equal candidate status")
        if tuple(item.sequence for item in self.transitions) != tuple(
            range(1, len(self.transitions) + 1)
        ):
            raise ValueError("transition sequence must be contiguous")
        previous: Optional[BenchmarkCandidateStatus] = None
        for transition in self.transitions:
            if transition.from_status != previous:
                raise ValueError("transition history is not contiguous")
            if transition.to_status not in ALLOWED_CANDIDATE_TRANSITIONS[previous]:
                raise ValueError(
                    f"illegal candidate transition: {previous} -> {transition.to_status}"
                )
            previous = transition.to_status
        if self.status == BenchmarkCandidateStatus.PUBLISHED:
            if self.review_decision != ReviewDecision.APPROVED:
                raise ValueError("published candidate requires approval")
            if not self.quality_gates or not all(gate.passed for gate in self.quality_gates):
                raise ValueError("published candidate requires all quality gates")
        if self.status == BenchmarkCandidateStatus.REJECTED and not self.rejection_reasons:
            raise ValueError("rejected candidate requires rejection reasons")
        return self


class BenchmarkJob(FrozenModel):
    schema_version: Literal["ase/benchmark-job/v1alpha1"] = "ase/benchmark-job/v1alpha1"
    id: UUID
    status: BenchmarkJobStatus
    source_spec_sha256: HexDigest
    generator_profile: str
    verifier_profile: str
    target_split: str
    max_candidates: int = Field(ge=1)
    max_commands: int = Field(ge=1)
    wall_seconds: int = Field(ge=1)
    candidate_ids: Tuple[UUID, ...]
    created_at: datetime
    completed_at: Optional[datetime] = None


class PublishedCase(FrozenModel):
    candidate_id: UUID
    case_id: str
    case_sha256: HexDigest
    fixture_sha256: HexDigest
    grader_sha256: HexDigest
    provenance_sha256: HexDigest
    metadata_sha256: Optional[HexDigest] = None


class BenchmarkDatasetVersion(FrozenModel):
    schema_version: Literal["ase/benchmark-dataset-version/v1alpha1"] = (
        "ase/benchmark-dataset-version/v1alpha1"
    )
    id: UUID
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    split: str = Field(min_length=1)
    job_id: UUID
    published_at: datetime
    publisher: str = Field(min_length=1)
    cases: Tuple[PublishedCase, ...] = Field(min_length=1)
    content_sha256: HexDigest
    source_lineages: Tuple[str, ...] = Field(min_length=1)
    metadata: Dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def calculate_content_sha256(cases: Tuple[PublishedCase, ...]) -> str:
        return stable_sha256(
            [item.model_dump(mode="json", exclude_none=True) for item in cases]
        )

    @model_validator(mode="after")
    def content_hash_matches(self) -> "BenchmarkDatasetVersion":
        actual = self.calculate_content_sha256(self.cases)
        if actual != self.content_sha256:
            raise ValueError("dataset version content_sha256 mismatch")
        return self
