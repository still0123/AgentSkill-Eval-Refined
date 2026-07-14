from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agentskill_eval_contracts import (
    FinalDecision,
    FinalEvaluationStage,
    PromotionEvidenceRef,
    PromotionStatus,
)
from agentskill_eval_skill_optimizer import PromotionError, SkillVersionPromotionCore


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _skills(tmp_path: Path, suffix: str = "") -> tuple[Path, Path]:
    base = tmp_path / f"base{suffix}.md"
    winner = tmp_path / f"winner{suffix}.md"
    base.write_text("# Review Skill\n\nInspect the change.\n", encoding="utf-8")
    winner.write_text(
        "# Review Skill\n\nInspect the change.\n\nRun focused regression tests.\n"
        f"{suffix}\n",
        encoding="utf-8",
    )
    return base, winner


def _evidence(
    stage: FinalEvaluationStage,
    base: Path,
    winner: Path,
    *,
    decision: FinalDecision = FinalDecision.CONFIRMED,
    base_sha: str | None = None,
) -> PromotionEvidenceRef:
    return PromotionEvidenceRef(
        stage=stage,
        final_evaluation_job_id=uuid4(),
        report_sha256=_sha(f"fake-{stage.value}".encode()),
        decision=decision,
        base_skill_sha256=base_sha or _sha(base.read_bytes()),
        winner_skill_sha256=_sha(winner.read_bytes()),
        simulated=True,
        validator_version="fake-final-evaluator-v1",
        recorded_at=datetime.now(timezone.utc),
    )


def _create(
    core: SkillVersionPromotionCore,
    base: Path,
    winner: Path,
    *,
    target_version: str = "2.0.0-test",
    optimization_job_id: UUID | None = None,
) -> UUID:
    promotion = core.create(
        skill_name="python-review",
        target_version=target_version,
        optimization_job_id=optimization_job_id or uuid4(),
        winner_candidate_id=uuid4(),
        base_skill_path=base,
        winner_skill_path=winner,
        actor="fake-search",
        metadata={"evidence_class": "fake-only"},
    )
    return promotion.id


def _confirm_and_approve(
    core: SkillVersionPromotionCore,
    promotion_id: UUID,
    base: Path,
    winner: Path,
) -> None:
    core.record_validation_confirm(
        promotion_id,
        _evidence(FinalEvaluationStage.VALIDATION_CONFIRM, base, winner),
        actor="fake-validator",
    )
    core.record_locked_test(
        promotion_id,
        _evidence(FinalEvaluationStage.LOCKED_TEST, base, winner),
        actor="fake-locked-test",
    )
    core.approve(promotion_id, actor="human-reviewer", reason="fake controller test")


def test_fake_winner_promotion_publishes_immutable_manifest_and_diff(tmp_path: Path) -> None:
    core = SkillVersionPromotionCore(tmp_path / "workspace")
    base, winner = _skills(tmp_path)
    promotion_id = _create(core, base, winner)

    _confirm_and_approve(core, promotion_id, base, winner)
    published = core.publish(promotion_id, actor="publisher")
    replayed = core.publish(promotion_id, actor="publisher")

    assert published.promotion.status == PromotionStatus.PUBLISHED
    assert published.manifest.simulated_evidence is True
    assert "not Agent performance evidence" in published.manifest.claim_limit
    assert published.skill_path.read_bytes() == winner.read_bytes()
    assert _sha(published.diff_path.read_bytes()) == published.manifest.diff_sha256
    assert "Run focused regression tests" in published.diff_path.read_text(encoding="utf-8")
    assert replayed.manifest == published.manifest
    assert core.store.load_manifest("python-review", "2.0.0-test") == published.manifest

    published.manifest_path.write_bytes(b"tampered")
    with pytest.raises(PromotionError, match="manifest integrity mismatch"):
        core.publish(promotion_id, actor="publisher")


@pytest.mark.parametrize(
    ("stage", "decision", "expected"),
    [
        (
            FinalEvaluationStage.VALIDATION_CONFIRM,
            FinalDecision.NOT_CONFIRMED,
            "validation_confirm decision was NOT_CONFIRMED",
        ),
        (
            FinalEvaluationStage.LOCKED_TEST,
            FinalDecision.REGRESSION,
            "locked_test decision was REGRESSION",
        ),
    ],
)
def test_non_confirmed_evidence_rejects_promotion(
    tmp_path: Path,
    stage: FinalEvaluationStage,
    decision: FinalDecision,
    expected: str,
) -> None:
    core = SkillVersionPromotionCore(tmp_path / stage.value)
    base, winner = _skills(tmp_path, stage.value)
    promotion_id = _create(core, base, winner)
    if stage == FinalEvaluationStage.LOCKED_TEST:
        core.record_validation_confirm(
            promotion_id,
            _evidence(FinalEvaluationStage.VALIDATION_CONFIRM, base, winner),
            actor="fake-validator",
        )

    rejected = (
        core.record_validation_confirm(
            promotion_id,
            _evidence(stage, base, winner, decision=decision),
            actor="fake-validator",
        )
        if stage == FinalEvaluationStage.VALIDATION_CONFIRM
        else core.record_locked_test(
            promotion_id,
            _evidence(stage, base, winner, decision=decision),
            actor="fake-validator",
        )
    )

    assert rejected.status == PromotionStatus.REJECTED
    assert rejected.rejection_reason == expected


def test_hash_mismatch_and_evidence_order_are_rejected(tmp_path: Path) -> None:
    core = SkillVersionPromotionCore(tmp_path / "workspace")
    base, winner = _skills(tmp_path)
    promotion_id = _create(core, base, winner)

    with pytest.raises(PromotionError, match="expected validation_confirm"):
        core.record_validation_confirm(
            promotion_id,
            _evidence(FinalEvaluationStage.LOCKED_TEST, base, winner),
            actor="fake-validator",
        )
    with pytest.raises(PromotionError, match="base Skill hash mismatch"):
        core.record_validation_confirm(
            promotion_id,
            _evidence(
                FinalEvaluationStage.VALIDATION_CONFIRM,
                base,
                winner,
                base_sha="0" * 64,
            ),
            actor="fake-validator",
        )


def test_publication_collision_is_rejected_without_overwrite(tmp_path: Path) -> None:
    core = SkillVersionPromotionCore(tmp_path / "workspace")
    base, winner_one = _skills(tmp_path, "one")
    _, winner_two = _skills(tmp_path, "two")
    first_id = _create(core, base, winner_one)
    _confirm_and_approve(core, first_id, base, winner_one)
    first = core.publish(first_id, actor="publisher")
    original_skill = first.skill_path.read_bytes()

    second_id = _create(core, base, winner_two)
    _confirm_and_approve(core, second_id, base, winner_two)
    with pytest.raises(PromotionError, match="already exists"):
        core.publish(second_id, actor="publisher")

    rejected = core.store.load_promotion(second_id)
    assert rejected.status == PromotionStatus.REJECTED
    assert rejected.rejection_reason is not None
    assert rejected.rejection_reason.startswith("publication failed")
    assert first.skill_path.read_bytes() == original_skill
