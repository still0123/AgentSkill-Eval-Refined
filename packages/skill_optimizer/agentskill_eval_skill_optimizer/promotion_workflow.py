"""Stage 4b integration from a frozen evolution handoff to Fake-only publication."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, Optional, Tuple, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from agentskill_eval_contracts import (
    CandidateOrigin,
    FinalEvaluationStage,
    HumanReviewRecord,
    OptimizationJobStatus,
    PromotionEvidenceRef,
    PromotionLineageArtifact,
    PromotionReleaseManifest,
    PromotionStatus,
    PromotionWorkflowRecord,
    PromotionWorkflowStatus,
    stable_sha256,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter, fsync_directory
from agentskill_eval_experiment.storage.locks import LocalRunLock
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes
from agentskill_eval_skill_optimizer.evolution import (
    EvolutionHandoff,
    EvolutionReport,
    RegressionGateResult,
)
from agentskill_eval_skill_optimizer.final_evaluation import (
    FinalEvaluationResult,
    IndependentFinalEvaluator,
)
from agentskill_eval_skill_optimizer.final_spec import IndependentFinalEvaluationSpec
from agentskill_eval_skill_optimizer.promotion import (
    SkillVersionPromotionCore,
    SkillVersionPublication,
)
from agentskill_eval_skill_optimizer.search import OptimizationStore


class PromotionWorkflowError(RuntimeError):
    """Raised when a handoff, evidence step, or review violates Stage 4b."""


@dataclass(frozen=True)
class PromotionWorkflowResult:
    workflow: PromotionWorkflowRecord
    release_manifest: Optional[PromotionReleaseManifest] = None
    publication: Optional[SkillVersionPublication] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class PromotionWorkflowStore:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "promotion-workflows"
        self.writer = AtomicFileWriter()

    def workflow_dir(self, workflow_id: UUID) -> Path:
        return self.root / str(workflow_id)

    def workflow_path(self, workflow_id: UUID) -> Path:
        return self.workflow_dir(workflow_id) / "workflow.json"

    def lock_path(self, workflow_id: UUID) -> Path:
        return self.workflow_dir(workflow_id) / ".workflow.lock"

    def release_path(self, workflow_id: UUID) -> Path:
        return self.workflow_dir(workflow_id) / "release-manifest.json"

    def save(self, workflow: PromotionWorkflowRecord) -> None:
        self.writer.write(self.workflow_path(workflow.id), model_bytes(workflow))

    def load(self, workflow_id: UUID) -> PromotionWorkflowRecord:
        path = self.workflow_path(workflow_id)
        if not path.is_file():
            raise PromotionWorkflowError(f"promotion workflow does not exist: {workflow_id}")
        return load_model(path.read_bytes(), PromotionWorkflowRecord)

    def load_release(self, workflow_id: UUID) -> PromotionReleaseManifest:
        path = self.release_path(workflow_id)
        if not path.is_file():
            raise PromotionWorkflowError(f"release manifest does not exist: {workflow_id}")
        content = path.read_bytes()
        sidecar = path.with_suffix(".sha256")
        if not sidecar.is_file() or sidecar.read_text(encoding="utf-8").strip() != _sha256(content):
            raise PromotionWorkflowError("release manifest integrity mismatch")
        return load_model(content, PromotionReleaseManifest)

    def write_release_once(self, release: PromotionReleaseManifest) -> str:
        path = self.release_path(release.workflow_id)
        content = model_bytes(release)
        digest = _sha256(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            if path.read_bytes() != content:
                raise PromotionWorkflowError("immutable release manifest already exists") from exc
            sidecar = path.with_suffix(".sha256")
            if not sidecar.is_file():
                self.writer.write(sidecar, (digest + "\n").encode())
            existing = self.load_release(release.workflow_id)
            if existing != release:
                raise PromotionWorkflowError("immutable release manifest already exists") from exc
            return digest
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        self.writer.write(path.with_suffix(".sha256"), (digest + "\n").encode())
        fsync_directory(path.parent)
        return digest


class PromotionWorkflow:
    """Integrate a simulated Evolution handoff with existing final evaluation and promotion."""

    CLAIM_LIMIT = (
        "Stage 4b Fake/fixture integration evidence only. This is not a real Skill v2 release "
        "and does not establish Agent performance."
    )

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.store = PromotionWorkflowStore(self.workspace)
        self.optimization_store = OptimizationStore(self.workspace)
        self.promotion_core = SkillVersionPromotionCore(self.workspace)
        self.final_evaluator = IndependentFinalEvaluator(self.workspace)

    def begin(
        self,
        handoff_path: Path,
        *,
        skill_name: str,
        target_version: str,
        actor: str,
    ) -> PromotionWorkflowRecord:
        handoff_source = handoff_path.resolve(strict=True)
        handoff = EvolutionHandoff.model_validate_json(handoff_source.read_bytes())
        if handoff.status != "AWAITING_INDEPENDENT_FINAL_EVALUATION":
            raise PromotionWorkflowError("handoff is not awaiting independent final evaluation")
        if handoff.locked_test_accessed or handoff.auto_publish:
            raise PromotionWorkflowError("handoff must not access locked test or auto-publish")
        evolution_path = handoff_source.parent / "evolution-report.json"
        regression_path = handoff_source.parent / "regression-gate.json"
        try:
            evolution = EvolutionReport.model_validate_json(evolution_path.read_bytes())
            regression = RegressionGateResult.model_validate_json(regression_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise PromotionWorkflowError(f"invalid evolution lineage: {exc}") from exc
        self._validate_handoff(handoff, evolution, regression, handoff_source)
        if not evolution.simulated:
            raise PromotionWorkflowError("Stage 4b accepts Fake/fixture evolution evidence only")

        job = self.optimization_store.load_job(handoff.optimization_job_id)
        if job.status != OptimizationJobStatus.FROZEN:
            raise PromotionWorkflowError("handoff optimization job is not frozen")
        candidates = self.optimization_store.list_candidates(job)
        base = next((item for item in candidates if item.origin == CandidateOrigin.ORIGINAL), None)
        winner = next((item for item in candidates if item.id == handoff.winner_candidate_id), None)
        if base is None or winner is None or job.frozen_winner_id != handoff.winner_candidate_id:
            raise PromotionWorkflowError("handoff base or frozen winner is missing")
        self.optimization_store.assert_skill_integrity(base)
        self.optimization_store.assert_skill_integrity(winner)
        if base.content_sha256 != handoff.base_skill_sha256:
            raise PromotionWorkflowError("handoff parent Skill hash mismatch")
        if winner.content_sha256 != handoff.winner_skill_sha256:
            raise PromotionWorkflowError("handoff winner Skill hash mismatch")
        winner_path = self.optimization_store.skill_path(winner).resolve(strict=True)
        if Path(handoff.winner_skill_path).resolve(strict=True) != winner_path:
            raise PromotionWorkflowError("handoff winner path does not match frozen store")

        lineage = self._lineage(evolution, handoff_source, evolution_path, regression_path)
        lineage_sha = stable_sha256([item.model_dump(mode="json") for item in lineage])
        workflow_identity = stable_sha256(
            {
                "handoff_sha256": lineage[0].sha256,
                "skill_name": skill_name,
                "target_version": target_version,
                "lineage_sha256": lineage_sha,
            }
        )
        workflow_id = uuid5(NAMESPACE_URL, f"ase-promotion-workflow:{workflow_identity}")
        if self.store.workflow_path(workflow_id).exists():
            return self.store.load(workflow_id)
        promotion = self.promotion_core.create(
            skill_name=skill_name,
            target_version=target_version,
            optimization_job_id=job.id,
            winner_candidate_id=winner.id,
            base_skill_path=self.optimization_store.skill_path(base),
            winner_skill_path=winner_path,
            actor=actor,
            metadata={
                "promotion_workflow_id": str(workflow_id),
                "lineage_sha256": lineage_sha,
                "evidence_class": "fake-fixture-only",
            },
        )
        now = _utcnow()
        workflow = PromotionWorkflowRecord(
            id=workflow_id,
            promotion_id=promotion.id,
            evolution_id=handoff.evolution_id,
            optimization_job_id=job.id,
            winner_candidate_id=winner.id,
            skill_name=skill_name,
            target_version=target_version,
            base_skill_sha256=base.content_sha256,
            winner_skill_sha256=winner.content_sha256,
            lineage_sha256=lineage_sha,
            lineage=lineage,
            status=PromotionWorkflowStatus.AWAITING_CONFIRMATION,
            created_at=now,
            updated_at=now,
            claim_limit=self.CLAIM_LIMIT,
        )
        self.store.save(workflow)
        return workflow

    def confirm(
        self, workflow_id: UUID, spec: IndependentFinalEvaluationSpec
    ) -> PromotionWorkflowResult:
        return self._evaluate(workflow_id, spec, FinalEvaluationStage.VALIDATION_CONFIRM)

    def locked_test(
        self, workflow_id: UUID, spec: IndependentFinalEvaluationSpec
    ) -> PromotionWorkflowResult:
        return self._evaluate(workflow_id, spec, FinalEvaluationStage.LOCKED_TEST)

    def approve(self, workflow_id: UUID, *, reviewer: str, reason: str) -> PromotionWorkflowResult:
        with LocalRunLock(self.store.lock_path(workflow_id)):
            workflow = self.store.load(workflow_id)
            recovered = self._recover_written_release(workflow, actor=reviewer)
            if recovered is not None:
                return recovered
            if workflow.status == PromotionWorkflowStatus.APPROVED:
                return PromotionWorkflowResult(
                    workflow,
                    self.store.load_release(workflow.id),
                    self.promotion_core.publish(workflow.promotion_id, actor=reviewer),
                )
            if workflow.status != PromotionWorkflowStatus.AWAITING_HUMAN_REVIEW:
                raise PromotionWorkflowError("approval requires completed locked-test evidence")
            review = HumanReviewRecord(
                decision="APPROVED", reviewer=reviewer, reason=reason, reviewed_at=_utcnow()
            )
            promotion = self.promotion_core.store.load_promotion(workflow.promotion_id)
            if promotion.status == PromotionStatus.LOCKED_TEST_COMPLETED:
                promotion = self.promotion_core.approve(
                    workflow.promotion_id, actor=reviewer, reason=reason
                )
            if promotion.status not in {PromotionStatus.APPROVED, PromotionStatus.PUBLISHED}:
                raise PromotionWorkflowError("promotion core is not eligible for publication")
            publication = self.promotion_core.publish(workflow.promotion_id, actor=reviewer)
            manifest_sha = _sha256(publication.manifest_path.read_bytes())
            release = self._release(
                workflow,
                decision="APPROVED",
                review=review,
                publication=publication,
            )
            release_sha = self.store.write_release_once(release)
            updated = workflow.model_copy(
                update={
                    "status": PromotionWorkflowStatus.APPROVED,
                    "human_review": review,
                    "skill_version_manifest_sha256": manifest_sha,
                    "diff_sha256": publication.manifest.diff_sha256,
                    "release_manifest_sha256": release_sha,
                    "updated_at": _utcnow(),
                }
            )
            validated = PromotionWorkflowRecord.model_validate(updated.model_dump(mode="python"))
            self.store.save(validated)
            return PromotionWorkflowResult(validated, release, publication)

    def reject(self, workflow_id: UUID, *, reviewer: str, reason: str) -> PromotionWorkflowResult:
        with LocalRunLock(self.store.lock_path(workflow_id)):
            workflow = self.store.load(workflow_id)
            recovered = self._recover_written_release(workflow, actor=reviewer)
            if recovered is not None:
                return recovered
            if workflow.status != PromotionWorkflowStatus.AWAITING_HUMAN_REVIEW:
                raise PromotionWorkflowError(
                    "human rejection requires completed locked-test evidence"
                )
            review = HumanReviewRecord(
                decision="REJECTED", reviewer=reviewer, reason=reason, reviewed_at=_utcnow()
            )
            self.promotion_core.reject(workflow.promotion_id, actor=reviewer, reason=reason)
            return self._finalize_rejection(workflow, review=review)

    def status(self, workflow_id: UUID) -> PromotionWorkflowResult:
        workflow = self.store.load(workflow_id)
        release = (
            self.store.load_release(workflow_id)
            if workflow.release_manifest_sha256 is not None
            else None
        )
        return PromotionWorkflowResult(workflow, release)

    def _evaluate(
        self,
        workflow_id: UUID,
        spec: IndependentFinalEvaluationSpec,
        stage: FinalEvaluationStage,
    ) -> PromotionWorkflowResult:
        with LocalRunLock(self.store.lock_path(workflow_id)):
            workflow = self.store.load(workflow_id)
            recovered = self._recover_written_release(workflow, actor="independent-final-evaluator")
            if recovered is not None:
                return recovered
            expected_status = (
                PromotionWorkflowStatus.AWAITING_CONFIRMATION
                if stage == FinalEvaluationStage.VALIDATION_CONFIRM
                else PromotionWorkflowStatus.AWAITING_LOCKED_TEST
            )
            if workflow.status != expected_status:
                raise PromotionWorkflowError(
                    f"{stage.value} requires workflow status {expected_status.value}"
                )
            if (
                spec.stage != stage.value
                or spec.optimization_job_id != workflow.optimization_job_id
            ):
                raise PromotionWorkflowError(
                    "final-evaluation spec does not match workflow stage/job"
                )
            if not spec.evaluator.simulated:
                raise PromotionWorkflowError("Stage 4b cannot execute real final evaluation")
            result = self.final_evaluator.run(spec)
            evidence = self._evidence(workflow, result, spec.evaluator.version)
            promotion = (
                self.promotion_core.record_validation_confirm(
                    workflow.promotion_id, evidence, actor="independent-final-evaluator"
                )
                if stage == FinalEvaluationStage.VALIDATION_CONFIRM
                else self.promotion_core.record_locked_test(
                    workflow.promotion_id, evidence, actor="locked-test-evaluator"
                )
            )
            evidence_field = (
                "confirmation"
                if stage == FinalEvaluationStage.VALIDATION_CONFIRM
                else "locked_test"
            )
            if promotion.status == PromotionStatus.REJECTED:
                interim = PromotionWorkflowRecord.model_validate(
                    workflow.model_copy(
                        update={evidence_field: evidence, "updated_at": _utcnow()}
                    ).model_dump(mode="python")
                )
                return self._finalize_rejection(interim)
            next_status = (
                PromotionWorkflowStatus.AWAITING_LOCKED_TEST
                if stage == FinalEvaluationStage.VALIDATION_CONFIRM
                else PromotionWorkflowStatus.AWAITING_HUMAN_REVIEW
            )
            updates: dict[str, object] = {
                "status": next_status,
                "updated_at": _utcnow(),
                evidence_field: evidence,
            }
            updated = PromotionWorkflowRecord.model_validate(
                workflow.model_copy(update=updates).model_dump(mode="python")
            )
            self.store.save(updated)
            return PromotionWorkflowResult(updated)

    def _finalize_rejection(
        self,
        workflow: PromotionWorkflowRecord,
        *,
        review: Optional[HumanReviewRecord] = None,
    ) -> PromotionWorkflowResult:
        release = self._release(workflow, decision="REJECTED", review=review)
        release_sha = self.store.write_release_once(release)
        updated = PromotionWorkflowRecord.model_validate(
            workflow.model_copy(
                update={
                    "status": PromotionWorkflowStatus.REJECTED,
                    "human_review": review,
                    "release_manifest_sha256": release_sha,
                    "updated_at": _utcnow(),
                }
            ).model_dump(mode="python")
        )
        self.store.save(updated)
        return PromotionWorkflowResult(updated, release)

    def _recover_written_release(
        self, workflow: PromotionWorkflowRecord, *, actor: str
    ) -> Optional[PromotionWorkflowResult]:
        """Finish a terminal state after a crash between release and workflow writes."""

        path = self.store.release_path(workflow.id)
        if not path.is_file():
            return None
        release = self.store.load_release(workflow.id)
        if (
            release.promotion_id != workflow.promotion_id
            or release.evolution_id != workflow.evolution_id
            or release.optimization_job_id != workflow.optimization_job_id
            or release.winner_candidate_id != workflow.winner_candidate_id
            or release.parent_skill_sha256 != workflow.base_skill_sha256
            or release.winner_skill_sha256 != workflow.winner_skill_sha256
            or release.lineage_sha256 != workflow.lineage_sha256
        ):
            raise PromotionWorkflowError("release manifest lineage does not match workflow")
        release_sha = _sha256(path.read_bytes())
        publication: Optional[SkillVersionPublication] = None
        updates: dict[str, object] = {
            "confirmation": release.confirmation,
            "locked_test": release.locked_test,
            "human_review": release.human_review,
            "release_manifest_sha256": release_sha,
            "updated_at": _utcnow(),
        }
        if release.decision == "APPROVED":
            publication = self.promotion_core.publish(workflow.promotion_id, actor=actor)
            manifest_sha = _sha256(publication.manifest_path.read_bytes())
            if (
                release.skill_version_manifest_sha256 != manifest_sha
                or release.diff_sha256 != publication.manifest.diff_sha256
            ):
                raise PromotionWorkflowError(
                    "release manifest does not match published SkillVersion"
                )
            updates.update(
                {
                    "status": PromotionWorkflowStatus.APPROVED,
                    "skill_version_manifest_sha256": manifest_sha,
                    "diff_sha256": publication.manifest.diff_sha256,
                }
            )
        else:
            updates["status"] = PromotionWorkflowStatus.REJECTED
        recovered = PromotionWorkflowRecord.model_validate(
            workflow.model_copy(update=updates).model_dump(mode="python")
        )
        self.store.save(recovered)
        return PromotionWorkflowResult(recovered, release, publication)

    def _release(
        self,
        workflow: PromotionWorkflowRecord,
        *,
        decision: Literal["APPROVED", "REJECTED"],
        review: Optional[HumanReviewRecord],
        publication: Optional[SkillVersionPublication] = None,
    ) -> PromotionReleaseManifest:
        if workflow.confirmation is None:
            raise PromotionWorkflowError("release requires confirmation evidence")
        terminal_evidence = workflow.locked_test or workflow.confirmation
        return PromotionReleaseManifest(
            workflow_id=workflow.id,
            promotion_id=workflow.promotion_id,
            decision=decision,
            evolution_id=workflow.evolution_id,
            optimization_job_id=workflow.optimization_job_id,
            winner_candidate_id=workflow.winner_candidate_id,
            parent_skill_sha256=workflow.base_skill_sha256,
            winner_skill_sha256=workflow.winner_skill_sha256,
            lineage_sha256=workflow.lineage_sha256,
            lineage=workflow.lineage,
            confirmation=workflow.confirmation,
            locked_test=workflow.locked_test,
            human_review=review,
            skill_version_manifest_sha256=(
                _sha256(publication.manifest_path.read_bytes()) if publication else None
            ),
            diff_sha256=publication.manifest.diff_sha256 if publication else None,
            released_at=(
                review.reviewed_at if review is not None else terminal_evidence.recorded_at
            ),
            claim_limit=self.CLAIM_LIMIT,
        )

    @staticmethod
    def _evidence(
        workflow: PromotionWorkflowRecord,
        result: FinalEvaluationResult,
        validator_version: str,
    ) -> PromotionEvidenceRef:
        job = result.job
        if not job.simulated:
            raise PromotionWorkflowError("Stage 4b final evidence must remain simulated")
        if job.base_skill_sha256 != workflow.base_skill_sha256:
            raise PromotionWorkflowError("final evidence parent Skill hash mismatch")
        if job.winner_skill_sha256 != workflow.winner_skill_sha256:
            raise PromotionWorkflowError("final evidence winner Skill hash mismatch")
        if job.completed_at is None:
            raise PromotionWorkflowError("final evidence job is incomplete")
        return PromotionEvidenceRef(
            stage=job.stage,
            final_evaluation_job_id=job.id,
            report_sha256=_sha256(result.report_json.read_bytes()),
            decision=result.report.decision,
            base_skill_sha256=job.base_skill_sha256,
            winner_skill_sha256=job.winner_skill_sha256,
            simulated=True,
            validator_version=validator_version,
            recorded_at=job.completed_at,
        )

    @staticmethod
    def _validate_handoff(
        handoff: EvolutionHandoff,
        evolution: EvolutionReport,
        regression: RegressionGateResult,
        handoff_path: Path,
    ) -> None:
        if evolution.evolution_id != handoff.evolution_id:
            raise PromotionWorkflowError("handoff evolution ID mismatch")
        if evolution.optimization_job_id != handoff.optimization_job_id:
            raise PromotionWorkflowError("handoff optimization job mismatch")
        if evolution.winner_candidate_id != handoff.winner_candidate_id:
            raise PromotionWorkflowError("handoff winner candidate mismatch")
        if evolution.winner_skill_sha256 != handoff.winner_skill_sha256:
            raise PromotionWorkflowError("handoff winner hash mismatches evolution report")
        if evolution.regression_gate != regression or not regression.passed:
            raise PromotionWorkflowError("handoff requires a passing frozen regression gate")
        artifact = evolution.artifacts.get("final_handoff")
        if artifact is None or Path(artifact).resolve(strict=True) != handoff_path:
            raise PromotionWorkflowError("evolution report does not reference this handoff")

    @staticmethod
    def _lineage(
        evolution: EvolutionReport,
        handoff: Path,
        evolution_report: Path,
        regression_gate: Path,
    ) -> Tuple[PromotionLineageArtifact, ...]:
        sources: Tuple[Tuple[str, Path], ...] = (
            ("handoff", handoff),
            ("evolution_report", evolution_report),
            ("regression_gate", regression_gate),
            ("hypotheses", PromotionWorkflow._artifact_path(evolution.artifacts, "hypotheses")),
            (
                "search_report",
                PromotionWorkflow._artifact_path(evolution.artifacts, "search_report"),
            ),
        )
        return tuple(
            PromotionLineageArtifact(
                role=cast(
                    Literal[
                        "handoff",
                        "evolution_report",
                        "regression_gate",
                        "hypotheses",
                        "search_report",
                    ],
                    role,
                ),
                sha256=_sha256(path.read_bytes()),
                size_bytes=path.stat().st_size,
            )
            for role, path in sources
        )

    @staticmethod
    def _artifact_path(artifacts: Mapping[str, str], role: str) -> Path:
        value = artifacts.get(role)
        if value is None:
            raise PromotionWorkflowError(f"evolution report is missing {role} lineage")
        try:
            path = Path(value).resolve(strict=True)
        except OSError as exc:
            raise PromotionWorkflowError(f"evolution lineage artifact is missing: {role}") from exc
        if not path.is_file():
            raise PromotionWorkflowError(f"evolution lineage artifact is not a file: {role}")
        return path
