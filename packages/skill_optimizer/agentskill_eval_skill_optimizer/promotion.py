"""Audited SkillVersion promotion core using frozen, fake-capable evidence refs."""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from agentskill_eval_contracts import (
    ALLOWED_PROMOTION_TRANSITIONS,
    FinalDecision,
    FinalEvaluationStage,
    PromotionEvidenceRef,
    PromotionStatus,
    PromotionTransition,
    SkillVersionManifest,
    SkillVersionPromotion,
    stable_sha256,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter, fsync_directory
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes


class PromotionError(RuntimeError):
    """Raised when promotion evidence, state, integrity, or publication is invalid."""


@dataclass(frozen=True)
class SkillVersionPublication:
    promotion: SkillVersionPromotion
    manifest: SkillVersionManifest
    version_dir: Path
    manifest_path: Path
    skill_path: Path
    diff_path: Path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class SkillVersionPromotionStore:
    """Crash-safe local persistence for mutable promotion state and immutable versions."""

    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "skill-version-promotion"
        self.writer = AtomicFileWriter()

    def promotion_dir(self, promotion_id: UUID) -> Path:
        return self.root / "promotions" / str(promotion_id)

    def version_dir(self, skill_name: str, version: str) -> Path:
        return self.root / "versions" / skill_name / version

    def save_promotion(self, promotion: SkillVersionPromotion) -> None:
        self.writer.write(
            self.promotion_dir(promotion.id) / "promotion.json",
            model_bytes(promotion),
        )

    def load_promotion(self, promotion_id: UUID) -> SkillVersionPromotion:
        target = self.promotion_dir(promotion_id) / "promotion.json"
        if not target.is_file():
            raise PromotionError(f"promotion does not exist: {promotion_id}")
        return load_model(target.read_bytes(), SkillVersionPromotion)

    def load_manifest(self, skill_name: str, version: str) -> SkillVersionManifest:
        target = self.version_dir(skill_name, version) / "manifest.json"
        if not target.is_file():
            raise PromotionError(f"SkillVersion does not exist: {skill_name}@{version}")
        content = target.read_bytes()
        digest_path = target.with_suffix(".sha256")
        if not digest_path.is_file() or digest_path.read_text(encoding="utf-8").strip() != _sha256(
            content
        ):
            raise PromotionError("SkillVersion manifest integrity mismatch")
        return load_model(content, SkillVersionManifest)

    def freeze_inputs(self, promotion: SkillVersionPromotion, base: bytes, winner: bytes) -> None:
        directory = self.promotion_dir(promotion.id) / "inputs"
        self._write_once(directory / "base-SKILL.md", base)
        self._write_once(directory / "winner-SKILL.md", winner)

    @staticmethod
    def _write_once(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            if target.read_bytes() == content:
                return
            raise PromotionError(
                f"immutable input already exists with other content: {target}"
            ) from exc
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(target.parent)


class SkillVersionPromotionCore:
    """Advance a frozen winner through confirmation, locked test, and publication."""

    def __init__(self, workspace: Path) -> None:
        self.store = SkillVersionPromotionStore(workspace)

    def create(
        self,
        *,
        skill_name: str,
        target_version: str,
        optimization_job_id: UUID,
        winner_candidate_id: UUID,
        base_skill_path: Path,
        winner_skill_path: Path,
        actor: str,
        metadata: Optional[Mapping[str, str]] = None,
    ) -> SkillVersionPromotion:
        base = base_skill_path.resolve(strict=True).read_bytes()
        winner = winner_skill_path.resolve(strict=True).read_bytes()
        base_sha = _sha256(base)
        winner_sha = _sha256(winner)
        identity = stable_sha256(
            {
                "skill_name": skill_name,
                "target_version": target_version,
                "optimization_job_id": str(optimization_job_id),
                "winner_candidate_id": str(winner_candidate_id),
                "base_skill_sha256": base_sha,
                "winner_skill_sha256": winner_sha,
            }
        )
        promotion_id = uuid5(NAMESPACE_URL, f"ase-skill-promotion:{identity}")
        now = _utcnow()
        transition = PromotionTransition(
            sequence=1,
            from_status=None,
            to_status=PromotionStatus.CREATED,
            occurred_at=now,
            actor=actor,
            input_sha256=identity,
            output_sha256=stable_sha256({"status": PromotionStatus.CREATED.value}),
            reason="frozen winner accepted for promotion review",
        )
        promotion = SkillVersionPromotion(
            id=promotion_id,
            skill_name=skill_name,
            target_version=target_version,
            optimization_job_id=optimization_job_id,
            winner_candidate_id=winner_candidate_id,
            base_skill_sha256=base_sha,
            winner_skill_sha256=winner_sha,
            status=PromotionStatus.CREATED,
            transitions=(transition,),
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        target = self.store.promotion_dir(promotion.id) / "promotion.json"
        if target.exists():
            existing = self.store.load_promotion(promotion.id)
            self._assert_frozen_inputs(existing)
            return existing
        self.store.freeze_inputs(promotion, base, winner)
        self.store.save_promotion(promotion)
        return promotion

    def record_validation_confirm(
        self, promotion_id: UUID, evidence: PromotionEvidenceRef, *, actor: str
    ) -> SkillVersionPromotion:
        promotion = self.store.load_promotion(promotion_id)
        self._require_status(promotion, PromotionStatus.CREATED)
        self._validate_evidence(promotion, evidence, FinalEvaluationStage.VALIDATION_CONFIRM)
        if evidence.decision != FinalDecision.CONFIRMED:
            return self._reject(
                promotion,
                actor=actor,
                reason=f"validation_confirm decision was {evidence.decision.value}",
                evidence=evidence,
            )
        return self._advance(
            promotion,
            PromotionStatus.VALIDATION_CONFIRMED,
            actor=actor,
            reason="validation_confirm evidence accepted",
            evidence=evidence,
        )

    def record_locked_test(
        self, promotion_id: UUID, evidence: PromotionEvidenceRef, *, actor: str
    ) -> SkillVersionPromotion:
        promotion = self.store.load_promotion(promotion_id)
        self._require_status(promotion, PromotionStatus.VALIDATION_CONFIRMED)
        self._validate_evidence(promotion, evidence, FinalEvaluationStage.LOCKED_TEST)
        if evidence.decision != FinalDecision.CONFIRMED:
            return self._reject(
                promotion,
                actor=actor,
                reason=f"locked_test decision was {evidence.decision.value}",
                evidence=evidence,
            )
        return self._advance(
            promotion,
            PromotionStatus.LOCKED_TEST_COMPLETED,
            actor=actor,
            reason="locked_test evidence accepted",
            evidence=evidence,
        )

    def approve(self, promotion_id: UUID, *, actor: str, reason: str) -> SkillVersionPromotion:
        promotion = self.store.load_promotion(promotion_id)
        self._require_status(promotion, PromotionStatus.LOCKED_TEST_COMPLETED)
        if len(promotion.evidence) != 2 or any(
            item.decision != FinalDecision.CONFIRMED for item in promotion.evidence
        ):
            raise PromotionError("approval requires confirmed validation and locked-test evidence")
        return self._advance(
            promotion,
            PromotionStatus.APPROVED,
            actor=actor,
            reason=reason,
        )

    def reject(self, promotion_id: UUID, *, actor: str, reason: str) -> SkillVersionPromotion:
        promotion = self.store.load_promotion(promotion_id)
        return self._reject(promotion, actor=actor, reason=reason)

    def publish(self, promotion_id: UUID, *, actor: str) -> SkillVersionPublication:
        promotion = self.store.load_promotion(promotion_id)
        if promotion.status == PromotionStatus.PUBLISHED:
            return self._load_publication(promotion)
        self._require_status(promotion, PromotionStatus.APPROVED)
        try:
            publication = self._publish_immutable(promotion)
        except Exception as exc:
            self._reject(
                promotion,
                actor=actor,
                reason=f"publication failed: {type(exc).__name__}: {exc}",
            )
            if isinstance(exc, PromotionError):
                raise
            raise PromotionError(f"SkillVersion publication failed: {exc}") from exc
        published = self._advance(
            promotion,
            PromotionStatus.PUBLISHED,
            actor=actor,
            reason="immutable SkillVersion published",
            published_skill_version_id=publication.manifest.id,
        )
        return SkillVersionPublication(
            promotion=published,
            manifest=publication.manifest,
            version_dir=publication.version_dir,
            manifest_path=publication.manifest_path,
            skill_path=publication.skill_path,
            diff_path=publication.diff_path,
        )

    def _publish_immutable(
        self, promotion: SkillVersionPromotion
    ) -> SkillVersionPublication:
        base, winner = self._frozen_input_bytes(promotion)
        diff = "".join(
            difflib.unified_diff(
                base.decode("utf-8").splitlines(keepends=True),
                winner.decode("utf-8").splitlines(keepends=True),
                fromfile=f"{promotion.skill_name}@base/SKILL.md",
                tofile=f"{promotion.skill_name}@{promotion.target_version}/SKILL.md",
            )
        ).encode("utf-8")
        if not diff:
            raise PromotionError("v1/v2 diff is empty")
        validation, locked = promotion.evidence
        now = _utcnow()
        version_id = uuid5(
            NAMESPACE_URL,
            f"ase-skill-version:{promotion.skill_name}:{promotion.target_version}:"
            f"{promotion.winner_skill_sha256}",
        )
        manifest = SkillVersionManifest(
            id=version_id,
            skill_name=promotion.skill_name,
            version=promotion.target_version,
            promotion_id=promotion.id,
            optimization_job_id=promotion.optimization_job_id,
            winner_candidate_id=promotion.winner_candidate_id,
            parent_content_sha256=promotion.base_skill_sha256,
            content_sha256=promotion.winner_skill_sha256,
            content_bytes=len(winner),
            diff_sha256=_sha256(diff),
            validation_confirm=validation,
            locked_test=locked,
            simulated_evidence=validation.simulated or locked.simulated,
            created_at=promotion.created_at,
            published_at=now,
            claim_limit=(
                "Fake/simulated evidence validates the promotion controller only; it is not "
                "Agent performance evidence."
                if validation.simulated or locked.simulated
                else "Evidence applies only to the frozen datasets, Agent, and evaluation protocol."
            ),
            metadata=promotion.metadata,
        )
        version_dir = self.store.version_dir(promotion.skill_name, promotion.target_version)
        if version_dir.exists():
            existing = self.store.load_manifest(
                promotion.skill_name, promotion.target_version
            )
            if existing.promotion_id != promotion.id or existing.content_sha256 != (
                promotion.winner_skill_sha256
            ):
                raise PromotionError(
                    f"immutable SkillVersion already exists: "
                    f"{promotion.skill_name}@{promotion.target_version}"
                )
            return self._publication_from_manifest(promotion, existing)
        parent = version_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".tmp-{uuid4()}-{promotion.target_version}"
        try:
            staging.mkdir(mode=0o700)
            manifest_bytes = model_bytes(manifest)
            self.store.writer.write(staging / "SKILL.md", winner)
            self.store.writer.write(staging / "v1-v2.diff", diff)
            self.store.writer.write(staging / "manifest.json", manifest_bytes)
            self.store.writer.write(
                staging / "manifest.sha256", (_sha256(manifest_bytes) + "\n").encode()
            )
            os.rename(staging, version_dir)
            fsync_directory(parent)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return SkillVersionPublication(
            promotion=promotion,
            manifest=manifest,
            version_dir=version_dir,
            manifest_path=version_dir / "manifest.json",
            skill_path=version_dir / "SKILL.md",
            diff_path=version_dir / "v1-v2.diff",
        )

    def _publication_from_manifest(
        self,
        promotion: SkillVersionPromotion,
        manifest: SkillVersionManifest,
    ) -> SkillVersionPublication:
        version_dir = self.store.version_dir(promotion.skill_name, promotion.target_version)
        skill_path = version_dir / "SKILL.md"
        diff_path = version_dir / "v1-v2.diff"
        if not skill_path.is_file() or _sha256(skill_path.read_bytes()) != manifest.content_sha256:
            raise PromotionError("published Skill content integrity mismatch")
        if not diff_path.is_file() or _sha256(diff_path.read_bytes()) != manifest.diff_sha256:
            raise PromotionError("published Skill diff integrity mismatch")
        return SkillVersionPublication(
            promotion=promotion,
            manifest=manifest,
            version_dir=version_dir,
            manifest_path=version_dir / "manifest.json",
            skill_path=skill_path,
            diff_path=diff_path,
        )

    def _load_publication(
        self, promotion: SkillVersionPromotion
    ) -> SkillVersionPublication:
        manifest = self.store.load_manifest(promotion.skill_name, promotion.target_version)
        if manifest.id != promotion.published_skill_version_id:
            raise PromotionError("promotion references a different SkillVersion manifest")
        return self._publication_from_manifest(promotion, manifest)

    def _assert_frozen_inputs(self, promotion: SkillVersionPromotion) -> None:
        self._frozen_input_bytes(promotion)

    def _frozen_input_bytes(self, promotion: SkillVersionPromotion) -> tuple[bytes, bytes]:
        directory = self.store.promotion_dir(promotion.id) / "inputs"
        base = (directory / "base-SKILL.md").read_bytes()
        winner = (directory / "winner-SKILL.md").read_bytes()
        if _sha256(base) != promotion.base_skill_sha256:
            raise PromotionError("frozen base Skill integrity mismatch")
        if _sha256(winner) != promotion.winner_skill_sha256:
            raise PromotionError("frozen winner Skill integrity mismatch")
        return base, winner

    @staticmethod
    def _require_status(
        promotion: SkillVersionPromotion, expected: PromotionStatus
    ) -> None:
        if promotion.status != expected:
            raise PromotionError(
                f"promotion requires {expected.value}, found {promotion.status.value}"
            )

    @staticmethod
    def _validate_evidence(
        promotion: SkillVersionPromotion,
        evidence: PromotionEvidenceRef,
        expected_stage: FinalEvaluationStage,
    ) -> None:
        if evidence.stage != expected_stage:
            raise PromotionError(f"expected {expected_stage.value} evidence")
        if evidence.base_skill_sha256 != promotion.base_skill_sha256:
            raise PromotionError("evidence base Skill hash mismatch")
        if evidence.winner_skill_sha256 != promotion.winner_skill_sha256:
            raise PromotionError("evidence winner Skill hash mismatch")

    def _reject(
        self,
        promotion: SkillVersionPromotion,
        *,
        actor: str,
        reason: str,
        evidence: Optional[PromotionEvidenceRef] = None,
    ) -> SkillVersionPromotion:
        if promotion.status in {PromotionStatus.PUBLISHED, PromotionStatus.REJECTED}:
            raise PromotionError(f"cannot reject terminal promotion {promotion.status.value}")
        return self._advance(
            promotion,
            PromotionStatus.REJECTED,
            actor=actor,
            reason=reason,
            evidence=evidence,
            rejection_reason=reason,
        )

    def _advance(
        self,
        promotion: SkillVersionPromotion,
        status: PromotionStatus,
        *,
        actor: str,
        reason: str,
        evidence: Optional[PromotionEvidenceRef] = None,
        rejection_reason: Optional[str] = None,
        published_skill_version_id: Optional[UUID] = None,
    ) -> SkillVersionPromotion:
        if status not in ALLOWED_PROMOTION_TRANSITIONS[promotion.status]:
            raise PromotionError(
                f"illegal promotion transition {promotion.status.value}->{status.value}"
            )
        now = _utcnow()
        new_evidence = promotion.evidence + ((evidence,) if evidence is not None else ())
        transition = PromotionTransition(
            sequence=len(promotion.transitions) + 1,
            from_status=promotion.status,
            to_status=status,
            occurred_at=now,
            actor=actor,
            input_sha256=stable_sha256(promotion.model_dump(mode="json")),
            output_sha256=stable_sha256(
                {
                    "status": status.value,
                    "evidence": [item.report_sha256 for item in new_evidence],
                    "reason": reason,
                    "published_skill_version_id": (
                        str(published_skill_version_id)
                        if published_skill_version_id is not None
                        else None
                    ),
                }
            ),
            reason=reason,
        )
        updated = promotion.model_copy(
            update={
                "status": status,
                "evidence": new_evidence,
                "transitions": promotion.transitions + (transition,),
                "updated_at": now,
                "rejection_reason": rejection_reason,
                "published_skill_version_id": published_skill_version_id,
            }
        )
        validated = SkillVersionPromotion.model_validate(updated.model_dump(mode="python"))
        self.store.save_promotion(validated)
        return validated
