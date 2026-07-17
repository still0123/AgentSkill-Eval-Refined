"""No-cost preflight for direct Skill v1 versus Candidate v2 evaluation."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

import yaml
from pydantic import Field

from agentskill_eval_benchmark_gen import DatasetLoader
from agentskill_eval_contracts import (
    CandidateEvaluation,
    FrozenModel,
    SearchCaseResult,
    SearchEvaluationStage,
    canonical_json,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_real_evidence import BaselineReplay, RealAgentEvidenceSpec
from agentskill_eval_skill_optimizer.candidate_quality import (
    CandidateQualityError,
    CandidateQualityGate,
    CandidateQualityReport,
)
from agentskill_eval_skill_optimizer.evolution import FailureEvidenceBundle
from agentskill_eval_skill_optimizer.proposal import RealLLMProposalService
from agentskill_eval_skill_optimizer.real_evaluator import (
    RealAgentCandidateEvaluator,
    RealCandidateEvaluationError,
    RealEvaluationAuthorization,
)
from agentskill_eval_skill_optimizer.spec import SearchCase


class OptimizationV2Error(RuntimeError):
    """Raised when the direct Skill comparison plan is not reproducible."""


class OptimizationV2Spec(FrozenModel):
    schema_version: Literal["ase/optimization-evaluation-v2/v1alpha1"]
    name: str = Field(min_length=1)
    base_skill_path: Path
    proposal_directory: Path
    failure_bundle_path: Path
    real_agent_config_path: Path
    validation_search_path: Path
    case_ids: Tuple[str, str]
    target_provider: str = "deepseek"
    target_model: str = Field(min_length=1)
    max_candidates: int = Field(default=3, ge=1, le=3)
    max_agent_runs: int = Field(default=12, ge=4, le=12)

    @classmethod
    def load(cls, path: Path) -> "OptimizationV2Spec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = cls.model_validate(payload)
            root = path.resolve(strict=True).parent

            def resolve(value: Path) -> Path:
                candidate = value if value.is_absolute() else root / value
                if candidate.is_symlink():
                    raise OptimizationV2Error("symbolic-link plan inputs are not allowed")
                return candidate.resolve(strict=True)

            return spec.model_copy(
                update={
                    "base_skill_path": resolve(spec.base_skill_path),
                    "proposal_directory": resolve(spec.proposal_directory),
                    "failure_bundle_path": resolve(spec.failure_bundle_path),
                    "real_agent_config_path": resolve(spec.real_agent_config_path),
                    "validation_search_path": resolve(spec.validation_search_path),
                }
            )
        except OptimizationV2Error:
            raise
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise OptimizationV2Error(f"invalid Optimization Evaluation v2 spec: {path}") from exc


class OptimizationV2Preflight(FrozenModel):
    schema_version: Literal["ase/optimization-evaluation-v2-preflight/v1alpha1"] = (
        "ase/optimization-evaluation-v2-preflight/v1alpha1"
    )
    name: str
    status: Literal["READY", "INSUFFICIENT"]
    reasons: Tuple[str, ...] = ()
    proposal_job_id: str
    proposal_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_candidate_ids: Tuple[str, ...] = ()
    rejected_candidate_ids: Tuple[str, ...] = ()
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_ids: Tuple[str, ...]
    provider: str
    model: str
    evidence_provider: Optional[str] = None
    evidence_model: Optional[str] = None
    planned_agent_runs: int = Field(ge=0, le=12)
    expected_new_agent_runs: int = Field(ge=0, le=12)
    estimated_cost_microusd: int = Field(ge=0)
    estimated_new_cost_microusd: int = Field(ge=0)
    simulated: Literal[False] = False
    search_executed: Literal[False] = False
    regression_executed: Literal[False] = False
    confirmation_executed: Literal[False] = False
    locked_test_accessed: Literal[False] = False
    claim_limit: str = (
        "No-cost preflight only; no Agent quality, Skill improvement, or Skill v2 claim."
    )


class OptimizationV2PreflightResult(FrozenModel):
    report: OptimizationV2Preflight
    candidate_quality: CandidateQualityReport
    report_path: Path
    html_path: Path


class OptimizationV2CandidateResult(FrozenModel):
    candidate_id: str
    skill_sha256: str
    baseline_results: Tuple[SearchCaseResult, ...]
    evaluation: CandidateEvaluation
    baseline_pass_rate: float
    candidate_pass_rate: float
    absolute_gain: float
    wtl: Dict[str, int]
    invalid_runs: int
    token_summary: Dict[str, int]
    latency_summary: Dict[str, int]
    cost_summary: Dict[str, Optional[int]]
    newly_consumed_agent_runs: int
    newly_observed_cost_microusd: int


class OptimizationV2ScreeningReport(FrozenModel):
    schema_version: Literal["ase/optimization-evaluation-v2-screening/v1alpha1"] = (
        "ase/optimization-evaluation-v2-screening/v1alpha1"
    )
    name: str
    status: Literal["COMPLETED", "FAILED_FAST"]
    proposal_job_id: str
    parent_skill_sha256: str
    provider: str
    model: str
    case_ids: Tuple[str, ...]
    candidates: Tuple[OptimizationV2CandidateResult, ...]
    planned_agent_runs: int
    expected_new_agent_runs: int
    observed_agent_runs: int
    observed_cost_microusd: int
    baseline_reused_runs: int
    simulated: Literal[False] = False
    search_executed: Literal[True] = True
    regression_executed: Literal[False] = False
    confirmation_executed: Literal[False] = False
    locked_test_accessed: Literal[False] = False
    claim_limit: str = (
        "Validation-search evidence only. No regression, confirmation, locked-test, "
        "generalization, or Skill v2 release claim."
    )


class OptimizationV2Planner:
    """Prepare a bounded, model-aligned direct-comparison experiment."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def preflight(self, spec: OptimizationV2Spec) -> OptimizationV2PreflightResult:
        try:
            proposal = RealLLMProposalService(spec.proposal_directory.parent.parent).verify(
                spec.proposal_directory
            )
            quality_root = self.workspace / "candidate-quality"
            quality = CandidateQualityGate(quality_root).prepare_from_proposal(
                spec.proposal_directory,
                base_skill_path=spec.base_skill_path,
                case_tokens=spec.case_ids,
                max_candidates=spec.max_candidates,
            )
            agent_spec = RealAgentEvidenceSpec.load(spec.real_agent_config_path)
            dataset = DatasetLoader().load(spec.validation_search_path)
            bundle = FailureEvidenceBundle.load(spec.failure_bundle_path)
        except (CandidateQualityError, OptimizationV2Error, ValueError, OSError) as exc:
            raise OptimizationV2Error(str(exc)) from exc

        reasons = []
        base_sha = self._skill_sha(spec.base_skill_path)
        if base_sha != proposal.manifest.base_skill_sha256:
            reasons.append("base Skill hash does not match Proposal v5")
        configured_sha = self._skill_sha(agent_spec.skill_path)
        if configured_sha != base_sha:
            reasons.append("real Agent config is not bound to Skill v1")
        if agent_spec.agent.provider != spec.target_provider:
            reasons.append("real Agent provider does not match target provider")
        if agent_spec.agent.model != spec.target_model:
            reasons.append("real Agent model does not match target model")
        if tuple(agent_spec.case_ids) != tuple(spec.case_ids):
            reasons.append("real Agent config Case IDs do not match Optimization v2 plan")
        dataset_case_ids = {item.metadata.case_id for item in dataset.cases}
        if set(spec.case_ids) != dataset_case_ids.intersection(spec.case_ids):
            reasons.append("validation dataset does not contain exactly the planned Cases")
        if proposal.manifest.input_evidence_class != "observed_train":
            reasons.append("Proposal input is not observed train evidence")
        if bundle.agent_provider != spec.target_provider:
            reasons.append("train failure evidence provider is missing or mismatched")
        if bundle.agent_model != spec.target_model:
            reasons.append("train failure evidence model is missing or mismatched")
        if not quality.accepted_candidate_ids:
            reasons.append("offline quality gate produced no accepted candidate")
        planned_runs = len(quality.accepted_candidate_ids) * len(spec.case_ids) * 2
        estimated_cost = planned_runs * agent_spec.pricing.estimated_cost_per_run_microusd
        expected_new_runs = (
            0
            if not quality.accepted_candidate_ids
            else len(spec.case_ids) * 2
            + max(0, len(quality.accepted_candidate_ids) - 1) * len(spec.case_ids)
        )
        estimated_new_cost = expected_new_runs * agent_spec.pricing.estimated_cost_per_run_microusd
        if planned_runs > spec.max_agent_runs:
            reasons.append("planned Agent Runs exceed the 12-Run Optimization v2 cap")

        report = OptimizationV2Preflight(
            name=spec.name,
            status="READY" if not reasons else "INSUFFICIENT",
            reasons=tuple(reasons),
            proposal_job_id=str(proposal.manifest.proposal_job_id),
            proposal_manifest_sha256=self._sha(
                (spec.proposal_directory / "proposal-manifest.json").read_bytes()
            ),
            parent_skill_sha256=base_sha,
            candidate_quality_report_sha256=self._sha(
                (quality_root / "candidate-quality-report.json").read_bytes()
            ),
            accepted_candidate_ids=quality.accepted_candidate_ids,
            rejected_candidate_ids=quality.rejected_candidate_ids,
            dataset_sha256=dataset.dataset_sha256,
            case_ids=spec.case_ids,
            provider=agent_spec.agent.provider,
            model=agent_spec.agent.model,
            evidence_provider=bundle.agent_provider,
            evidence_model=bundle.agent_model,
            planned_agent_runs=planned_runs,
            expected_new_agent_runs=expected_new_runs,
            estimated_cost_microusd=estimated_cost,
            estimated_new_cost_microusd=estimated_new_cost,
        )
        report_path = self.workspace / "optimization-v2-preflight.json"
        html_path = self.workspace / "optimization-v2-preflight.html"
        self.writer.write(report_path, canonical_json(report.model_dump(mode="json")) + b"\n")
        self.writer.write(html_path, self._html(report, quality).encode("utf-8"))
        return OptimizationV2PreflightResult(
            report=report,
            candidate_quality=quality,
            report_path=report_path,
            html_path=html_path,
        )

    def verify(self, report_path: Path) -> OptimizationV2Preflight:
        report = OptimizationV2Preflight.model_validate_json(report_path.read_bytes())
        quality_path = self.workspace / "candidate-quality" / "candidate-quality-report.json"
        if self._sha(quality_path.read_bytes()) != report.candidate_quality_report_sha256:
            raise OptimizationV2Error("candidate quality report hash mismatch")
        quality = CandidateQualityGate(self.workspace / "candidate-quality").verify(quality_path)
        if quality.accepted_candidate_ids != report.accepted_candidate_ids:
            raise OptimizationV2Error("preflight accepted candidate list mismatch")
        return report

    @staticmethod
    def _skill_sha(path: Path) -> str:
        root = path.resolve(strict=True)
        file = root / "SKILL.md" if root.is_dir() else root
        if not file.is_file() or file.name != "SKILL.md":
            raise OptimizationV2Error("Skill must be a regular SKILL.md")
        return hashlib.sha256(file.read_bytes()).hexdigest()

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _html(report: OptimizationV2Preflight, quality: CandidateQualityReport) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        rows = "".join(
            "<tr>"
            f"<td>{esc(item.candidate_id)}</td><td>{str(item.accepted).lower()}</td>"
            f"<td>{esc(item.skill_sha256)}</td>"
            f"<td>{esc(', '.join(item.rejection_reasons) or 'none')}</td>"
            "</tr>"
            for item in quality.candidates
        )
        reasons = "<br>".join(esc(item) for item in report.reasons) or "none"
        return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{esc(report.name)} — Optimization v2 preflight</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:auto;padding:24px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #aaa;padding:6px;text-align:left}}
.warning{{padding:10px;background:#fff3cd}}
</style></head>
<body><h1>{esc(report.name)}</h1>
<p class="warning"><b>Status:</b> {esc(report.status)}<br><b>Reasons:</b> {reasons}</p>
<dl><dt>Provider/model</dt><dd>{esc(report.provider)} / {esc(report.model)}</dd>
<dt>Evidence provider/model</dt><dd>{esc(report.evidence_provider or 'unavailable')} /
{esc(report.evidence_model or 'unavailable')}</dd>
<dt>Planned new Agent Runs</dt><dd>{report.planned_agent_runs}</dd>
<dt>Expected new Agent Runs with v1 reuse</dt><dd>{report.expected_new_agent_runs}</dd>
<dt>Estimated cost (microusd)</dt><dd>{report.estimated_cost_microusd}</dd></dl>
<table><thead><tr><th>Candidate</th><th>Accepted</th>
<th>Skill SHA-256</th><th>Reasons</th></tr></thead>
<tbody>{rows}</tbody></table>
<p>{esc(report.claim_limit)}</p></body></html>"""


class OptimizationV2ScreeningRunner:
    """Run bounded direct v1-to-candidate validation with a reusable v1 baseline."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def run(
        self,
        spec: OptimizationV2Spec,
        *,
        confirm_real_run: bool,
        max_cost_microusd: int,
        max_agent_runs: int,
    ) -> Tuple[OptimizationV2ScreeningReport, Path, Path]:
        preflight = OptimizationV2Planner(self.workspace / "preflight").preflight(spec)
        if preflight.report.status != "READY":
            raise OptimizationV2Error(
                "Optimization v2 preflight is insufficient: "
                + "; ".join(preflight.report.reasons)
            )
        if max_agent_runs < 4 or max_agent_runs > 12:
            raise OptimizationV2Error("Optimization v2 requires a 4..12 Agent Run limit")
        if max_cost_microusd < 1:
            raise OptimizationV2Error("positive max cost is required")

        agent_spec = RealAgentEvidenceSpec.load(spec.real_agent_config_path)
        dataset_file = spec.validation_search_path / "dataset.yaml"
        quality_root = self.workspace / "preflight" / "candidate-quality"
        quality = CandidateQualityGate(quality_root).verify(
            quality_root / "candidate-quality-report.json"
        )
        cases = tuple(SearchCase(id=item) for item in spec.case_ids)
        shared_baseline_cache: Dict[str, BaselineReplay] = {}
        baseline_results: Dict[str, SearchCaseResult] = {}
        authorization = RealEvaluationAuthorization(
            confirm_real_run=confirm_real_run,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
        )
        candidate_results = []
        status: Literal["COMPLETED", "FAILED_FAST"] = "COMPLETED"
        prior_runs = 0
        prior_cost = 0
        for candidate_id in quality.accepted_candidate_ids:
            candidate = next(
                item for item in quality.candidates if item.candidate_id == candidate_id
            )
            candidate_path = (quality_root / candidate.skill_path).resolve(strict=True).parent
            config_path = self._candidate_config(agent_spec, candidate_path, candidate_id)
            try:
                evaluator = RealAgentCandidateEvaluator(
                    config_path,
                    self.workspace / "runtime",
                    authorization,
                    baseline_skill_path=spec.base_skill_path,
                    baseline_replay_cache=shared_baseline_cache,
                )
                evaluation = evaluator.evaluate(
                    candidate_path / "SKILL.md",
                    dataset_file,
                    preflight.report.dataset_sha256,
                    cases,
                    SearchEvaluationStage.FULL,
                    agent_spec.agent.timeout_seconds,
                )
            except RealCandidateEvaluationError as exc:
                raise OptimizationV2Error(str(exc)) from exc
            if not baseline_results:
                baseline_results.update(evaluator.baseline_results)
            baseline_tuple = tuple(baseline_results[item] for item in spec.case_ids)
            baseline_pass_rate = sum(item.passed for item in baseline_tuple) / len(
                baseline_tuple
            )
            wtl = {"win": 0, "tie": 0, "loss": 0}
            for baseline, treatment in zip(baseline_tuple, evaluation.results):
                if treatment.passed and not baseline.passed:
                    wtl["win"] += 1
                elif treatment.passed == baseline.passed:
                    wtl["tie"] += 1
                else:
                    wtl["loss"] += 1
            baseline_costs = [item.cost_microusd for item in baseline_tuple]
            treatment_costs = [item.cost_microusd for item in evaluation.results]
            candidate_results.append(
                OptimizationV2CandidateResult(
                    candidate_id=candidate_id,
                    skill_sha256=candidate.skill_sha256,
                    baseline_results=baseline_tuple,
                    evaluation=evaluation,
                    baseline_pass_rate=baseline_pass_rate,
                    candidate_pass_rate=evaluation.pass_rate,
                    absolute_gain=evaluation.pass_rate - baseline_pass_rate,
                    wtl=wtl,
                    invalid_runs=sum(
                        item.outcome == "invalid"
                        for item in baseline_tuple + evaluation.results
                    ),
                    token_summary={
                        "baseline": sum(
                            item.input_tokens + item.output_tokens for item in baseline_tuple
                        ),
                        "candidate": evaluation.total_tokens,
                    },
                    latency_summary={
                        "baseline": sum(item.latency_ms for item in baseline_tuple),
                        "candidate": evaluation.total_latency_ms,
                    },
                    cost_summary={
                        "baseline": (
                            None
                            if any(item is None for item in baseline_costs)
                            else sum(item for item in baseline_costs if item is not None)
                        ),
                        "candidate": (
                            None
                            if any(item is None for item in treatment_costs)
                            else sum(item for item in treatment_costs if item is not None)
                        ),
                    },
                    newly_consumed_agent_runs=authorization.consumed_agent_runs - prior_runs,
                    newly_observed_cost_microusd=(
                        authorization.consumed_cost_microusd - prior_cost
                    ),
                )
            )
            prior_runs = authorization.consumed_agent_runs
            prior_cost = authorization.consumed_cost_microusd
            baseline_invalid = any(
                item.outcome == "invalid" for item in baseline_results.values()
            )
            candidate_invalid = any(item.outcome == "invalid" for item in evaluation.results)
            if baseline_invalid or candidate_invalid:
                status = "FAILED_FAST"
                break

        report = OptimizationV2ScreeningReport(
            name=spec.name,
            status=status,
            proposal_job_id=preflight.report.proposal_job_id,
            parent_skill_sha256=preflight.report.parent_skill_sha256,
            provider=preflight.report.provider,
            model=preflight.report.model,
            case_ids=spec.case_ids,
            candidates=tuple(candidate_results),
            planned_agent_runs=preflight.report.planned_agent_runs,
            expected_new_agent_runs=preflight.report.expected_new_agent_runs,
            observed_agent_runs=authorization.consumed_agent_runs,
            observed_cost_microusd=authorization.consumed_cost_microusd,
            baseline_reused_runs=max(0, authorization.consumed_agent_runs - 4),
        )
        report_path = self.workspace / "optimization-v2-screening.json"
        html_path = self.workspace / "optimization-v2-screening.html"
        self.writer.write(report_path, canonical_json(report.model_dump(mode="json")) + b"\n")
        self.writer.write(html_path, self._html(report).encode("utf-8"))
        return report, report_path, html_path

    @staticmethod
    def _candidate_config(
        agent_spec: RealAgentEvidenceSpec, candidate_path: Path, candidate_id: str
    ) -> Path:
        root = candidate_path.parents[1].parent / "generated-configs"
        root.mkdir(parents=True, exist_ok=True)
        config_path = root / f"{candidate_id}.yaml"
        payload = agent_spec.model_copy(update={"skill_path": candidate_path}).model_dump(
            mode="json"
        )
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return config_path

    @staticmethod
    def _html(report: OptimizationV2ScreeningReport) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        rows = []
        for candidate in report.candidates:
            traces = ", ".join(
                item.trace_ref or "unavailable" for item in candidate.evaluation.results
            )
            tokens = candidate.token_summary
            latency = candidate.latency_summary
            rows.append(
                "<tr>"
                f"<td>{esc(candidate.candidate_id)}</td>"
                f"<td>{candidate.candidate_pass_rate:.3f}</td>"
                f"<td>{candidate.baseline_pass_rate:.3f}</td>"
                f"<td>{candidate.absolute_gain:+.3f}</td>"
                f"<td>{esc(candidate.wtl)}</td>"
                f"<td>{candidate.invalid_runs}</td>"
                f"<td>{tokens['baseline']} / {tokens['candidate']}</td>"
                f"<td>{latency['baseline']} / {latency['candidate']}</td>"
                f"<td>{esc(candidate.cost_summary)}</td>"
                f"<td>{candidate.newly_consumed_agent_runs}</td>"
                f"<td>{esc(traces)}</td>"
                "</tr>"
            )
        return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src 'unsafe-inline'">
<title>{esc(report.name)} — Optimization v2 screening</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1200px;margin:auto;padding:24px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #aaa;padding:6px;text-align:left}}
.notice{{background:#fff3cd;padding:10px}}
</style></head>
<body><h1>{esc(report.name)}</h1>
<p class="notice"><b>Status:</b> {esc(report.status)}<br>{esc(report.claim_limit)}</p>
<dl>
<dt>Provider/model</dt><dd>{esc(report.provider)} / {esc(report.model)}</dd>
<dt>Planned Agent Runs</dt><dd>{report.planned_agent_runs}</dd>
<dt>Expected new Agent Runs</dt><dd>{report.expected_new_agent_runs}</dd>
<dt>Observed Agent Runs</dt><dd>{report.observed_agent_runs}</dd>
<dt>Observed cost (microusd)</dt><dd>{report.observed_cost_microusd}</dd>
<dt>Reused baseline Runs</dt><dd>{report.baseline_reused_runs}</dd>
</dl>
<table><thead><tr><th>Candidate</th><th>Candidate pass rate</th>
<th>v1 pass rate</th><th>Gain</th><th>W/T/L</th><th>Invalid</th>
<th>Tokens v1/candidate</th><th>Latency v1/candidate (ms)</th>
<th>Cost v1/candidate</th><th>New Runs</th><th>Trace refs</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
