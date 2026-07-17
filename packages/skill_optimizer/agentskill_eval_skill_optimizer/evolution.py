"""Failure-guided, leakage-safe bridge from diagnoses to existing Skill search."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional, Protocol, Tuple, Union
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator

from agentskill_eval_benchmark_gen import DatasetLoader, DatasetSplit
from agentskill_eval_contracts import (
    CandidateEvaluation,
    FailureDiagnosis,
    FailureLabel,
    FrozenModel,
    SearchEvaluationStage,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_skill_optimizer.deepseek_generator import (
    DeepSeekGeneratorAuthorization,
    DeepSeekGeneratorError,
    DeepSeekGeneratorInvocationEvidence,
    DeepSeekHypothesisGenerator,
)
from agentskill_eval_skill_optimizer.evaluator import build_evaluator
from agentskill_eval_skill_optimizer.process_generator import (
    GeneratorInvocationEvidence,
    HypothesisGeneratorSpec,
    ProcessGeneratorError,
    ProcessHypothesisGenerator,
)
from agentskill_eval_skill_optimizer.real_evaluator import RealEvaluationAuthorization
from agentskill_eval_skill_optimizer.search import (
    BenchmarkGuidedSkillSearch,
    SkillSearchResult,
)
from agentskill_eval_skill_optimizer.spec import (
    EvaluatorSpec,
    MutationSpec,
    OptimizationSearchSpec,
    SearchAlgorithmSpec,
    SearchBudgetSpec,
    SearchCase,
    SearchConstraintSpec,
    ValidationSearchDataset,
)


class EvolutionError(RuntimeError):
    """Raised when an evolution input violates isolation, integrity, or quality gates."""


class FailureEvidenceBundle(FrozenModel):
    schema_version: Literal["ase/failure-evidence-bundle/v1alpha1"]
    name: str = Field(min_length=1)
    split: Literal["train"]
    diagnoses: Tuple[FailureDiagnosis, ...] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> "FailureEvidenceBundle":
        try:
            return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise EvolutionError(f"invalid train failure bundle {path}: {exc}") from exc


EXCLUDED_PROPOSAL_FAILURES = {
    FailureLabel.ENVIRONMENT,
    FailureLabel.BUDGET,
    FailureLabel.JUDGE,
    FailureLabel.UNKNOWN,
}


class FailureGuidedEvolutionSpec(FrozenModel):
    schema_version: Literal["ase/failure-guided-evolution/v1alpha1"]
    name: str = Field(min_length=1)
    base_skill_path: Path
    manual_skill_path: Path
    failure_bundle_path: Path
    validation_search_path: Path
    regression_dev_path: Path
    generator: HypothesisGeneratorSpec
    search: SearchAlgorithmSpec
    constraints: SearchConstraintSpec = SearchConstraintSpec()
    budget: SearchBudgetSpec
    evaluator: EvaluatorSpec

    @classmethod
    def load(cls, path: Path) -> "FailureGuidedEvolutionSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = cls.model_validate(payload)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise EvolutionError(f"invalid evolution spec {path}: {exc}") from exc
        root = path.resolve(strict=True).parent

        def resolved(value: Path) -> Path:
            candidate = value if value.is_absolute() else root / value
            if candidate.is_symlink():
                raise EvolutionError("symbolic-link evolution inputs are not allowed")
            return candidate.resolve(strict=True)

        generator = spec.generator
        if generator.executable is not None:
            generator = generator.model_copy(update={"executable": resolved(generator.executable)})
        evaluator = spec.evaluator
        if evaluator.real_agent_config_path is not None:
            evaluator = evaluator.model_copy(
                update={"real_agent_config_path": resolved(evaluator.real_agent_config_path)}
            )
        return spec.model_copy(
            update={
                "base_skill_path": resolved(spec.base_skill_path),
                "manual_skill_path": resolved(spec.manual_skill_path),
                "failure_bundle_path": resolved(spec.failure_bundle_path),
                "validation_search_path": resolved(spec.validation_search_path),
                "regression_dev_path": resolved(spec.regression_dev_path),
                "generator": generator,
                "evaluator": evaluator,
            }
        )


class RegressionDevDataset(FrozenModel):
    schema_version: Literal["ase/optimizer-regression/v1alpha1"]
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    split: Literal["regression_dev"]
    simulated: bool
    cases: Tuple[SearchCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_must_be_unique(self) -> "RegressionDevDataset":
        ids = [item.id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("regression_dev case IDs must be unique")
        if self.simulated and any(not item.required_terms for item in self.cases):
            raise ValueError("simulated regression_dev cases require required_terms")
        return self

    @classmethod
    def load(cls, path: Path) -> "RegressionDevDataset":
        try:
            return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise EvolutionError(f"invalid regression_dev dataset {path}: {exc}") from exc


class EligibilityDecision(FrozenModel):
    run_id: UUID
    label: FailureLabel
    rule_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_sequence_nos: Tuple[int, ...]
    eligible: bool
    reason: str = Field(min_length=1)
    observed_summary: str = Field(default="", max_length=1200, exclude=True)


def classify_failure_bundle(bundle: FailureEvidenceBundle) -> Tuple[EligibilityDecision, ...]:
    """Reduce train diagnoses to the sanitized evidence visible to proposal generators."""

    decisions = []
    for diagnosis in bundle.diagnoses:
        for finding in diagnosis.findings:
            eligible = (
                diagnosis.status == "diagnosed"
                and finding.label not in EXCLUDED_PROPOSAL_FAILURES
            )
            decisions.append(
                EligibilityDecision(
                    run_id=diagnosis.run_id,
                    label=finding.label,
                    rule_id=finding.rule_id,
                    confidence=finding.confidence,
                    evidence_sequence_nos=finding.evidence_sequence_nos,
                    eligible=eligible,
                    reason=(
                        "observable Agent/Skill behavior can be changed by guidance"
                        if eligible
                        else "infrastructure, budget, Judge, unknown, or abstained failure"
                    ),
                    observed_summary=sanitize_observed_summary(finding.rationale),
                )
            )
    return tuple(
        sorted(decisions, key=lambda item: (str(item.run_id), item.label.value, item.rule_id))
    )


def sanitize_observed_summary(value: str) -> str:
    """Keep proposal context actionable without persisting secrets or host paths."""

    summary = re.sub(
        r"(?i)\b(secret|api[_-]?key|token|password)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        value,
    )
    summary = re.sub(r"/(?:Users|home|private|tmp|var)/[^\s,;]+", "<path>", summary)
    return summary.strip()[:1200]


class ImprovementHypothesis(FrozenModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    failure_label: FailureLabel
    hypothesis: str = Field(min_length=10)
    instruction: str = Field(min_length=10)
    evidence_refs: Tuple[str, ...] = Field(min_length=1)
    risks: Tuple[str, ...] = ()


GeneratorEvidence = Union[  # noqa: UP007
    GeneratorInvocationEvidence, DeepSeekGeneratorInvocationEvidence
]


class HypothesisArtifact(FrozenModel):
    schema_version: Literal["ase/improvement-hypotheses/v1alpha1"] = (
        "ase/improvement-hypotheses/v1alpha1"
    )
    generator: str = Field(min_length=1)
    hypotheses: Tuple[ImprovementHypothesis, ...] = Field(min_length=3)
    invocation_evidence: Optional[GeneratorEvidence] = None

    @model_validator(mode="after")
    def ids_and_evidence_must_match(self) -> "HypothesisArtifact":
        ids = [item.id for item in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("improvement hypothesis IDs must be unique")
        if self.invocation_evidence is not None:
            if self.invocation_evidence.hypothesis_count != len(self.hypotheses):
                raise ValueError("generator evidence hypothesis count mismatch")
            expected = stable_sha256([item.model_dump(mode="json") for item in self.hypotheses])
            if self.invocation_evidence.hypotheses_sha256 != expected:
                raise ValueError("generator evidence hypothesis hash mismatch")
        return self


class OptimizationContext(FrozenModel):
    schema_version: Literal["ase/optimization-context/v1alpha1"] = (
        "ase/optimization-context/v1alpha1"
    )
    source_split: Literal["train"]
    base_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligible: Tuple[EligibilityDecision, ...]
    excluded: Tuple[EligibilityDecision, ...]
    locked_test_accessed: Literal[False] = False
    raw_rationale_stored: Literal[False] = False
    hidden_reasoning_stored: Literal[False] = False


def build_hypothesis_request(
    base_skill: Path, context: OptimizationContext, max_hypotheses: int
) -> Dict[str, object]:
    """Build the sole payload allowed to cross a proposal-generator trust boundary."""

    return {
        "schema_version": "ase/process-hypothesis-request/v1alpha1",
        "source_split": "train",
        "base_skill": {
            "sha256": context.base_skill_sha256,
            "content": base_skill.read_text(encoding="utf-8"),
        },
        "eligible_failures": [
            {
                "failure_label": item.label.value,
                "rule_id": item.rule_id,
                "confidence": item.confidence,
                "observed_summary": item.observed_summary,
            }
            for item in context.eligible
        ],
        "max_hypotheses": max_hypotheses,
        "output_contract": "structured_hypotheses_only_no_case_answers_no_hidden_reasoning",
    }


def proposal_evidence_refs(
    context: OptimizationContext, label: FailureLabel
) -> Tuple[str, ...]:
    """Return stable lineage references without exposing diagnosis rationale text."""

    refs = tuple(
        sorted(
            f"diagnosis://{item.run_id}/{item.rule_id}"
            for item in context.eligible
            if item.label == label
        )
    )
    if not refs:
        raise EvolutionError("proposal has no eligible train evidence")
    return refs


class EvolutionHandoff(FrozenModel):
    schema_version: Literal["ase/evolution-final-handoff/v1alpha1"] = (
        "ase/evolution-final-handoff/v1alpha1"
    )
    evolution_id: UUID
    optimization_job_id: UUID
    base_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    winner_candidate_id: UUID
    winner_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    winner_skill_path: str = Field(min_length=1)
    status: Literal["AWAITING_INDEPENDENT_FINAL_EVALUATION"]
    locked_test_accessed: Literal[False] = False
    auto_publish: Literal[False] = False


class RegressionGateResult(FrozenModel):
    schema_version: Literal["ase/evolution-regression-gate/v1alpha1"] = (
        "ase/evolution-regression-gate/v1alpha1"
    )
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base: CandidateEvaluation
    winner: CandidateEvaluation
    loss_cases: Tuple[str, ...]
    # Invalid observations are not task failures.  They are unusable evidence
    # (for example a provider/runner error) and must prevent a regression gate
    # from being reported as passed.
    invalid_cases: Tuple[str, ...] = ()
    token_overhead_ratio: float
    max_loss_cases: int = Field(ge=0)
    max_token_overhead_ratio: float = Field(ge=0)
    passed: bool

    @model_validator(mode="after")
    def result_must_match_evidence_and_thresholds(self) -> "RegressionGateResult":
        evaluations = (self.base, self.winner)
        if any(item.stage != SearchEvaluationStage.REGRESSION_DEV for item in evaluations):
            raise ValueError("regression gate evaluations must use regression_dev")
        if any(item.dataset_sha256 != self.dataset_sha256 for item in evaluations):
            raise ValueError("regression gate dataset hash mismatch")
        if self.base.case_ids != self.winner.case_ids:
            raise ValueError("regression gate base/winner case sets must match")
        base_by_case = {item.case_id: item for item in self.base.results}
        expected_losses = tuple(
            item.case_id
            for item in self.winner.results
            if base_by_case[item.case_id].passed and not item.passed
        )
        if self.loss_cases != expected_losses:
            raise ValueError("regression gate loss_cases do not match evidence")
        expected_invalid_cases = tuple(
            dict.fromkeys(
                item.case_id
                for evaluation in evaluations
                for item in evaluation.results
                if item.outcome == "invalid"
            )
        )
        if self.invalid_cases != expected_invalid_cases:
            raise ValueError("regression gate invalid_cases do not match evidence")
        expected_overhead = (self.winner.total_tokens - self.base.total_tokens) / max(
            1, self.base.total_tokens
        )
        if abs(self.token_overhead_ratio - expected_overhead) > 1e-12:
            raise ValueError("regression gate token overhead does not match evidence")
        expected_pass = (
            not expected_invalid_cases
            and len(expected_losses) <= self.max_loss_cases
            and expected_overhead <= self.max_token_overhead_ratio
        )
        if self.passed != expected_pass:
            raise ValueError("regression gate decision does not match thresholds")
        return self


class EvolutionReport(FrozenModel):
    schema_version: Literal["ase/evolution-report/v1alpha1"] = "ase/evolution-report/v1alpha1"
    evolution_id: UUID
    name: str
    generator_identity: str = Field(default="legacy-unrecorded", min_length=1)
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypotheses: Tuple[ImprovementHypothesis, ...]
    optimization_job_id: UUID
    candidate_count: int = Field(ge=1)
    winner_candidate_id: UUID
    winner_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    regression_gate: RegressionGateResult
    generator_evidence: Optional[GeneratorEvidence] = None
    simulated: bool
    locked_test_accessed: Literal[False] = False
    claim_limit: str = Field(min_length=1)
    artifacts: Dict[str, str]


class HypothesisGenerator(Protocol):
    @property
    def identity(self) -> str: ...

    def generate(
        self, context: OptimizationContext, limit: int
    ) -> Tuple[ImprovementHypothesis, ...]: ...


_GUIDANCE: Dict[FailureLabel, Tuple[str, str, Tuple[str, ...]]] = {
    FailureLabel.TASK_UNDERSTANDING: (
        "Explicit canonical-boundary checks reduce task interpretation mismatches.",
        "Normalize at producer and consumer boundaries, then compare the canonical values.",
        ("May add unnecessary normalization checks when formats are already canonical.",),
    ),
    FailureLabel.PLANNING: (
        "Exceptional-path planning reduces incomplete resource analysis.",
        "For resources, verify cleanup on every exception path before reporting a leak.",
        ("May increase inspection latency.",),
    ),
    FailureLabel.TOOL_SELECTION: (
        "Explicit tool preconditions reduce unsupported tool selection.",
        "Before acting, match the task goal to each tool capability and choose only a "
        "supported tool.",
        ("May add a planning step before simple calls.",),
    ),
    FailureLabel.TOOL_ARGUMENT: (
        "Schema-first argument validation reduces rejected tool calls.",
        "Validate every required tool argument against its schema before executing the call.",
        ("May increase token use for parameter checks.",),
    ),
    FailureLabel.TOOL_RECOVERY: (
        "Explicit retry accounting prevents recovery budget mistakes.",
        "Derive the attempt count from the configured retry budget before judging the loop.",
        ("May increase calls when failures are retryable.",),
    ),
    FailureLabel.RETRIEVAL: (
        "Evidence-use checks improve retrieval-grounded answers.",
        "After retrieval, cite and use relevant returned evidence before producing the final "
        "answer.",
        ("May increase context processing cost.",),
    ),
    FailureLabel.MEMORY: (
        "Session and freshness checks reduce unsafe Memory use.",
        "Validate Memory session scope and freshness before reading, updating, or forgetting "
        "a value.",
        ("May reject ambiguous cross-session requests.",),
    ),
    FailureLabel.SKILL_CONFLICT: (
        "Conflict resolution prevents incompatible Skill instructions from being followed "
        "together.",
        "When Skill instructions conflict, prefer the narrower safety-preserving rule and record "
        "the conflict.",
        ("May abstain when priority cannot be established.",),
    ),
    FailureLabel.VERIFICATION: (
        "A runtime evidence gate reduces unsupported findings.",
        "Require reachable runtime evidence before reporting any actionable defect.",
        ("May reduce recall for issues without executable evidence.",),
    ),
}


class DeterministicHypothesisGenerator:
    def __init__(self, version: str) -> None:
        self._identity = f"deterministic-failure-guidance@{version}"

    @property
    def identity(self) -> str:
        return self._identity

    def generate(
        self, context: OptimizationContext, limit: int
    ) -> Tuple[ImprovementHypothesis, ...]:
        labels = sorted({item.label for item in context.eligible}, key=lambda item: item.value)
        hypotheses = []
        for label in labels:
            guidance = _GUIDANCE.get(label)
            if guidance is None:
                continue
            hypothesis, instruction, risks = guidance
            evidence = tuple(
                sorted(
                    f"diagnosis://{item.run_id}/{item.rule_id}"
                    for item in context.eligible
                    if item.label == label
                )
            )
            hypotheses.append(
                ImprovementHypothesis(
                    id=f"failure-{label.value.lower().replace('_', '-')}",
                    failure_label=label,
                    hypothesis=hypothesis,
                    instruction=instruction,
                    evidence_refs=evidence,
                    risks=risks,
                )
            )
        if len(hypotheses) < 3:
            raise EvolutionError(
                "at least three distinct eligible, supported failure labels are required"
            )
        return tuple(hypotheses[:limit])


@dataclass(frozen=True)
class FailureGuidedEvolutionResult:
    report: EvolutionReport
    search: SkillSearchResult
    report_json: Path
    report_html: Path
    handoff_path: Path


class FailureGuidedSkillEvolution:
    """Create hypotheses from train diagnoses and delegate selection to the existing search."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def run(
        self,
        spec: FailureGuidedEvolutionSpec,
        *,
        real_authorization: Optional[RealEvaluationAuthorization] = None,
        generator_authorization: Optional[DeepSeekGeneratorAuthorization] = None,
    ) -> FailureGuidedEvolutionResult:
        if not spec.evaluator.simulated and real_authorization is None:
            raise EvolutionError("real model evolution requires a confirmation and budget protocol")
        base = self._skill_file(spec.base_skill_path)
        manual = self._skill_file(spec.manual_skill_path)
        bundle = FailureEvidenceBundle.load(spec.failure_bundle_path)
        base_sha = self._sha(base.read_bytes())
        manual_sha = self._sha(manual.read_bytes())
        bundle_sha = self._sha(spec.failure_bundle_path.read_bytes())
        validation_sha = self._sha(spec.validation_search_path.read_bytes())
        regression_sha = self._sha(spec.regression_dev_path.read_bytes())
        decisions = classify_failure_bundle(bundle)
        context = OptimizationContext(
            source_split=bundle.split,
            base_skill_sha256=base_sha,
            failure_bundle_sha256=bundle_sha,
            eligible=tuple(item for item in decisions if item.eligible),
            excluded=tuple(item for item in decisions if not item.eligible),
        )
        deterministic_generator: Optional[DeterministicHypothesisGenerator] = None
        process_generator: Optional[ProcessHypothesisGenerator] = None
        deepseek_generator: Optional[DeepSeekHypothesisGenerator] = None
        if spec.generator.type == "process":
            try:
                process_generator = ProcessHypothesisGenerator(spec.generator)
            except (OSError, ValueError) as exc:
                raise EvolutionError(f"invalid Process Generator: {exc}") from exc
            generator_identity = process_generator.identity
        elif spec.generator.type == "deepseek":
            try:
                deepseek_generator = DeepSeekHypothesisGenerator(
                    spec.generator, generator_authorization
                )
            except ValueError as exc:
                raise EvolutionError(f"invalid DeepSeek Generator: {exc}") from exc
            generator_identity = deepseek_generator.identity
        else:
            deterministic_generator = DeterministicHypothesisGenerator(spec.generator.version)
            generator_identity = deterministic_generator.identity
        semantic_spec = spec.model_dump(
            mode="json",
            exclude={
                "base_skill_path",
                "manual_skill_path",
                "failure_bundle_path",
                "validation_search_path",
                "regression_dev_path",
            },
        )
        generator_spec = semantic_spec.get("generator")
        if isinstance(generator_spec, dict):
            generator_spec.pop("executable", None)
        evolution_id = uuid5(
            NAMESPACE_URL,
            "agentskill-eval:evolution:"
            + stable_sha256(
                {
                    "spec": semantic_spec,
                    "base": base_sha,
                    "manual": manual_sha,
                    "bundle": bundle_sha,
                    "validation_search": validation_sha,
                    "regression_dev": regression_sha,
                    "generator": generator_identity,
                }
            ),
        )
        output = self.workspace / "evolution-jobs" / str(evolution_id)
        hypotheses_path = output / "hypotheses.json"
        regression_path = output / "regression-gate.json"
        if hypotheses_path.exists():
            artifact = HypothesisArtifact.model_validate_json(hypotheses_path.read_bytes())
            if artifact.generator != generator_identity:
                raise EvolutionError("persisted hypothesis generator identity mismatch")
            if process_generator is not None or deepseek_generator is not None:
                request = build_hypothesis_request(
                    base, context, spec.generator.max_hypotheses
                )
                if artifact.invocation_evidence is None:
                    raise EvolutionError("persisted proposal Generator evidence is missing")
                expected_request_sha = self._sha(canonical_json(request))
                if artifact.invocation_evidence.request_sha256 != expected_request_sha:
                    if deepseek_generator is None:
                        raise EvolutionError("persisted Process Generator request hash mismatch")
                    expected_api_request_sha = deepseek_generator.request_sha256(request)
                    if artifact.invocation_evidence.request_sha256 != expected_api_request_sha:
                        raise EvolutionError("persisted DeepSeek Generator request hash mismatch")
            hypotheses = artifact.hypotheses
        elif process_generator is not None:
            request = build_hypothesis_request(base, context, spec.generator.max_hypotheses)
            try:
                generated = process_generator.generate(
                    request,
                    tuple(sorted({item.label for item in context.eligible}, key=lambda x: x.value)),
                )
            except ProcessGeneratorError as exc:
                raise EvolutionError(str(exc)) from exc
            hypotheses = tuple(
                ImprovementHypothesis(
                    id=item.id,
                    failure_label=item.failure_label,
                    hypothesis=item.hypothesis,
                    instruction=item.instruction,
                    evidence_refs=proposal_evidence_refs(context, item.failure_label),
                    risks=item.risks,
                )
                for item in generated.proposals
            )
            evidence = generated.evidence.model_copy(
                update={
                    "hypotheses_sha256": stable_sha256(
                        [item.model_dump(mode="json") for item in hypotheses]
                    )
                }
            )
            artifact = HypothesisArtifact(
                generator=generator_identity,
                hypotheses=hypotheses,
                invocation_evidence=evidence,
            )
            self._write(hypotheses_path, artifact.model_dump(mode="json"))
        elif deepseek_generator is not None:
            request = build_hypothesis_request(base, context, spec.generator.max_hypotheses)
            try:
                deepseek_generated = deepseek_generator.generate(
                    request,
                    tuple(sorted({item.label for item in context.eligible}, key=lambda x: x.value)),
                )
            except DeepSeekGeneratorError as exc:
                raise EvolutionError(str(exc)) from exc
            hypotheses = tuple(
                ImprovementHypothesis(
                    id=item.id,
                    failure_label=item.failure_label,
                    hypothesis=item.hypothesis,
                    instruction=item.instruction,
                    evidence_refs=proposal_evidence_refs(context, item.failure_label),
                    risks=item.risks,
                )
                for item in deepseek_generated.proposals
            )
            deepseek_evidence = deepseek_generated.evidence.model_copy(
                update={
                    "hypotheses_sha256": stable_sha256(
                        [item.model_dump(mode="json") for item in hypotheses]
                    )
                }
            )
            artifact = HypothesisArtifact(
                generator=generator_identity,
                hypotheses=hypotheses,
                invocation_evidence=deepseek_evidence,
            )
            self._write(hypotheses_path, artifact.model_dump(mode="json"))
        else:
            if deterministic_generator is None:
                raise AssertionError("deterministic generator was not initialized")
            hypotheses = deterministic_generator.generate(context, spec.generator.max_hypotheses)
            artifact = HypothesisArtifact(
                generator=generator_identity,
                hypotheses=hypotheses,
            )
            self._write(hypotheses_path, artifact.model_dump(mode="json"))
        frozen_inputs = (
            (base, base_sha),
            (manual, manual_sha),
            (spec.failure_bundle_path, bundle_sha),
            (spec.validation_search_path, validation_sha),
            (spec.regression_dev_path, regression_sha),
        )
        if any(self._sha(path.read_bytes()) != expected for path, expected in frozen_inputs):
            raise EvolutionError("proposal Generator mutated a frozen evolution input")
        search_spec = OptimizationSearchSpec(
            schema_version="ase/optimization-search/v1alpha1",
            name=f"{spec.name}-generated-search",
            base_skill_path=base.parent,
            manual_skill_path=manual.parent,
            validation_search_path=spec.validation_search_path,
            mutations=tuple(
                MutationSpec(
                    id=item.id,
                    hypothesis=item.hypothesis,
                    instruction=item.instruction,
                )
                for item in hypotheses
            ),
            search=spec.search.model_copy(
                update={
                    "promote_search_candidates": min(
                        spec.search.promote_search_candidates, len(hypotheses)
                    )
                }
            ),
            constraints=spec.constraints,
            budget=spec.budget,
            evaluator=spec.evaluator,
        )
        search = BenchmarkGuidedSkillSearch(self.workspace).run(
            search_spec, real_authorization=real_authorization
        )
        winner_path = BenchmarkGuidedSkillSearch(self.workspace).store.skill_path(search.winner)
        regression = (
            RegressionGateResult.model_validate_json(regression_path.read_bytes())
            if regression_path.exists()
            else self._regression_gate(
                spec, base, winner_path, real_authorization=real_authorization
            )
        )
        if not regression.passed:
            raise EvolutionError(
                "frozen validation winner failed regression_dev or token-overhead gates"
            )
        handoff = EvolutionHandoff(
            evolution_id=evolution_id,
            optimization_job_id=search.job.id,
            base_skill_sha256=base_sha,
            winner_candidate_id=search.winner.id,
            winner_skill_sha256=search.winner.content_sha256,
            winner_skill_path=str(winner_path.resolve(strict=True)),
            status="AWAITING_INDEPENDENT_FINAL_EVALUATION",
        )
        context_path = output / "optimization-context.json"
        handoff_path = output / "final-evaluation-handoff.json"
        self._write(context_path, context.model_dump(mode="json"))
        self._write(handoff_path, handoff.model_dump(mode="json"))
        self._write(regression_path, regression.model_dump(mode="json"))
        report = EvolutionReport(
            evolution_id=evolution_id,
            name=spec.name,
            generator_identity=generator_identity,
            context_sha256=stable_sha256(context.model_dump(mode="json")),
            hypotheses=hypotheses,
            optimization_job_id=search.job.id,
            candidate_count=len(search.candidates),
            winner_candidate_id=search.winner.id,
            winner_skill_sha256=search.winner.content_sha256,
            regression_gate=regression,
            generator_evidence=artifact.invocation_evidence,
            simulated=search.job.simulated,
            claim_limit=(
                "Validation-search engineering evidence only. The frozen candidate is not a "
                "published Skill v2 and has not accessed validation_confirm or locked_test."
            ),
            artifacts={
                "optimization_context": str(context_path),
                "hypotheses": str(hypotheses_path),
                "search_report": str(search.report_json),
                "regression_gate": str(regression_path),
                "final_handoff": str(handoff_path),
            },
        )
        report_json = output / "evolution-report.json"
        report_html = output / "evolution-report.html"
        self._write(report_json, report.model_dump(mode="json"))
        self._write_bytes(report_html, self._html(report).encode("utf-8"))
        return FailureGuidedEvolutionResult(
            report=report,
            search=search,
            report_json=report_json,
            report_html=report_html,
            handoff_path=handoff_path,
        )

    def load(self, evolution_id: UUID) -> EvolutionReport:
        path = self.workspace / "evolution-jobs" / str(evolution_id) / "evolution-report.json"
        return EvolutionReport.model_validate_json(path.read_bytes())

    def _regression_gate(
        self,
        spec: FailureGuidedEvolutionSpec,
        base_skill: Path,
        winner_skill: Path,
        *,
        real_authorization: Optional[RealEvaluationAuthorization],
    ) -> RegressionGateResult:
        if spec.evaluator.type == "real_agent":
            loaded = DatasetLoader().load(spec.regression_dev_path)
            if any(item.metadata.split != DatasetSplit.REGRESSION_DEV for item in loaded.cases):
                raise EvolutionError("real regression DatasetVersion must be regression_dev")
            dataset_cases = tuple(SearchCase(id=item.metadata.case_id) for item in loaded.cases)
            surrogate = ValidationSearchDataset(
                schema_version="ase/optimizer-validation/v1alpha1",
                name=loaded.manifest.name,
                version=loaded.manifest.version,
                split="validation_search",
                simulated=False,
                cases=dataset_cases,
            )
            dataset_file = spec.regression_dev_path / "dataset.yaml"
            dataset_sha = loaded.dataset_sha256
        else:
            dataset = RegressionDevDataset.load(spec.regression_dev_path)
            if dataset.simulated != spec.evaluator.simulated:
                raise EvolutionError("regression_dev and evaluator simulated flags must match")
            dataset_cases = dataset.cases
            surrogate = ValidationSearchDataset(
                schema_version="ase/optimizer-validation/v1alpha1",
                name=dataset.name,
                version=dataset.version,
                split="validation_search",
                simulated=dataset.simulated,
                cases=dataset_cases,
            )
            dataset_file = spec.regression_dev_path
            dataset_sha = hashlib.sha256(dataset_file.read_bytes()).hexdigest()
        evaluator = build_evaluator(
            spec.evaluator,
            surrogate,
            workspace=self.workspace,
            real_authorization=real_authorization,
        )
        base = evaluator.evaluate(
            base_skill,
            dataset_file,
            dataset_sha,
            dataset_cases,
            SearchEvaluationStage.REGRESSION_DEV,
            spec.budget.timeout_seconds,
        )
        winner = evaluator.evaluate(
            winner_skill,
            dataset_file,
            dataset_sha,
            dataset_cases,
            SearchEvaluationStage.REGRESSION_DEV,
            spec.budget.timeout_seconds,
        )
        base_by_case = {item.case_id: item for item in base.results}
        losses = tuple(
            item.case_id
            for item in winner.results
            if base_by_case[item.case_id].passed and not item.passed
        )
        invalid_cases = tuple(
            dict.fromkeys(
                item.case_id
                for evaluation in (base, winner)
                for item in evaluation.results
                if item.outcome == "invalid"
            )
        )
        overhead = (winner.total_tokens - base.total_tokens) / max(1, base.total_tokens)
        return RegressionGateResult(
            dataset_sha256=dataset_sha,
            base=base,
            winner=winner,
            loss_cases=losses,
            invalid_cases=invalid_cases,
            token_overhead_ratio=overhead,
            max_loss_cases=spec.constraints.max_loss_cases,
            max_token_overhead_ratio=spec.constraints.max_token_overhead_ratio,
            passed=(
                not invalid_cases
                and len(losses) <= spec.constraints.max_loss_cases
                and overhead <= spec.constraints.max_token_overhead_ratio
            ),
        )

    @staticmethod
    def _skill_file(path: Path) -> Path:
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise EvolutionError("symbolic-link Skill inputs are not allowed")
        resolved = expanded.resolve(strict=True)
        candidate = resolved / "SKILL.md" if resolved.is_dir() else resolved
        if candidate.is_symlink() or not candidate.is_file() or candidate.name != "SKILL.md":
            raise EvolutionError("Skill input must be a regular SKILL.md or its directory")
        return candidate

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _write(self, path: Path, payload: object) -> None:
        self._write_bytes(path, canonical_json(payload) + b"\n")

    def _write_bytes(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise EvolutionError(f"immutable evolution artifact changed: {path}")
            return
        self.writer.write(path, content)

    @staticmethod
    def _html(report: EvolutionReport) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        rows = "".join(
            "<tr><td>"
            + esc(item.id)
            + "</td><td>"
            + esc(item.failure_label.value)
            + "</td><td>"
            + esc(item.instruction)
            + "</td></tr>"
            for item in report.hypotheses
        )
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{esc(report.name)}</title><style>body{{font-family:system-ui;max-width:1000px;margin:auto}}
td,th{{border:1px solid #aaa;padding:6px}}table{{border-collapse:collapse}}</style></head><body>
<h1>Failure-Guided Skill Evolution</h1>
<p><strong>Claim limit:</strong> {esc(report.claim_limit)}</p>
<p>Generator: <code>{esc(report.generator_identity)}</code> · audited Process evidence:
<strong>{esc(report.generator_evidence is not None)}</strong></p>
<p>Evolution: <code>{esc(report.evolution_id)}</code></p><p>Winner: <code>
{esc(report.winner_candidate_id)}</code> · <code>{esc(report.winner_skill_sha256)}</code></p>
<p>Regression gate: <strong>{esc(report.regression_gate.passed)}</strong> · losses:
{esc(len(report.regression_gate.loss_cases))}/{esc(report.regression_gate.max_loss_cases)} ·
invalid: {esc(len(report.regression_gate.invalid_cases))} ·
token overhead: {esc(f"{report.regression_gate.token_overhead_ratio:.3f}")} / maximum
{esc(f"{report.regression_gate.max_token_overhead_ratio:.3f}")}</p>
<table><thead><tr><th>Hypothesis</th><th>Failure</th><th>Instruction</th></tr></thead>
<tbody>{rows}</tbody></table><footer>No external scripts or resources.</footer></body></html>"""
