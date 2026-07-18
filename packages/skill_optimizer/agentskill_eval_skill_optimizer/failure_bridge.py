"""Export observed Skill-run failures into the existing evolution input contract."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional, Sequence, Tuple
from uuid import UUID

import yaml
from pydantic import Field, model_validator

from agentskill_eval_contracts import (
    AttributionRole,
    DiagnosticFinding,
    EvaluationOutcome,
    ExperimentManifest,
    ExperimentVariant,
    FailureDiagnosis,
    FailureLabel,
    FrozenModel,
    RealEvidenceClass,
    RealEvidenceRunManifest,
    RealEvidenceStatus,
    RealExperimentReport,
    RealPreflightReport,
    Run,
    TraceEvent,
    TraceManifest,
    VariantRole,
    canonical_json,
)
from agentskill_eval_experiment import ExactSecretScanner, LocalExperimentStore
from agentskill_eval_experiment.storage import AtomicFileWriter, ExperimentLayout, load_model
from agentskill_eval_skill_optimizer.evolution import (
    EvolutionError,
    FailureBundleSecretScan,
    FailureEvidenceBundle,
    ObservedFailureProvenance,
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
    provenance: Optional[ObservedFailureProvenance] = None
    insufficiency_reason: Optional[str] = None

    @model_validator(mode="after")
    def status_matches_bundle(self) -> "FailureBridgeReport":
        if self.status == "READY":
            if (
                not self.eligible
                or not self.bundle_path
                or not self.bundle_sha256
                or self.provenance is None
            ):
                raise ValueError("READY reports require eligible evidence and a bundle")
            if self.insufficiency_reason is not None:
                raise ValueError("READY reports cannot contain insufficiency_reason")
        elif (
            self.bundle_path is not None
            or self.bundle_sha256 is not None
            or self.provenance is not None
        ):
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


@dataclass(frozen=True)
class ObservedFailureSource:
    """Validated, non-secret source identity for a completed observed experiment."""

    experiment: ExperimentManifest
    real_run: RealEvidenceRunManifest
    skill_variant: ExperimentVariant
    experiment_path: Path
    real_run_path: Path
    report_path: Path
    runner_version: str
    runner_sha256: str
    secret_env_names: Tuple[str, ...]
    source_attempt_scan_verified: bool


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
        source = self._source(experiment_id)
        experiment = source.experiment
        variant = source.skill_variant
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
                        findings=self._sanitize_findings(accepted_findings),
                    )
                )

        clusters = self._clusters(eligible)
        output = output.expanduser().resolve()
        bundle: Optional[FailureEvidenceBundle] = None
        bundle_bytes: Optional[bytes] = None
        if diagnoses:
            bundle, bundle_bytes = self._bundle(
                source,
                name=f"{experiment.name} observed train failures",
                diagnoses=tuple(diagnoses),
            )
        return self._finalize(
            source,
            output,
            bundle=bundle,
            bundle_bytes=bundle_bytes,
            treatment_run_count=len(treatment_runs),
            task_failed_run_count=task_failed_runs,
            invalid_run_count=invalid_runs,
            unfinished_run_count=unfinished_runs,
            eligible=eligible,
            excluded=excluded,
            clusters=clusters,
        )

    def derive(
        self,
        experiment_id: UUID,
        parent_bundle_path: Path,
        output: Path,
    ) -> FailureBridgeResult:
        """Attach immutable observed provenance to a legacy reviewed failure bundle."""
        source = self._source(experiment_id)
        parent_path = parent_bundle_path.expanduser().resolve(strict=True)
        output = output.expanduser().resolve()
        if output == parent_path:
            raise FailureBridgeError(
                "derived failure bundle must not overwrite its parent evidence"
            )
        try:
            parent = FailureEvidenceBundle.load(parent_path)
        except EvolutionError as exc:
            raise FailureBridgeError(str(exc)) from exc
        parent_bytes = parent_path.read_bytes()
        self._validate_parent_bundle(source, parent)
        diagnoses = self._sanitize_diagnoses(parent.diagnoses)
        eligible, excluded = self._decisions_for_diagnoses(source, diagnoses)
        if not eligible:
            raise FailureBridgeError("parent bundle contains no eligible observed task failures")
        bundle, bundle_bytes = self._bundle(
            source,
            name=f"{parent.name} derived observed provenance",
            diagnoses=diagnoses,
            parent_bundle_sha256=self._sha(parent_bytes),
        )
        treatment_runs = self._treatment_runs(source)
        return self._finalize(
            source,
            output,
            bundle=bundle,
            bundle_bytes=bundle_bytes,
            treatment_run_count=len(treatment_runs),
            task_failed_run_count=sum(
                run.evaluation_outcome == EvaluationOutcome.FAIL for run in treatment_runs
            ),
            invalid_run_count=sum(
                run.evaluation_outcome == EvaluationOutcome.INVALID for run in treatment_runs
            ),
            unfinished_run_count=sum(run.evaluation_outcome is None for run in treatment_runs),
            eligible=eligible,
            excluded=excluded,
            clusters=self._clusters(eligible),
        )

    def verify_derived(
        self,
        experiment_id: UUID,
        parent_bundle_path: Path,
        derived_bundle_path: Path,
    ) -> FailureEvidenceBundle:
        """Revalidate the source, parent and deterministic derived content.

        A parent hash alone is insufficient: an editor could retain it while
        changing the sanitized findings consumed by the optimizer.  The
        derived bundle must therefore equal the deterministic transformation
        of the supplied parent evidence.
        """
        source = self._source(experiment_id)
        parent_path = parent_bundle_path.expanduser().resolve(strict=True)
        parent = FailureEvidenceBundle.load(parent_path)
        derived = FailureEvidenceBundle.load(derived_bundle_path.expanduser().resolve(strict=True))
        provenance = derived.provenance
        if (
            provenance is None
            or provenance.parent_bundle_sha256 != self._sha(parent_path.read_bytes())
        ):
            raise FailureBridgeError("derived bundle parent hash does not match supplied parent")
        self._validate_parent_bundle(source, parent)
        self._validate_provenance(source, provenance)
        expected_name = f"{parent.name} derived observed provenance"
        expected_diagnoses = self._sanitize_diagnoses(parent.diagnoses)
        if derived.name != expected_name:
            raise FailureBridgeError("derived bundle name does not match its parent derivation")
        if derived.split != parent.split:
            raise FailureBridgeError("derived bundle split does not match its parent")
        if derived.diagnoses != expected_diagnoses:
            raise FailureBridgeError(
                "derived bundle diagnoses do not match the deterministic parent derivation"
            )
        return derived

    def _source(self, experiment_id: UUID) -> ObservedFailureSource:
        layout = ExperimentLayout(self.workspace, experiment_id)
        experiment = self.store.load_experiment(experiment_id)
        real_run = self._load_observed_run(experiment_id)
        variant = self._skill_variant(experiment_id)
        if variant.skill_snapshot is None:
            raise FailureBridgeError("selected Skill variant has no Skill snapshot")
        if variant.runner_snapshot.config.get("simulated") is True:
            raise FailureBridgeError("failure preparation rejects simulated Variant evidence")
        if variant.agent_snapshot.model != real_run.model:
            raise FailureBridgeError("treatment Variant model does not match observed run")
        configured_provider = variant.agent_snapshot.generation_parameters.get("provider")
        if configured_provider is not None and configured_provider != real_run.provider:
            raise FailureBridgeError("treatment Variant provider does not match observed run")

        runner_version = variant.runner_snapshot.version
        runner_sha = variant.runner_snapshot.binary_sha256
        secret_names: Tuple[str, ...] = ()
        preflight_path = layout.root / "real-preflight.json"
        if preflight_path.is_file():
            try:
                preflight = load_model(preflight_path.read_bytes(), RealPreflightReport)
            except (OSError, ValueError) as exc:
                raise FailureBridgeError(f"invalid observed preflight manifest: {exc}") from exc
            if (
                preflight.config_sha256 != real_run.config_sha256
                or preflight.provider != real_run.provider
                or preflight.model != real_run.model
                or preflight.dataset_sha256 != experiment.dataset_sha256
                or preflight.simulated
                or preflight.evidence_class != RealEvidenceClass.OBSERVED_AGENT
            ):
                raise FailureBridgeError("observed preflight provenance does not match source run")
            if (
                preflight.runner.version != variant.runner_snapshot.version
                or preflight.runner.sha256 != variant.runner_snapshot.binary_sha256
            ):
                raise FailureBridgeError(
                    "source Runner hash/version drifted from treatment Variant"
                )
            runner_version = preflight.runner.version
            runner_sha = preflight.runner.sha256
            secret_names = preflight.secret_env_names

        report_path = layout.reports / "real-experiment-report.json"
        if report_path.is_file():
            try:
                report = load_model(report_path.read_bytes(), RealExperimentReport)
            except (OSError, ValueError) as exc:
                raise FailureBridgeError(f"invalid observed real report: {exc}") from exc
            if (
                report.run != real_run
                or report.dataset_sha256 != experiment.dataset_sha256
                or report.provider != real_run.provider
                or report.model != real_run.model
                or report.simulated
                or report.evidence_class != RealEvidenceClass.OBSERVED_AGENT
            ):
                raise FailureBridgeError(
                    "observed real report provenance does not match source run"
                )
        else:
            report_path = layout.reports / "report.json"
            if not report_path.is_file():
                raise FailureBridgeError("source experiment has no immutable report")

        return ObservedFailureSource(
            experiment=experiment,
            real_run=real_run,
            skill_variant=variant,
            experiment_path=layout.experiment,
            real_run_path=layout.root / "real-evidence-run.json",
            report_path=report_path,
            runner_version=runner_version,
            runner_sha256=runner_sha,
            secret_env_names=secret_names,
            source_attempt_scan_verified=self._source_attempt_scan_verified(experiment.id),
        )

    def _bundle(
        self,
        source: ObservedFailureSource,
        *,
        name: str,
        diagnoses: Tuple[FailureDiagnosis, ...],
        parent_bundle_sha256: Optional[str] = None,
    ) -> Tuple[FailureEvidenceBundle, bytes]:
        provisional = ObservedFailureProvenance(
            source_experiment_id=source.experiment.id,
            source_experiment_sha256=self._sha_file(source.experiment_path),
            source_real_run_sha256=self._sha_file(source.real_run_path),
            source_report_sha256=self._sha_file(source.report_path),
            parent_bundle_sha256=parent_bundle_sha256,
            provider=source.real_run.provider,
            model=source.real_run.model,
            runner_version=source.runner_version,
            runner_sha256=source.runner_sha256,
            agent_config_sha256=source.real_run.config_sha256,
            dataset_version_sha256=source.experiment.dataset_sha256,
            secret_scan=FailureBundleSecretScan(
                configured_secret_count=len(source.secret_env_names),
                exact_values_available=not source.secret_env_names,
                source_attempt_scan_verified=source.source_attempt_scan_verified,
            ),
        )
        bundle = FailureEvidenceBundle(
            schema_version="ase/failure-evidence-bundle/v1alpha1",
            name=name,
            split="train",
            diagnoses=diagnoses,
            agent_provider=source.real_run.provider,
            agent_model=source.real_run.model,
            provenance=provisional,
        )
        content = self._bundle_bytes(bundle)
        receipt = self._scan_secrets(source, (("failure-bundle.yaml", content),))
        provenance = provisional.model_copy(update={"secret_scan": receipt})
        bundle = bundle.model_copy(update={"provenance": provenance})
        content = self._bundle_bytes(bundle)
        self._scan_secrets(source, (("failure-bundle.yaml", content),))
        return bundle, content

    def _finalize(
        self,
        source: ObservedFailureSource,
        output: Path,
        *,
        bundle: Optional[FailureEvidenceBundle],
        bundle_bytes: Optional[bytes],
        treatment_run_count: int,
        task_failed_run_count: int,
        invalid_run_count: int,
        unfinished_run_count: int,
        eligible: Sequence[ObservedFindingDecision],
        excluded: Sequence[ObservedFindingDecision],
        clusters: Tuple[FailureEvidenceCluster, ...],
    ) -> FailureBridgeResult:
        skill = source.skill_variant.skill_snapshot
        if skill is None:
            raise FailureBridgeError("selected Skill variant has no Skill snapshot")
        ready = bundle is not None and bundle_bytes is not None
        report = FailureBridgeReport(
            status="READY" if ready else "INSUFFICIENT",
            experiment_id=source.experiment.id,
            experiment_sha256=self._sha_file(source.experiment_path),
            real_run_sha256=self._sha_file(source.real_run_path),
            skill_variant_id=source.skill_variant.id,
            skill_sha256=skill.content_sha256,
            treatment_run_count=treatment_run_count,
            task_failed_run_count=task_failed_run_count,
            invalid_run_count=invalid_run_count,
            unfinished_run_count=unfinished_run_count,
            eligible=tuple(sorted(eligible, key=self._decision_key)),
            excluded=tuple(sorted(excluded, key=self._decision_key)),
            clusters=clusters,
            bundle_path=str(output) if ready else None,
            bundle_sha256=self._sha(bundle_bytes) if bundle_bytes is not None else None,
            provenance=bundle.provenance if bundle is not None else None,
            insufficiency_reason=(
                None
                if ready
                else "no eligible task-failure findings were observed in the Skill treatment arm"
            ),
        )
        report_path = output.with_suffix(output.suffix + ".audit.json")
        report_bytes = canonical_json(report.model_dump(mode="json")) + b"\n"
        payloads = [("failure-bridge-audit.json", report_bytes)]
        if bundle_bytes is not None:
            payloads.append(("failure-bundle.yaml", bundle_bytes))
        self._scan_secrets(source, tuple(payloads))
        if bundle_bytes is not None:
            self._write_immutable(output, bundle_bytes)
        self._write_immutable(report_path, report_bytes)
        return FailureBridgeResult(
            report=report,
            report_path=report_path,
            bundle=bundle,
            bundle_path=output if bundle is not None else None,
        )

    def _validate_parent_bundle(
        self, source: ObservedFailureSource, parent: FailureEvidenceBundle
    ) -> None:
        if parent.agent_provider is not None and parent.agent_provider != source.real_run.provider:
            raise FailureBridgeError("parent bundle provider does not match observed source")
        if parent.agent_model is not None and parent.agent_model != source.real_run.model:
            raise FailureBridgeError("parent bundle model does not match observed source")
        if parent.provenance is not None:
            self._validate_provenance(source, parent.provenance)
        runs = {item.id: item for item in self._treatment_runs(source)}
        for diagnosis in parent.diagnoses:
            run = runs.get(diagnosis.run_id)
            if run is None or run.evaluation_outcome != EvaluationOutcome.FAIL:
                raise FailureBridgeError(
                    "parent bundle diagnosis is not a source task-failed treatment Run"
                )
            attempt = self.store.load_selected_attempt(source.experiment.id, run)
            if attempt is None or attempt.id != diagnosis.attempt_id:
                raise FailureBridgeError(
                    "parent bundle diagnosis attempt does not match source evidence"
                )

    def _validate_provenance(
        self, source: ObservedFailureSource, provenance: ObservedFailureProvenance
    ) -> None:
        expected = {
            "source_experiment_id": source.experiment.id,
            "source_experiment_sha256": self._sha_file(source.experiment_path),
            "source_real_run_sha256": self._sha_file(source.real_run_path),
            "source_report_sha256": self._sha_file(source.report_path),
            "provider": source.real_run.provider,
            "model": source.real_run.model,
            "runner_version": source.runner_version,
            "runner_sha256": source.runner_sha256,
            "agent_config_sha256": source.real_run.config_sha256,
            "dataset_version_sha256": source.experiment.dataset_sha256,
        }
        if any(getattr(provenance, field) != value for field, value in expected.items()):
            raise FailureBridgeError("observed failure provenance source hash or identity drifted")

    def _decisions_for_diagnoses(
        self,
        source: ObservedFailureSource,
        diagnoses: Tuple[FailureDiagnosis, ...],
    ) -> Tuple[list[ObservedFindingDecision], list[ObservedFindingDecision]]:
        eligible: list[ObservedFindingDecision] = []
        excluded: list[ObservedFindingDecision] = []
        for diagnosis in diagnoses:
            for finding in diagnosis.findings:
                accepted = (
                    diagnosis.status == "diagnosed" and finding.label not in self.EXCLUDED_LABELS
                )
                decision = self._decision(
                    source.experiment.id,
                    diagnosis,
                    finding,
                    eligible=accepted,
                    reason=(
                        "derived from immutable observed task-failure evidence"
                        if accepted
                        else "derived finding is not eligible for Skill optimization"
                    ),
                )
                (eligible if accepted else excluded).append(decision)
        return eligible, excluded

    @staticmethod
    def _sanitize_findings(
        findings: Sequence[DiagnosticFinding],
    ) -> Tuple[DiagnosticFinding, ...]:
        return tuple(
            item.model_copy(update={"rationale": sanitize_observed_summary(item.rationale)})
            for item in findings
        )

    @classmethod
    def _sanitize_diagnoses(
        cls, diagnoses: Sequence[FailureDiagnosis]
    ) -> Tuple[FailureDiagnosis, ...]:
        return tuple(
            item.model_copy(update={"findings": cls._sanitize_findings(item.findings)})
            for item in diagnoses
        )

    def _scan_secrets(
        self,
        source: ObservedFailureSource,
        payloads: Sequence[Tuple[str, bytes]],
    ) -> FailureBundleSecretScan:
        secrets = {
            name: value
            for name in source.secret_env_names
            if (value := os.environ.get(name))
        }
        if len(secrets) != len(source.secret_env_names):
            if not source.source_attempt_scan_verified:
                raise FailureBridgeError(
                    "source Secret values are unavailable and source attempt scans are incomplete"
                )
            return FailureBundleSecretScan(
                configured_secret_count=len(source.secret_env_names),
                exact_values_available=False,
                source_attempt_scan_verified=True,
            )
        result = ExactSecretScanner().scan(payloads, secrets)
        if not result.clean:
            raise FailureBridgeError(
                "derived failure evidence contains a configured Secret: "
                + ", ".join(result.matched_secret_names)
            )
        return FailureBundleSecretScan(
            configured_secret_count=len(source.secret_env_names),
            exact_values_available=True,
            source_attempt_scan_verified=source.source_attempt_scan_verified,
        )

    def _source_attempt_scan_verified(self, experiment_id: UUID) -> bool:
        runs = self.store.list_runs(experiment_id)
        if not runs:
            return False
        for run in runs:
            attempt = self.store.load_selected_attempt(experiment_id, run)
            if attempt is None:
                return False
            try:
                scan = self.store.load_security_scan(experiment_id, run.id, attempt.attempt_no)
            except (OSError, ValueError):
                return False
            if scan.status != "clean" or scan.matched_secret_names:
                return False
        return True

    def _treatment_runs(self, source: ObservedFailureSource) -> Tuple[Run, ...]:
        return tuple(
            item
            for item in self.store.list_runs(source.experiment.id)
            if item.variant_id == source.skill_variant.id
        )

    @staticmethod
    def _bundle_bytes(bundle: FailureEvidenceBundle) -> bytes:
        return yaml.safe_dump(
            bundle.model_dump(mode="json"), allow_unicode=True, sort_keys=False
        ).encode("utf-8")

    def _write_immutable(self, path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise FailureBridgeError(f"immutable failure evidence already differs: {path}")
            return
        self.writer.write(path, content)

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def _sha_file(cls, path: Path) -> str:
        try:
            return cls._sha(path.read_bytes())
        except OSError as exc:
            raise FailureBridgeError(f"cannot hash provenance source {path}: {exc}") from exc

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
