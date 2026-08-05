"""Proposal-only real LLM Skill improvement jobs with immutable audit evidence."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import Dict, Literal, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator

from agentskill_eval_contracts import FrozenModel, canonical_json, stable_sha256
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_skill_optimizer.deepseek_generator import (
    DeepSeekGeneratorAuthorization,
    DeepSeekGeneratorError,
    DeepSeekGeneratorInvocationEvidence,
    DeepSeekHypothesisGenerator,
)
from agentskill_eval_skill_optimizer.evolution import (
    EvolutionError,
    FailureEvidenceBundle,
    HypothesisArtifact,
    ImprovementHypothesis,
    OptimizationContext,
    build_hypothesis_request,
    classify_failure_bundle,
    proposal_evidence_refs,
)
from agentskill_eval_skill_optimizer.process_generator import HypothesisGeneratorSpec


class RealLLMProposalError(RuntimeError):
    """Raised when a proposal-only job violates its frozen input or artifact contract."""


class RealLLMProposalSpec(FrozenModel):
    schema_version: Literal["ase/real-llm-proposal-spec/v1alpha1"]
    name: str = Field(min_length=1)
    base_skill_path: Path
    failure_bundle_path: Path
    input_evidence_class: Literal["observed_train", "simulated_fixture"]
    generator: HypothesisGeneratorSpec
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def generator_must_be_real_and_proposal_only(self) -> "RealLLMProposalSpec":
        if self.generator.type != "deepseek":
            raise ValueError("real LLM proposal MVP requires the DeepSeek OpenAI-compatible API")
        if not 2 <= self.generator.max_hypotheses <= 5:
            raise ValueError("real LLM proposal MVP requires two to five candidates")
        return self

    @classmethod
    def load(cls, path: Path) -> "RealLLMProposalSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = cls.model_validate(payload)
            root = path.resolve(strict=True).parent

            def resolved(value: Path) -> Path:
                candidate = value if value.is_absolute() else root / value
                if candidate.is_symlink():
                    raise RealLLMProposalError("symbolic-link proposal inputs are not allowed")
                return candidate.resolve(strict=True)

            return spec.model_copy(
                update={
                    "base_skill_path": resolved(spec.base_skill_path),
                    "failure_bundle_path": resolved(spec.failure_bundle_path),
                }
            )
        except RealLLMProposalError:
            raise
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise RealLLMProposalError(f"invalid real LLM proposal spec {path}: {exc}") from exc


class ProposalGeneratorParameters(FrozenModel):
    base_url: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    input_cache_miss_microusd_per_million: int = Field(ge=0)
    input_cache_hit_microusd_per_million: int = Field(ge=0)
    output_microusd_per_million: int = Field(ge=0)


class RealLLMProposalPreflight(FrozenModel):
    schema_version: Literal["ase/real-llm-proposal-preflight/v1alpha1"] = (
        "ase/real-llm-proposal-preflight/v1alpha1"
    )
    proposal_job_id: UUID
    provider: Literal["deepseek"] = "deepseek"
    model: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    generator_identity: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligible_failure_count: int = Field(ge=1)
    excluded_failure_count: int = Field(ge=0)
    planned_calls: Literal[1] = 1
    candidate_count: int = Field(ge=2, le=5)
    estimated_max_cost_microusd: int = Field(ge=0)
    parameters: ProposalGeneratorParameters
    source_split: Literal["train"] = "train"
    request_contains_sanitized_train_evidence_only: Literal[True] = True
    search_will_execute: Literal[False] = False
    locked_test_will_execute: Literal[False] = False


class RealLLMProposalManifest(FrozenModel):
    schema_version: Literal["ase/real-llm-proposal-manifest/v1alpha1"] = (
        "ase/real-llm-proposal-manifest/v1alpha1"
    )
    proposal_job_id: UUID
    name: str = Field(min_length=1)
    provider: Literal["deepseek"] = "deepseek"
    model: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    generator_identity: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposals_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_count: int = Field(ge=2, le=5)
    input_evidence_class: Literal["observed_train", "simulated_fixture"]
    evidence_class: Literal["real_llm_skill_proposal"] = "real_llm_skill_proposal"
    parameters: ProposalGeneratorParameters
    invocation_evidence: DeepSeekGeneratorInvocationEvidence
    artifacts: Dict[str, str]
    claim_limit: str = Field(min_length=1)
    source_split: Literal["train"] = "train"
    real_run_confirmed: Literal[True] = True
    simulated: Literal[False] = False
    search_executed: Literal[False] = False
    locked_test_accessed: Literal[False] = False
    raw_failure_rationale_stored: Literal[False] = False
    hidden_reasoning_stored: Literal[False] = False
    secret_value_stored: Literal[False] = False

    @model_validator(mode="after")
    def evidence_must_match_manifest(self) -> "RealLLMProposalManifest":
        evidence = self.invocation_evidence
        if evidence.model != self.model or evidence.generator_version != self.generator_version:
            raise ValueError("proposal invocation identity mismatch")
        if evidence.prompt_sha256 != self.prompt_sha256:
            raise ValueError("proposal prompt hash mismatch")
        if evidence.output_schema_sha256 != self.output_schema_sha256:
            raise ValueError("proposal schema hash mismatch")
        if evidence.request_sha256 != self.request_sha256:
            raise ValueError("proposal request hash mismatch")
        if evidence.hypotheses_sha256 != self.proposals_sha256:
            raise ValueError("proposal artifact hash mismatch")
        if evidence.hypothesis_count != self.proposal_count:
            raise ValueError("proposal count mismatch")
        return self


class RealLLMProposalReport(FrozenModel):
    schema_version: Literal["ase/real-llm-proposal-report/v1alpha1"] = (
        "ase/real-llm-proposal-report/v1alpha1"
    )
    proposal_job_id: UUID
    name: str
    provider: str
    model: str
    base_skill_sha256: str
    failure_bundle_sha256: str
    input_evidence_class: str
    proposals: Tuple[ImprovementHypothesis, ...]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    claim_limit: str
    search_executed: Literal[False] = False
    locked_test_accessed: Literal[False] = False


class RealLLMProposalResult(FrozenModel):
    manifest: RealLLMProposalManifest
    directory: Path
    proposals_path: Path
    report_json: Path
    report_html: Path


class RealLLMProposalService:
    """Call one real LLM for proposals without constructing or evaluating a search job."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def preflight(self, spec: RealLLMProposalSpec) -> RealLLMProposalPreflight:
        base_skill = self._skill_file(spec.base_skill_path)
        bundle = FailureEvidenceBundle.load(spec.failure_bundle_path)
        base_sha = self._sha(base_skill.read_bytes())
        bundle_sha = self._sha(spec.failure_bundle_path.read_bytes())
        if spec.input_evidence_class == "observed_train":
            try:
                bundle.require_observed_provenance(
                    provider="deepseek",
                    model=str(spec.generator.model),
                    bundle_sha256=bundle_sha,
                )
            except EvolutionError as exc:
                raise RealLLMProposalError(str(exc)) from exc
        decisions = classify_failure_bundle(bundle)
        eligible = tuple(item for item in decisions if item.eligible)
        excluded = tuple(item for item in decisions if not item.eligible)
        if not eligible:
            raise RealLLMProposalError("train bundle contains no eligible Skill-change failures")
        context = OptimizationContext(
            source_split="train",
            base_skill_sha256=base_sha,
            failure_bundle_sha256=bundle_sha,
            eligible=eligible,
            excluded=excluded,
        )
        request = build_hypothesis_request(
            base_skill, context, spec.generator.max_hypotheses
        )
        generator = DeepSeekHypothesisGenerator(spec.generator, None)
        request_sha = generator.request_sha256(request)
        semantic_spec = spec.model_dump(
            mode="json", exclude={"base_skill_path", "failure_bundle_path"}
        )
        proposal_job_id = uuid5(
            NAMESPACE_URL,
            "agentskill-eval:real-llm-proposal:"
            + stable_sha256(
                {
                    "spec": semantic_spec,
                    "base_skill_sha256": base_sha,
                    "failure_bundle_sha256": bundle_sha,
                    "request_sha256": request_sha,
                }
            ),
        )
        return RealLLMProposalPreflight(
            proposal_job_id=proposal_job_id,
            model=str(spec.generator.model),
            generator_version=spec.generator.version,
            generator_identity=generator.identity,
            prompt_sha256=generator.prompt_sha256,
            output_schema_sha256=generator.output_schema_sha256,
            request_sha256=request_sha,
            base_skill_sha256=base_sha,
            failure_bundle_sha256=bundle_sha,
            eligible_failure_count=len(eligible),
            excluded_failure_count=len(excluded),
            candidate_count=spec.generator.max_hypotheses,
            estimated_max_cost_microusd=generator.estimate_call_cost_microusd(request),
            parameters=self._parameters(spec.generator),
        )

    def run(
        self,
        spec: RealLLMProposalSpec,
        authorization: DeepSeekGeneratorAuthorization,
    ) -> RealLLMProposalResult:
        preflight = self.preflight(spec)
        output = self.workspace / "proposal-jobs" / str(preflight.proposal_job_id)
        manifest_path = output / "proposal-manifest.json"
        if manifest_path.exists():
            return self.verify(output)

        base_skill = self._skill_file(spec.base_skill_path)
        bundle = FailureEvidenceBundle.load(spec.failure_bundle_path)
        decisions = classify_failure_bundle(bundle)
        context = OptimizationContext(
            source_split="train",
            base_skill_sha256=preflight.base_skill_sha256,
            failure_bundle_sha256=preflight.failure_bundle_sha256,
            eligible=tuple(item for item in decisions if item.eligible),
            excluded=tuple(item for item in decisions if not item.eligible),
        )
        request = build_hypothesis_request(
            base_skill, context, spec.generator.max_hypotheses
        )
        generator = DeepSeekHypothesisGenerator(spec.generator, authorization)
        try:
            generated = generator.generate(
                request,
                tuple(
                    sorted(
                        {item.label for item in context.eligible},
                        key=lambda item: item.value,
                    )
                ),
            )
        except DeepSeekGeneratorError as exc:
            raise RealLLMProposalError(str(exc)) from exc
        proposals = tuple(
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
        proposals_sha = stable_sha256([item.model_dump(mode="json") for item in proposals])
        evidence = generated.evidence.model_copy(
            update={"hypotheses_sha256": proposals_sha}
        )
        artifact = HypothesisArtifact(
            generator=preflight.generator_identity,
            hypotheses=proposals,
            invocation_evidence=evidence,
        )
        report = RealLLMProposalReport(
            proposal_job_id=preflight.proposal_job_id,
            name=spec.name,
            provider="deepseek",
            model=preflight.model,
            base_skill_sha256=preflight.base_skill_sha256,
            failure_bundle_sha256=preflight.failure_bundle_sha256,
            input_evidence_class=spec.input_evidence_class,
            proposals=proposals,
            input_tokens=evidence.input_tokens,
            output_tokens=evidence.output_tokens,
            cost_microusd=evidence.cost_microusd,
            duration_ms=evidence.duration_ms,
            claim_limit=spec.claim_limit,
        )
        proposals_bytes = canonical_json(artifact.model_dump(mode="json")) + b"\n"
        report_bytes = canonical_json(report.model_dump(mode="json")) + b"\n"
        html_bytes = self._html(report).encode("utf-8")
        artifacts = {
            "proposals.json": self._sha(proposals_bytes),
            "proposal-report.json": self._sha(report_bytes),
            "proposal-report.html": self._sha(html_bytes),
        }
        manifest = RealLLMProposalManifest(
            proposal_job_id=preflight.proposal_job_id,
            name=spec.name,
            model=preflight.model,
            generator_version=preflight.generator_version,
            generator_identity=preflight.generator_identity,
            prompt_sha256=preflight.prompt_sha256,
            output_schema_sha256=preflight.output_schema_sha256,
            request_sha256=preflight.request_sha256,
            base_skill_sha256=preflight.base_skill_sha256,
            failure_bundle_sha256=preflight.failure_bundle_sha256,
            proposals_sha256=proposals_sha,
            proposal_count=len(proposals),
            input_evidence_class=spec.input_evidence_class,
            parameters=preflight.parameters,
            invocation_evidence=evidence,
            artifacts=artifacts,
            claim_limit=spec.claim_limit,
        )
        output.mkdir(parents=True, exist_ok=True)
        self._write(output / "proposals.json", proposals_bytes)
        self._write(output / "proposal-report.json", report_bytes)
        self._write(output / "proposal-report.html", html_bytes)
        self._write(
            manifest_path,
            canonical_json(manifest.model_dump(mode="json")) + b"\n",
        )
        return RealLLMProposalResult(
            manifest=manifest,
            directory=output,
            proposals_path=output / "proposals.json",
            report_json=output / "proposal-report.json",
            report_html=output / "proposal-report.html",
        )

    def verify(self, directory: Path) -> RealLLMProposalResult:
        root = directory.resolve(strict=True)
        manifest_path = root / "proposal-manifest.json"
        try:
            manifest = RealLLMProposalManifest.model_validate_json(manifest_path.read_bytes())
            for name, expected in manifest.artifacts.items():
                path = root / name
                if not path.is_file() or self._sha(path.read_bytes()) != expected:
                    raise RealLLMProposalError(f"proposal artifact hash mismatch: {name}")
            artifact = HypothesisArtifact.model_validate_json(
                (root / "proposals.json").read_bytes()
            )
            report = RealLLMProposalReport.model_validate_json(
                (root / "proposal-report.json").read_bytes()
            )
        except RealLLMProposalError:
            raise
        except (OSError, ValueError) as exc:
            raise RealLLMProposalError(f"invalid proposal job {directory}: {exc}") from exc
        proposals_sha = stable_sha256(
            [item.model_dump(mode="json") for item in artifact.hypotheses]
        )
        if proposals_sha != manifest.proposals_sha256:
            raise RealLLMProposalError("proposal semantic hash mismatch")
        if report.proposals != artifact.hypotheses:
            raise RealLLMProposalError("proposal report and artifact disagree")
        if report.proposal_job_id != manifest.proposal_job_id:
            raise RealLLMProposalError("proposal report job ID mismatch")
        return RealLLMProposalResult(
            manifest=manifest,
            directory=root,
            proposals_path=root / "proposals.json",
            report_json=root / "proposal-report.json",
            report_html=root / "proposal-report.html",
        )

    @staticmethod
    def _skill_file(path: Path) -> Path:
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise RealLLMProposalError("symbolic-link Skill inputs are not allowed")
        resolved = expanded.resolve(strict=True)
        candidate = resolved / "SKILL.md" if resolved.is_dir() else resolved
        if candidate.is_symlink() or not candidate.is_file() or candidate.name != "SKILL.md":
            raise RealLLMProposalError("Skill input must be a regular SKILL.md or its directory")
        return candidate

    @staticmethod
    def _parameters(spec: HypothesisGeneratorSpec) -> ProposalGeneratorParameters:
        return ProposalGeneratorParameters(
            base_url=str(spec.base_url),
            temperature=spec.temperature,
            max_output_tokens=spec.max_output_tokens,
            timeout_seconds=spec.timeout_seconds,
            input_cache_miss_microusd_per_million=(
                spec.input_cache_miss_microusd_per_million
            ),
            input_cache_hit_microusd_per_million=(
                spec.input_cache_hit_microusd_per_million
            ),
            output_microusd_per_million=spec.output_microusd_per_million,
        )

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _write(self, path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise RealLLMProposalError(f"immutable proposal artifact changed: {path}")
            return
        self.writer.write(path, content)

    @staticmethod
    def _html(report: RealLLMProposalReport) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        rows = "".join(
            "<tr>"
            f"<td><code>{esc(item.id)}</code></td>"
            f"<td>{esc(item.failure_label.value)}</td>"
            f"<td>{esc(item.hypothesis)}</td>"
            f"<td>{esc(item.instruction)}</td>"
            f"<td>{esc('; '.join(item.risks))}</td>"
            "</tr>"
            for item in report.proposals
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Real LLM Skill Proposals</title>
<style>body{{font-family:system-ui;margin:2rem;max-width:1200px}}table{{border-collapse:collapse}}
th,td{{border:1px solid #ccc;padding:.5rem;vertical-align:top}}code{{word-break:break-all}}</style>
</head><body><h1>Real LLM Skill Proposal</h1>
<p>Provider/model: <strong>{esc(report.provider)} / {esc(report.model)}</strong></p>
<p>Base Skill SHA-256: <code>{esc(report.base_skill_sha256)}</code></p>
<p>Input evidence: {esc(report.input_evidence_class)} · search executed: false ·
locked accessed: false</p>
<table><thead><tr><th>ID</th><th>Failure</th><th>Reason</th><th>Instruction</th><th>Risks</th></tr></thead>
<tbody>{rows}</tbody></table><h2>Claim limit</h2><p>{esc(report.claim_limit)}</p></body></html>"""
