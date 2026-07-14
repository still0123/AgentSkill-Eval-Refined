"""Persisted contracts for audited SkillVersion promotion and publication."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

from pydantic import Field, model_validator

from agentskill_eval_contracts.base import FrozenModel, HexDigest
from agentskill_eval_contracts.final_evaluation import FinalDecision, FinalEvaluationStage


class PromotionStatus(str, Enum):
    CREATED = "CREATED"
    VALIDATION_CONFIRMED = "VALIDATION_CONFIRMED"
    LOCKED_TEST_COMPLETED = "LOCKED_TEST_COMPLETED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


ALLOWED_PROMOTION_TRANSITIONS = {
    None: {PromotionStatus.CREATED},
    PromotionStatus.CREATED: {
        PromotionStatus.VALIDATION_CONFIRMED,
        PromotionStatus.REJECTED,
    },
    PromotionStatus.VALIDATION_CONFIRMED: {
        PromotionStatus.LOCKED_TEST_COMPLETED,
        PromotionStatus.REJECTED,
    },
    PromotionStatus.LOCKED_TEST_COMPLETED: {
        PromotionStatus.APPROVED,
        PromotionStatus.REJECTED,
    },
    PromotionStatus.APPROVED: {
        PromotionStatus.PUBLISHED,
        PromotionStatus.REJECTED,
    },
    PromotionStatus.PUBLISHED: set(),
    PromotionStatus.REJECTED: set(),
}


class PromotionEvidenceRef(FrozenModel):
    """Content-addressed reference to independently produced final evidence."""

    stage: FinalEvaluationStage
    final_evaluation_job_id: UUID
    report_sha256: HexDigest
    decision: FinalDecision
    base_skill_sha256: HexDigest
    winner_skill_sha256: HexDigest
    simulated: bool
    validator_version: str = Field(min_length=1)
    recorded_at: datetime


class PromotionTransition(FrozenModel):
    sequence: int = Field(ge=1)
    from_status: Optional[PromotionStatus]
    to_status: PromotionStatus
    occurred_at: datetime
    actor: str = Field(min_length=1)
    input_sha256: HexDigest
    output_sha256: HexDigest
    reason: Optional[str] = None


class SkillVersionPromotion(FrozenModel):
    schema_version: Literal["ase/skill-version-promotion/v1alpha1"] = (
        "ase/skill-version-promotion/v1alpha1"
    )
    id: UUID
    skill_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    target_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
    optimization_job_id: UUID
    winner_candidate_id: UUID
    base_skill_sha256: HexDigest
    winner_skill_sha256: HexDigest
    status: PromotionStatus
    evidence: Tuple[PromotionEvidenceRef, ...] = ()
    transitions: Tuple[PromotionTransition, ...] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    rejection_reason: Optional[str] = None
    published_skill_version_id: Optional[UUID] = None
    metadata: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def lifecycle_is_valid(self) -> "SkillVersionPromotion":
        if self.base_skill_sha256 == self.winner_skill_sha256:
            raise ValueError("promotion winner must differ from the base Skill")
        previous: Optional[PromotionStatus] = None
        for sequence, transition in enumerate(self.transitions, start=1):
            if transition.sequence != sequence or transition.from_status != previous:
                raise ValueError("promotion transition history is not contiguous")
            if transition.to_status not in ALLOWED_PROMOTION_TRANSITIONS[previous]:
                raise ValueError(
                    f"illegal promotion transition {previous}->{transition.to_status}"
                )
            previous = transition.to_status
        if previous != self.status:
            raise ValueError("last transition must equal promotion status")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

        stages = tuple(item.stage for item in self.evidence)
        if len(stages) != len(set(stages)):
            raise ValueError("promotion evidence stages must be unique")
        expected_prefix = (
            FinalEvaluationStage.VALIDATION_CONFIRM,
            FinalEvaluationStage.LOCKED_TEST,
        )[: len(stages)]
        if stages != expected_prefix:
            raise ValueError("promotion evidence must follow validation_confirm then locked_test")
        for item in self.evidence:
            if item.base_skill_sha256 != self.base_skill_sha256:
                raise ValueError("evidence base Skill hash does not match promotion")
            if item.winner_skill_sha256 != self.winner_skill_sha256:
                raise ValueError("evidence winner Skill hash does not match promotion")

        requires_validation = self.status in {
            PromotionStatus.VALIDATION_CONFIRMED,
            PromotionStatus.LOCKED_TEST_COMPLETED,
            PromotionStatus.APPROVED,
            PromotionStatus.PUBLISHED,
        }
        requires_locked = self.status in {
            PromotionStatus.LOCKED_TEST_COMPLETED,
            PromotionStatus.APPROVED,
            PromotionStatus.PUBLISHED,
        }
        if requires_validation and len(self.evidence) < 1:
            raise ValueError("promotion status requires validation_confirm evidence")
        if requires_locked and len(self.evidence) < 2:
            raise ValueError("promotion status requires locked_test evidence")
        if self.status in {PromotionStatus.APPROVED, PromotionStatus.PUBLISHED} and any(
            item.decision != FinalDecision.CONFIRMED for item in self.evidence
        ):
            raise ValueError("approved promotion requires confirmed evidence")
        if self.status == PromotionStatus.REJECTED:
            if not self.rejection_reason:
                raise ValueError("rejected promotion requires a reason")
            if self.published_skill_version_id is not None:
                raise ValueError("rejected promotion cannot reference a published version")
        elif self.rejection_reason is not None:
            raise ValueError("non-rejected promotion cannot carry a rejection reason")
        if self.status == PromotionStatus.PUBLISHED:
            if self.published_skill_version_id is None:
                raise ValueError("published promotion requires a SkillVersion ID")
        elif self.published_skill_version_id is not None:
            raise ValueError("unpublished promotion cannot reference a SkillVersion ID")
        return self


class SkillVersionManifest(FrozenModel):
    """Immutable publication manifest for a promoted Skill version."""

    schema_version: Literal["ase/skill-version/v1alpha1"] = "ase/skill-version/v1alpha1"
    id: UUID
    skill_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
    promotion_id: UUID
    optimization_job_id: UUID
    winner_candidate_id: UUID
    parent_content_sha256: HexDigest
    content_sha256: HexDigest
    content_bytes: int = Field(ge=1)
    diff_sha256: HexDigest
    validation_confirm: PromotionEvidenceRef
    locked_test: PromotionEvidenceRef
    simulated_evidence: bool
    created_at: datetime
    published_at: datetime
    claim_limit: str = Field(min_length=1)
    metadata: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def publication_is_consistent(self) -> "SkillVersionManifest":
        if self.parent_content_sha256 == self.content_sha256:
            raise ValueError("published Skill must differ from its parent")
        if self.validation_confirm.stage != FinalEvaluationStage.VALIDATION_CONFIRM:
            raise ValueError("validation_confirm evidence has the wrong stage")
        if self.locked_test.stage != FinalEvaluationStage.LOCKED_TEST:
            raise ValueError("locked_test evidence has the wrong stage")
        for item in (self.validation_confirm, self.locked_test):
            if item.decision != FinalDecision.CONFIRMED:
                raise ValueError("published Skill requires confirmed evidence")
            if item.base_skill_sha256 != self.parent_content_sha256:
                raise ValueError("published evidence base hash does not match parent")
            if item.winner_skill_sha256 != self.content_sha256:
                raise ValueError("published evidence winner hash does not match content")
        if self.simulated_evidence != (
            self.validation_confirm.simulated or self.locked_test.simulated
        ):
            raise ValueError("simulated_evidence must reflect either simulated evidence input")
        if self.published_at < self.created_at:
            raise ValueError("published_at cannot precede created_at")
        return self
