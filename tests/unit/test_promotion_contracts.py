from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentskill_eval_contracts import (
    FinalDecision,
    FinalEvaluationStage,
    PromotionEvidenceRef,
    PromotionStatus,
    PromotionTransition,
    SkillVersionPromotion,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evidence(stage: FinalEvaluationStage, base: str, winner: str) -> PromotionEvidenceRef:
    return PromotionEvidenceRef(
        stage=stage,
        final_evaluation_job_id=uuid4(),
        report_sha256="3" * 64,
        decision=FinalDecision.CONFIRMED,
        base_skill_sha256=base,
        winner_skill_sha256=winner,
        simulated=True,
        validator_version="fake-v1",
        recorded_at=_now(),
    )


def _transition(
    sequence: int,
    before: PromotionStatus | None,
    after: PromotionStatus,
) -> PromotionTransition:
    return PromotionTransition(
        sequence=sequence,
        from_status=before,
        to_status=after,
        occurred_at=_now(),
        actor="test",
        input_sha256="4" * 64,
        output_sha256="5" * 64,
    )


def test_promotion_contract_rejects_out_of_order_evidence() -> None:
    base = "1" * 64
    winner = "2" * 64

    with pytest.raises(ValidationError, match="validation_confirm then locked_test"):
        SkillVersionPromotion(
            id=uuid4(),
            skill_name="review-skill",
            target_version="2.0.0-test",
            optimization_job_id=uuid4(),
            winner_candidate_id=uuid4(),
            base_skill_sha256=base,
            winner_skill_sha256=winner,
            status=PromotionStatus.VALIDATION_CONFIRMED,
            evidence=(_evidence(FinalEvaluationStage.LOCKED_TEST, base, winner),),
            transitions=(
                _transition(1, None, PromotionStatus.CREATED),
                _transition(
                    2,
                    PromotionStatus.CREATED,
                    PromotionStatus.VALIDATION_CONFIRMED,
                ),
            ),
            created_at=_now(),
            updated_at=_now(),
        )


def test_promotion_contract_rejects_duplicate_evidence() -> None:
    base = "1" * 64
    winner = "2" * 64
    validation = _evidence(FinalEvaluationStage.VALIDATION_CONFIRM, base, winner)

    with pytest.raises(ValidationError, match="stages must be unique"):
        SkillVersionPromotion(
            id=uuid4(),
            skill_name="review-skill",
            target_version="2.0.0-test",
            optimization_job_id=uuid4(),
            winner_candidate_id=uuid4(),
            base_skill_sha256=base,
            winner_skill_sha256=winner,
            status=PromotionStatus.VALIDATION_CONFIRMED,
            evidence=(validation, validation),
            transitions=(
                _transition(1, None, PromotionStatus.CREATED),
                _transition(
                    2,
                    PromotionStatus.CREATED,
                    PromotionStatus.VALIDATION_CONFIRMED,
                ),
            ),
            created_at=_now(),
            updated_at=_now(),
        )


def test_promotion_contract_is_frozen() -> None:
    now = _now()
    promotion = SkillVersionPromotion(
        id=uuid4(),
        skill_name="review-skill",
        target_version="2.0.0-test",
        optimization_job_id=uuid4(),
        winner_candidate_id=uuid4(),
        base_skill_sha256="1" * 64,
        winner_skill_sha256="2" * 64,
        status=PromotionStatus.CREATED,
        transitions=(_transition(1, None, PromotionStatus.CREATED),),
        created_at=now,
        updated_at=now,
    )

    with pytest.raises(ValidationError, match="frozen"):
        promotion.status = PromotionStatus.REJECTED
