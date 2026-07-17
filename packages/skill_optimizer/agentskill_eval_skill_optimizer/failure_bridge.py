"""Export observed Skill-run failures into the existing evolution input contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple
from uuid import UUID

import yaml
from pydantic import Field, model_validator

from agentskill_eval_contracts import (
    AttributionRole,
    DiagnosticFinding,
    EvaluationOutcome,
    ExperimentVariant,
    FailureDiagnosis,
    FailureLabel,
    FrozenModel,
    RealEvidenceClass,
    RealEvidenceRunManifest,
    RealEvidenceStatus,
    TraceEvent,
    TraceManifest,
    VariantRole,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment import LocalExperimentStore
from agentskill_eval_experiment.storage import AtomicFileWriter, ExperimentLayout, load_model
from agentskill_eval_skill_optimizer.evolution import (
    FailureEvidenceBundle,
    sanitize_observed_summary,
)


class FailureBridgeError(RuntimeError):
    """Raised when observed experiment evidence cannot be exported safely."""


class FailureReviewDecision(FrozenModel):
    run_id: UUID
    rule_id: str = Field(min_length=1)
    action: Literal["include", "exclude"]
    reason: str = Field(min_length=1)
    override_label: Optional[FailureLabel] = None

    @model_validator(mode="after")
    def label_override_requires_inclusion(self) -> "FailureReviewDecision":
        if self.override_label is not None and self.action != "include":
            raise ValueError("override_label is only valid for include decisions")
        return self


class FailureReviewFile(FrozenModel):
    schema_version: Literal["ase/failure-evidence-review/v1alpha1"]
    decisions: Tuple[FailureReviewDecision, ...] = ()

    @model_validator(mode="after")
    def decision_keys_must_be_unique(self) -> "FailureReviewFile":
        keys = [(item.run_id, item.rule_id) for item in self.decisions]
        if len(keys) != len(set(keys)):
            raise ValueError("review decisions must have unique run_id/rule_id keys")
        return self

    @classmethod
    def load(cls, path: Path) -> "FailureReviewFile":
        try:
            return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise FailureBridgeError(f"invalid failure review file {path}: {exc}") from exc


class ObservedFindingDecision(FrozenModel):
    run_id: UUID
    attempt_id: UUID
    label: FailureLabel
    rule_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_sequence_nos: Tuple[int, ...]
    trace_event_refs: Tuple[str, ...]
    eligible: bool
    reason: str = Field(min_length=1)
    observed_summary: str = Field(default="", max_length=1200, exclude=True)
    review_applied: bool = False


class FailureEvidenceCluster(FrozenModel):
    label: FailureLabel
    rule_id: str = Field(min_length=1)
    finding_count: int = Field(ge=1)
    evidence_refs: Tuple[str, ...] = Field(min_length=1)


class FailureBridgeReport(FrozenModel):
    schema_version: Literal["ase/observed-failure-bridge-report/v1alpha1"] = (
        "ase/observed-failure-bridge-report/v1alpha1"
    )
    status: Literal["READY", "INSUFFICIENT"]
    experiment_id: UUID
    experiment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    real_run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_variant_id: UUID
    skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    treatment_run_count: int = Field(ge=0)
    task_failed_run_count: int = Field(ge=0)
    invalid_run_count: int = Field(ge=0)
    unfinished_run_count: int = Field(ge=0)
    eligible: Tuple[ObservedFindingDecision, ...]
    excluded: Tuple[ObservedFindingDecision, ...]
    clusters: Tuple[FailureEvidenceCluster, ...]
    bundle_path: Optional[str] = None
    bundle_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    insufficiency_reason: Optional[str] = None

    @model_validator(mode="after")
    def status_matches_bundle(self) -> "FailureBridgeReport":
        if self.status == "READY":
            if not self.eligible or not self.bundle_path or not self.bundle_sha256:
                raise ValueError("READY reports require eligible evidence and a bundle")
            if self.insufficiency_reason is not None:
                raise ValueError("READY reports cannot contain insufficiency_reason")
        elif self.bundle_path is not None or self.bundle_sha256 is not None:
            raise ValueError("INSUFFICIENT reports cannot claim a generated bundle")
        elif not self.insufficiency_reason:
            raise ValueError("INSUFFICIENT reports require a reason")
        return self


@dataclass(frozen=True)
class FailureBridgeResult:
    report: FailureBridgeReport
    report_path: Path
    bundle: Optional[FailureEvidenceBundle]
    bundle_path: Optional[Path]


class ObservedFailureEvidenceBridge:
    """Build train-only optimizer evidence from an observed Skill treatment arm."""

    EXCLUDED_LABELS = {
        FailureLabel.ENVIRONMENT,
        FailureLabel.BUDGET,
        FailureLabel.JUDGE,
        FailureLabel.UNKNOWN,
    }

    _EVENT_RULES: Tuple[Tuple[Tuple[str, ...], FailureLabel, str, str], ...] = (
        (
            ("skill.conflict",),
            FailureLabel.SKILL_CONFLICT,
            "rule.observed_skill_conflict",
            "The trace recorded a failed Skill-conflict event.",
        ),
        (
            ("tool.selection",),
            FailureLabel.TOOL_SELECTION,
            "rule.observed_tool_selection_failure",
            "The trace recorded a failed tool-selection event.",
        ),
        (
            ("tool.argument", "tool.arguments"),
            FailureLabel.TOOL_ARGUMENT,
            "rule.observed_tool_argument_failure",
            "The trace recorded a failed tool-argument event.",
        ),
        (
            ("tool.recovery",),
            FailureLabel.TOOL_RECOVERY,
            "rule.observed_tool_recovery_failure",
            "The trace recorded a failed tool-recovery event.",
        ),
        (
            ("verification", "test."),
            FailureLabel.VERIFICATION,
            "rule.observed_verification_failure",
            "The trace recorded a failed verification or test event.",
        ),
        (
            ("retrieval", "rag."),
            FailureLabel.RETRIEVAL,
            "rule.observed_retrieval_failure",
            "The trace recorded a failed retrieval event.",
        ),
        (
            ("memory.",),
            FailureLabel.MEMORY,
            "rule.observed_memory_failure",
            "The trace recorded a failed memory event.",
        ),
        (
            ("agent.plan", "planning"),
            FailureLabel.PLANNING,
            "rule.observed_planning_failure",
            "The trace recorded a failed planning event.",
        ),
    )

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.store = LocalExperimentStore(self.workspace)
        self.writer = AtomicFileWriter()

    def prepare(
        self,
        experiment_id: UUID,
        output: Path,
        *,
        review_path: Optional[Path] = None,
    ) -> FailureBridgeResult:
        experiment = self.store.load_experiment(experiment_id)
        real_run = self._load_observed_run(experiment_id)
        variant = self._skill_variant(experiment_id)
        skill_snapshot = variant.skill_snapshot
        if skill_snapshot is None:
            raise FailureBridgeError("selected Skill variant has no Skill snapshot")
        review = FailureReviewFile.load(review_path) if review_path is not None else None
        review_by_key = (
            {(item.run_id, item.rule_id): item for item in review.decisions} if review else {}
        )

        eligible: list[ObservedFindingDecision] = []
        excluded: list[ObservedFindingDecision] = []
        diagnoses: list[FailureDiagnosis] = []
        task_failed_runs = 0
        invalid_runs = 0
        unfinished_runs = 0
        treatment_runs = [
            run for run in self.store.list_runs(experiment_id) if run.variant_id == variant.id
        ]
        for run in treatment_runs:
            if run.evaluation_outcome is None:
                unfinished_runs += 1
                continue
            if run.evaluation_outcome == EvaluationOutcome.INVALID:
                invalid_runs += 1
            attempt = self.store.load_selected_attempt(experiment_id, run)
            if attempt is None:
                unfinished_runs += 1
                continue
            trace = self.store.load_trace_manifest(experiment_id, run.id, attempt.attempt_no)
            diagnosis = self.store.load_failure_diagnosis(
                experiment_id, run.id, attempt.attempt_no
            )
            if run.evaluation_outcome != EvaluationOutcome.FAIL:
                for finding in diagnosis.findings:
                    excluded.append(
                        self._decision(
                            experiment_id,
                            diagnosis,
                            finding,
                            eligible=False,
                            reason="only task-failed Skill runs may enter optimization",
                        )
                    )
                continue

            task_failed_runs += 1
            diagnosis = self._supplement_abstained(diagnosis, trace)
            accepted_findings: list[DiagnosticFinding] = []
            for finding in diagnosis.findings:
                review_decision = review_by_key.get((run.id, finding.rule_id))
                candidate = self._apply_review(finding, review_decision)
                review_promoted = (
                    review_decision is not None
                    and review_decision.action == "include"
                    and review_decision.override_label is not None
                )
                is_eligible = (
                    (diagnosis.status == "diagnosed" or review_promoted)
                    and candidate.label not in self.EXCLUDED_LABELS
                    and (review_decision is None or review_decision.action == "include")
                )
                reason = self._eligibility_reason(diagnosis, candidate, review_decision)
                decision = self._decision(
                    experiment_id,
                    diagnosis,
                    candidate,
                    eligible=is_eligible,
                    reason=reason,
                    review_applied=review_decision is not None,
                )
                (eligible if is_eligible else excluded).append(decision)
                if is_eligible:
                    accepted_findings.append(candidate)
            if accepted_findings:
                diagnoses.append(
                    FailureDiagnosis(
                        run_id=diagnosis.run_id,
                        attempt_id=diagnosis.attempt_id,
                        status="diagnosed",
                        findings=tuple(accepted_findings),
                    )
                )

        clusters = self._clusters(eligible)
        output = output.expanduser().resolve()
        bundle: Optional[FailureEvidenceBundle] = None
        bundle_path: Optional[Path] = None
        bundle_sha: Optional[str] = None
        if diagnoses:
            bundle = FailureEvidenceBundle(
                schema_version="ase/failure-evidence-bundle/v1alpha1",
                name=f"{experiment.name} observed train failures",
                split="train",
                diagnoses=tuple(diagnoses),
                agent_provider=real_run.provider,
                agent_model=real_run.model,
            )
            bundle_bytes = yaml.safe_dump(
                bundle.model_dump(mode="json"), allow_unicode=True, sort_keys=False
            ).encode("utf-8")
            self.writer.write(output, bundle_bytes)
            bundle_path = output
            bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()

        report_path = output.with_suffix(output.suffix + ".audit.json")
        status: Literal["READY", "INSUFFICIENT"] = "READY" if diagnoses else "INSUFFICIENT"
        report = FailureBridgeReport(
            status=status,
            experiment_id=experiment_id,
            experiment_sha256=stable_sha256(experiment.model_dump(mode="json")),
            real_run_sha256=stable_sha256(real_run.model_dump(mode="json")),
            skill_variant_id=variant.id,
            skill_sha256=skill_snapshot.content_sha256,
            treatment_run_count=len(treatment_runs),
            task_failed_run_count=task_failed_runs,
            invalid_run_count=invalid_runs,
            unfinished_run_count=unfinished_runs,
            eligible=tuple(sorted(eligible, key=self._decision_key)),
            excluded=tuple(sorted(excluded, key=self._decision_key)),
            clusters=clusters,
            bundle_path=str(bundle_path) if bundle_path else None,
            bundle_sha256=bundle_sha,
            insufficiency_reason=(
                None
                if diagnoses
                else "no eligible task-failure findings were observed in the Skill treatment arm"
            ),
        )
        self.writer.write(report_path, canonical_json(report.model_dump(mode="json")) + b"\n")
        return FailureBridgeResult(report, report_path, bundle, bundle_path)

    def _load_observed_run(self, experiment_id: UUID) -> RealEvidenceRunManifest:
        path = ExperimentLayout(self.workspace, experiment_id).root / "real-evidence-run.json"
        try:
            manifest = load_model(path.read_bytes(), RealEvidenceRunManifest)
        except (OSError, ValueError) as exc:
            raise FailureBridgeError(
                f"missing or invalid observed-Agent run manifest: {exc}"
            ) from exc
        if (
            manifest.simulated
            or manifest.evidence_class != RealEvidenceClass.OBSERVED_AGENT
            or not manifest.real_run_confirmed
        ):
            raise FailureBridgeError("failure preparation requires observed-Agent evidence")
        if manifest.status != RealEvidenceStatus.COMPLETED:
            raise FailureBridgeError("observed-Agent experiment must be completed")
        return manifest

    def _skill_variant(self, experiment_id: UUID) -> ExperimentVariant:
        variants = [
            item
            for item in self.store.list_variants(experiment_id)
            if item.skill_snapshot is not None
            and item.role in {VariantRole.TREATMENT, VariantRole.CANDIDATE}
        ]
        if len(variants) != 1:
            raise FailureBridgeError(
                "experiment must contain exactly one treatment/candidate Skill variant"
            )
        return variants[0]

    @classmethod
    def _supplement_abstained(
        cls, diagnosis: FailureDiagnosis, trace: TraceManifest
    ) -> FailureDiagnosis:
        if diagnosis.status != "abstained":
            return diagnosis
        failed_events = tuple(event for event in trace.events if cls._event_failed(event))
        for prefixes, label, rule_id, rationale in cls._EVENT_RULES:
            matching = tuple(
                event for event in failed_events if any(token in event.kind for token in prefixes)
            )
            if matching:
                return FailureDiagnosis(
                    run_id=diagnosis.run_id,
                    attempt_id=diagnosis.attempt_id,
                    status="diagnosed",
                    findings=(
                        DiagnosticFinding(
                            label=label,
                            role=AttributionRole.ROOT_CAUSE,
                            confidence=0.8,
                            rule_id=rule_id,
                            evidence_sequence_nos=tuple(event.sequence_no for event in matching),
                            rationale=rationale,
                        ),
                    ),
                )
        return diagnosis

    @staticmethod
    def _event_failed(event: TraceEvent) -> bool:
        if event.status == "failed":
            return True
        observed = event.summary.get("status") or event.summary.get("outcome")
        return isinstance(observed, str) and observed.lower() in {
            "error",
            "fail",
            "failed",
            "failure",
        }

    @classmethod
    def _apply_review(
        cls,
        finding: DiagnosticFinding,
        review: Optional[FailureReviewDecision],
    ) -> DiagnosticFinding:
        if review is None or review.override_label is None:
            return finding
        return finding.model_copy(
            update={
                "label": review.override_label,
                "rationale": f"Human-reviewed label override: {review.reason}",
            }
        )

    @classmethod
    def _eligibility_reason(
        cls,
        diagnosis: FailureDiagnosis,
        finding: DiagnosticFinding,
        review: Optional[FailureReviewDecision],
    ) -> str:
        if review is not None and review.action == "exclude":
            return f"excluded by review: {review.reason}"
        if review is not None and review.action == "include" and review.override_label is not None:
            return f"included by reviewed label override: {review.reason}"
        if diagnosis.status != "diagnosed":
            return "diagnosis abstained because observable trace evidence was insufficient"
        if finding.label in cls.EXCLUDED_LABELS:
            return "infrastructure, budget, Judge, or unknown failures do not guide Skill changes"
        if review is not None:
            return f"included by review: {review.reason}"
        return "observable task failure can be changed by Skill guidance"

    @staticmethod
    def _decision(
        experiment_id: UUID,
        diagnosis: FailureDiagnosis,
        finding: DiagnosticFinding,
        *,
        eligible: bool,
        reason: str,
        review_applied: bool = False,
    ) -> ObservedFindingDecision:
        refs = tuple(
            f"trace://{experiment_id}/{diagnosis.run_id}/{diagnosis.attempt_id}#event-{number}"
            for number in finding.evidence_sequence_nos
        )
        return ObservedFindingDecision(
            run_id=diagnosis.run_id,
            attempt_id=diagnosis.attempt_id,
            label=finding.label,
            rule_id=finding.rule_id,
            confidence=finding.confidence,
            evidence_sequence_nos=finding.evidence_sequence_nos,
            trace_event_refs=refs,
            eligible=eligible,
            reason=reason,
            observed_summary=sanitize_observed_summary(finding.rationale),
            review_applied=review_applied,
        )

    @staticmethod
    def _decision_key(item: ObservedFindingDecision) -> Tuple[str, str, str]:
        return (item.label.value, item.rule_id, str(item.run_id))

    @staticmethod
    def _clusters(
        decisions: list[ObservedFindingDecision],
    ) -> Tuple[FailureEvidenceCluster, ...]:
        grouped: Dict[Tuple[FailureLabel, str], list[ObservedFindingDecision]] = {}
        for item in decisions:
            grouped.setdefault((item.label, item.rule_id), []).append(item)
        return tuple(
            FailureEvidenceCluster(
                label=label,
                rule_id=rule_id,
                finding_count=len(items),
                evidence_refs=tuple(
                    sorted(
                        {
                            ref
                            for item in items
                            for ref in (
                                item.trace_event_refs
                                or (f"diagnosis://{item.run_id}/{item.rule_id}",)
                            )
                        }
                    )
                ),
            )
            for (label, rule_id), items in sorted(
                grouped.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        )
