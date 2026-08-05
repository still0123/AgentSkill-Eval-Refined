"""Contracts for auditable Skill promotion integration workflows."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest
from agentskill_eval_contracts.promotion import PromotionEvidenceRef


class PromotionWorkflowStatus(str, Enum):
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_LOCKED_TEST = "AWAITING_LOCKED_TEST"
    AWAITING_HUMAN_REVIEW = "AWAITING_HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PromotionLineageArtifact(FrozenModel):
    role: Literal[
        "handoff",
        "evolution_report",
        "regression_gate",
        "hypotheses",
        "search_report",
    ]
    sha256: HexDigest
    size_bytes: int = Field(ge=1)


class HumanReviewRecord(FrozenModel):
    decision: Literal["APPROVED", "REJECTED"]
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime


class PromotionWorkflowRecord(FrozenModel):
    id: UUID
    promotion_id: UUID
    evolution_id: UUID
    optimization_job_id: UUID
    winner_candidate_id: UUID
    skill_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    target_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
    base_skill_sha256: HexDigest
    winner_skill_sha256: HexDigest
    lineage_sha256: HexDigest
    lineage: Tuple[PromotionLineageArtifact, ...] = Field(min_length=5, max_length=5)
    status: PromotionWorkflowStatus
    confirmation: Optional[PromotionEvidenceRef] = None
    locked_test: Optional[PromotionEvidenceRef] = None
    human_review: Optional[HumanReviewRecord] = None
    skill_version_manifest_sha256: Optional[HexDigest] = None
    diff_sha256: Optional[HexDigest] = None
    release_manifest_sha256: Optional[HexDigest] = None
    simulated: bool = True
    created_at: datetime
    updated_at: datetime
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> "PromotionWorkflowRecord":
        roles = tuple(item.role for item in self.lineage)
        expected_roles = (
            "handoff",
            "evolution_report",
            "regression_gate",
            "hypotheses",
            "search_report",
        )
        if roles != expected_roles:
            raise ValueError("promotion lineage roles must be complete and ordered")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        needs_confirmation = self.status != PromotionWorkflowStatus.AWAITING_CONFIRMATION
        needs_locked = self.status in {
            PromotionWorkflowStatus.AWAITING_HUMAN_REVIEW,
            PromotionWorkflowStatus.APPROVED,
        }
        if needs_confirmation and self.confirmation is None:
            raise ValueError("workflow status requires confirmation evidence")
        if needs_locked and self.locked_test is None:
            raise ValueError("workflow status requires locked-test evidence")
        for evidence in (self.confirmation, self.locked_test):
            if evidence is not None and evidence.simulated != self.simulated:
                raise ValueError("workflow evidence boundary is inconsistent")
        if self.status in {
            PromotionWorkflowStatus.APPROVED,
            PromotionWorkflowStatus.REJECTED,
        }:
            if self.human_review is None and self.status == PromotionWorkflowStatus.APPROVED:
                raise ValueError("approved workflow requires human review")
            if self.release_manifest_sha256 is None:
                raise ValueError("terminal workflow requires a release manifest hash")
        if self.status == PromotionWorkflowStatus.APPROVED:
            if self.human_review is None or self.human_review.decision != "APPROVED":
                raise ValueError("approved workflow requires an approval review")
            if self.skill_version_manifest_sha256 is None or self.diff_sha256 is None:
                raise ValueError("approved workflow requires SkillVersion and diff hashes")
        elif self.skill_version_manifest_sha256 is not None or self.diff_sha256 is not None:
            raise ValueError("non-approved workflow cannot reference a published SkillVersion")
        if self.human_review is not None:
            expected = self.status.value
            if self.human_review.decision != expected:
                raise ValueError("human review decision must match terminal workflow status")
        return self


class PromotionReleaseManifest(FrozenModel):
    workflow_id: UUID
    promotion_id: UUID
    decision: Literal["APPROVED", "REJECTED"]
    evolution_id: UUID
    optimization_job_id: UUID
    winner_candidate_id: UUID
    parent_skill_sha256: HexDigest
    winner_skill_sha256: HexDigest
    lineage_sha256: HexDigest
    lineage: Tuple[PromotionLineageArtifact, ...] = Field(min_length=5, max_length=5)
    confirmation: PromotionEvidenceRef
    locked_test: Optional[PromotionEvidenceRef] = None
    human_review: Optional[HumanReviewRecord] = None
    skill_version_manifest_sha256: Optional[HexDigest] = None
    diff_sha256: Optional[HexDigest] = None
    simulated: bool = True
    released_at: datetime
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def decision_is_consistent(self) -> "PromotionReleaseManifest":
        if self.decision == "APPROVED":
            if self.locked_test is None or self.human_review is None:
                raise ValueError("approved release requires locked test and human review")
            if self.human_review.decision != "APPROVED":
                raise ValueError("approved release requires approval review")
            if self.skill_version_manifest_sha256 is None or self.diff_sha256 is None:
                raise ValueError("approved release requires published artifact hashes")
        else:
            if self.skill_version_manifest_sha256 is not None or self.diff_sha256 is not None:
                raise ValueError("rejected release cannot reference published artifacts")
            if self.human_review is not None and self.human_review.decision != "REJECTED":
                raise ValueError("rejected release review must be REJECTED")
        return self
