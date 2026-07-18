"""No-cost preflight for direct Skill v1 versus Candidate v2 evaluation."""

from __future__ import annotations

import hashlib
import html
import re
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Literal, Optional, Sequence, Tuple

import yaml
from pydantic import Field

from agentskill_eval_benchmark_gen import DatasetLoader
from agentskill_eval_contracts import (
    ArtifactManifest,
    CandidateEvaluation,
    FrozenModel,
    SearchCaseResult,
    SearchEvaluationStage,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment import ExperimentLayout, LocalExperimentStore
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes
from agentskill_eval_real_evidence import BaselineReplay, RealAgentEvidenceSpec
from agentskill_eval_runner_adapters import (
    ArtifactObservation,
    ExitReason,
    RunnerResult,
    RunnerStatus,
)
from agentskill_eval_skill_optimizer.candidate_quality import (
    CandidateQualityError,
    CandidateQualityGate,
    CandidateQualityReport,
)
from agentskill_eval_skill_optimizer.evolution import EvolutionError, FailureEvidenceBundle
from agentskill_eval_skill_optimizer.proposal import RealLLMProposalError, RealLLMProposalService
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
    max_candidates: int = Field(default=3, ge=1, le=5)
    max_agent_runs: int = Field(default=12, ge=4, le=20)

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
    planned_agent_runs: int = Field(ge=0, le=20)
    expected_new_agent_runs: int = Field(ge=0, le=20)
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
    status: Literal["PENDING", "COMPLETED", "INVALID", "PROVIDER_BLOCKED"]
    baseline_results: Tuple[SearchCaseResult, ...] = ()
    evaluation: Optional[CandidateEvaluation] = None
    baseline_pass_rate: Optional[float] = None
    candidate_pass_rate: Optional[float] = None
    absolute_gain: Optional[float] = None
    wtl: Dict[str, int] = Field(default_factory=dict)
    invalid_runs: int = Field(default=0, ge=0)
    token_summary: Dict[str, int] = Field(default_factory=dict)
    latency_summary: Dict[str, int] = Field(default_factory=dict)
    cost_summary: Dict[str, Optional[int]] = Field(default_factory=dict)
    newly_consumed_agent_runs: int = Field(default=0, ge=0)
    newly_observed_cost_microusd: int = Field(default=0, ge=0)
    reused_baseline_runs: int = Field(default=0, ge=0)
    error_types: Tuple[
        Literal[
            "task_failed",
            "agent_invalid",
            "insufficient_balance",
            "rate_limited",
            "provider_timeout",
            "budget_exhausted",
        ],
        ...,
    ] = ()
    error_history: Tuple[
        Literal[
            "task_failed",
            "agent_invalid",
            "insufficient_balance",
            "rate_limited",
            "provider_timeout",
            "budget_exhausted",
        ],
        ...,
    ] = ()
    run_keys: Tuple[str, ...] = ()


OptimizationV2ErrorType = Literal[
    "task_failed",
    "agent_invalid",
    "insufficient_balance",
    "rate_limited",
    "provider_timeout",
    "budget_exhausted",
]


class OptimizationV2InputSnapshot(FrozenModel):
    """Non-secret immutable inputs that must agree before a session is resumed."""

    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_skill_sha256: Dict[str, str]
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runner_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OptimizationV2LedgerEntry(FrozenModel):
    """One stable baseline or candidate case result, never a provider authorization."""

    run_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    arm: Literal["baseline", "candidate"]
    case_id: str = Field(min_length=1)
    candidate_id: Optional[str] = None
    status: Literal["PENDING", "COMPLETED", "INVALID", "PROVIDER_BLOCKED"]
    result: Optional[SearchCaseResult] = None
    error_type: Optional[OptimizationV2ErrorType] = None
    error_history: Tuple[OptimizationV2ErrorType, ...] = ()


class OptimizationV2CandidateState(FrozenModel):
    candidate_id: str = Field(min_length=1)
    skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["PENDING", "COMPLETED", "INVALID", "PROVIDER_BLOCKED"]
    evaluation: Optional[CandidateEvaluation] = None
    newly_consumed_agent_runs: int = Field(default=0, ge=0)
    newly_observed_cost_microusd: int = Field(default=0, ge=0)
    reused_baseline_runs: int = Field(default=0, ge=0)
    error_types: Tuple[OptimizationV2ErrorType, ...] = ()
    error_history: Tuple[OptimizationV2ErrorType, ...] = ()
    attempt_count: int = Field(default=0, ge=0)
    # A provider block that completed an auditable pair can be retried only by
    # the explicit `resume` command with a fresh authorization.  Partial pairs
    # are deliberately non-retryable: rerunning their immutable pair could
    # duplicate provider work.
    retryable_provider_error: bool = False


class OptimizationV2Session(FrozenModel):
    schema_version: Literal["ase/optimization-evaluation-v2-session/v1alpha1"] = (
        "ase/optimization-evaluation-v2-session/v1alpha1"
    )
    session_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1)
    status: Literal["READY", "PARTIAL", "COMPLETED", "BLOCKED", "BUDGET_EXHAUSTED"]
    inputs: OptimizationV2InputSnapshot
    proposal_job_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    case_ids: Tuple[str, ...] = Field(min_length=2)
    planned_agent_runs: int = Field(ge=0)
    expected_new_agent_runs: int = Field(ge=0)
    ledger: Tuple[OptimizationV2LedgerEntry, ...]
    candidates: Tuple[OptimizationV2CandidateState, ...]
    observed_agent_runs: int = Field(default=0, ge=0)
    observed_cost_microusd: int = Field(default=0, ge=0)
    last_error_type: Optional[OptimizationV2ErrorType] = None
    created_at: datetime
    updated_at: datetime
    claim_limit: str = (
        "Partial validation-search evidence only. No winner, Skill improvement, regression, "
        "confirmation, locked-test, generalization, or Skill v2 release claim."
    )


class OptimizationV2ScreeningReport(FrozenModel):
    schema_version: Literal["ase/optimization-evaluation-v2-screening/v1alpha1"] = (
        "ase/optimization-evaluation-v2-screening/v1alpha1"
    )
    name: str
    status: Literal["COMPLETED", "PARTIAL", "BLOCKED", "BUDGET_EXHAUSTED"]
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
    completed_candidate_ids: Tuple[str, ...] = ()
    invalid_candidate_ids: Tuple[str, ...] = ()
    provider_blocked_candidate_ids: Tuple[str, ...] = ()
    remaining_candidate_ids: Tuple[str, ...] = ()
    error_counts: Dict[str, int] = Field(default_factory=dict)
    session_path: str = Field(min_length=1)
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
        except (
            CandidateQualityError,
            OptimizationV2Error,
            RealLLMProposalError,
            ValueError,
            OSError,
        ) as exc:
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
        try:
            bundle.require_observed_provenance(
                provider=spec.target_provider,
                model=spec.target_model,
                proposal_bundle_sha256=proposal.manifest.failure_bundle_sha256,
                bundle_sha256=self._sha(spec.failure_bundle_path.read_bytes()),
            )
        except EvolutionError as exc:
            reasons.append(str(exc))
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
            reasons.append("planned Agent Runs exceed the 20-Run Optimization v2 cap")

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
    """Run and resume direct v1-to-candidate validation from a durable case ledger."""

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
        return self._execute(
            spec,
            confirm_real_run=confirm_real_run,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
            require_existing=False,
        )

    def resume(
        self,
        spec: OptimizationV2Spec,
        *,
        confirm_real_run: bool,
        max_cost_microusd: int,
        max_agent_runs: int,
    ) -> Tuple[OptimizationV2ScreeningReport, Path, Path]:
        """Continue only pending candidates under a newly supplied authorization."""
        return self._execute(
            spec,
            confirm_real_run=confirm_real_run,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
            require_existing=True,
        )

    def status(self) -> Tuple[OptimizationV2Session, OptimizationV2ScreeningReport, Path, Path]:
        """Read the durable session and regenerate its escaped partial report."""
        session = self._load_session()
        report, report_path, html_path = self._write_report(session)
        return session, report, report_path, html_path

    def _execute(
        self,
        spec: OptimizationV2Spec,
        *,
        confirm_real_run: bool,
        max_cost_microusd: int,
        max_agent_runs: int,
        require_existing: bool,
    ) -> Tuple[OptimizationV2ScreeningReport, Path, Path]:
        self._validate_authorization(confirm_real_run, max_cost_microusd, max_agent_runs)
        preflight, quality, agent_spec = self._prepared_inputs(spec)
        session = self._load_or_create_session(
            spec, preflight, quality, agent_spec, require_existing=require_existing
        )
        if session.status == "BLOCKED":
            if not require_existing:
                raise OptimizationV2Error(
                    "session is provider-blocked; use the explicit resume command with a new "
                    "authorization after resolving the provider issue"
                )
            session = self._rearm_retryable_provider_blocks(session)
            self._save_session(session)
        authorization = RealEvaluationAuthorization(
            confirm_real_run=confirm_real_run,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
        )
        baseline_cache = self._baseline_replay_cache(session, agent_spec)
        quality_root = self.workspace / "preflight" / "candidate-quality"
        candidates_by_id = {item.candidate_id: item for item in quality.candidates if item.accepted}
        cases = tuple(SearchCase(id=item) for item in spec.case_ids)

        for state in session.candidates:
            if state.status != "PENDING":
                continue
            candidate = candidates_by_id.get(state.candidate_id)
            if candidate is None:
                raise OptimizationV2Error(
                    "session candidate no longer exists in verified quality report"
                )
            candidate_path = (quality_root / candidate.skill_path).resolve(strict=True).parent
            config_path = self._candidate_config(
                agent_spec, candidate_path, state.candidate_id, state.attempt_count
            )
            before_runs = authorization.consumed_agent_runs
            before_cost = authorization.consumed_cost_microusd
            try:
                evaluator = RealAgentCandidateEvaluator(
                    config_path,
                    self.workspace / "runtime",
                    authorization,
                    baseline_skill_path=spec.base_skill_path,
                    baseline_replay_cache=baseline_cache,
                    attempt_no=state.attempt_count + 1,
                )
                evaluation = evaluator.evaluate(
                    candidate_path / "SKILL.md",
                    spec.validation_search_path / "dataset.yaml",
                    preflight.report.dataset_sha256,
                    cases,
                    SearchEvaluationStage.FULL,
                    agent_spec.agent.timeout_seconds,
                )
            except RealCandidateEvaluationError as exc:
                error_type = self._classify_error_text(str(exc))
                partial_baseline = exc.baseline_results
                partial_treatment = exc.treatment_results
                consumed_runs = authorization.consumed_agent_runs - before_runs
                consumed_cost = authorization.consumed_cost_microusd - before_cost
                if error_type == "budget_exhausted" and not exc.has_observed_work:
                    session = session.model_copy(
                        update={
                            "status": "BUDGET_EXHAUSTED",
                            "last_error_type": error_type,
                            "updated_at": self._now(),
                        }
                    )
                    self._save_session(session)
                    return self._write_report(session)
                error_types = tuple(
                    sorted(
                        {
                            error_type,
                            *self._evaluation_error_types(partial_baseline),
                            *self._evaluation_error_types(partial_treatment),
                        }
                    )
                )
                terminal_status: Literal["INVALID", "PROVIDER_BLOCKED"]
                terminal_status = (
                    "PROVIDER_BLOCKED"
                    if self._provider_blocked(error_type)
                    else "INVALID"
                )
                session = self._replace_candidate(
                    session,
                    state.model_copy(
                        update={
                            "status": terminal_status,
                            "newly_consumed_agent_runs": consumed_runs,
                            "newly_observed_cost_microusd": consumed_cost,
                            "reused_baseline_runs": exc.reused_baseline_runs,
                            "error_types": error_types,
                            "attempt_count": state.attempt_count + 1,
                            "retryable_provider_error": (
                                terminal_status == "PROVIDER_BLOCKED"
                                and not exc.has_observed_work
                            ),
                        }
                    ),
                    authorization,
                    before_runs,
                    before_cost,
                )
                session = self._record_evaluation_entries(
                    session,
                    state.candidate_id,
                    {item.case_id: item for item in partial_baseline},
                    partial_treatment,
                )
                session = self._replace_candidate_entries_without_results(
                    session, state.candidate_id, error_type
                )
                if error_type == "budget_exhausted":
                    session = session.model_copy(
                        update={
                            "status": "BUDGET_EXHAUSTED",
                            "last_error_type": error_type,
                            "updated_at": self._now(),
                        }
                    )
                else:
                    session = self._terminal_partial_status(session, error_type)
                self._save_session(session)
                return self._write_report(session)

            baseline_results = tuple(
                evaluator.baseline_results[case_id]
                for case_id in spec.case_ids
                if case_id in evaluator.baseline_results
            )
            error_types = tuple(
                sorted(
                    set(
                        self._evaluation_error_types(baseline_results)
                        + self._evaluation_error_types(evaluation.results)
                    )
                )
            )
            candidate_status: Literal["COMPLETED", "INVALID", "PROVIDER_BLOCKED"]
            if any(self._provider_blocked(item) for item in error_types):
                candidate_status = "PROVIDER_BLOCKED"
            elif any(item == "agent_invalid" for item in error_types):
                candidate_status = "INVALID"
            else:
                candidate_status = "COMPLETED"
            updated = OptimizationV2CandidateState(
                candidate_id=state.candidate_id,
                skill_sha256=state.skill_sha256,
                status=candidate_status,
                evaluation=evaluation,
                newly_consumed_agent_runs=authorization.consumed_agent_runs - before_runs,
                newly_observed_cost_microusd=(
                    authorization.consumed_cost_microusd - before_cost
                ),
                reused_baseline_runs=evaluator.reused_baseline_runs,
                error_types=error_types,
                error_history=state.error_history,
                attempt_count=state.attempt_count + 1,
                retryable_provider_error=(
                    candidate_status == "PROVIDER_BLOCKED"
                ),
            )
            session = self._replace_candidate(
                session, updated, authorization, before_runs, before_cost
            )
            session = self._record_evaluation_entries(
                session,
                state.candidate_id,
                evaluator.baseline_results,
                evaluation.results,
            )
            if candidate_status != "COMPLETED":
                session = self._terminal_partial_status(
                    session,
                    error_types[0] if error_types else "agent_invalid",
                )
                self._save_session(session)
                return self._write_report(session)
            self._save_session(session)

        session = self._completed_status(session)
        self._save_session(session)
        return self._write_report(session)

    def _prepared_inputs(
        self, spec: OptimizationV2Spec
    ) -> Tuple[OptimizationV2PreflightResult, CandidateQualityReport, RealAgentEvidenceSpec]:
        preflight = OptimizationV2Planner(self.workspace / "preflight").preflight(spec)
        if preflight.report.status != "READY":
            raise OptimizationV2Error(
                "Optimization v2 preflight is insufficient: "
                + "; ".join(preflight.report.reasons)
            )
        quality_root = self.workspace / "preflight" / "candidate-quality"
        quality = CandidateQualityGate(quality_root).verify(
            quality_root / "candidate-quality-report.json"
        )
        return preflight, quality, RealAgentEvidenceSpec.load(spec.real_agent_config_path)

    @staticmethod
    def _validate_authorization(
        confirm_real_run: bool, max_cost_microusd: int, max_agent_runs: int
    ) -> None:
        if not confirm_real_run:
            raise OptimizationV2Error("Optimization v2 real screening requires confirmation")
        if max_agent_runs < 1 or max_agent_runs > 20:
            raise OptimizationV2Error("Optimization v2 requires a 1..20 Agent Run limit")
        if max_cost_microusd < 1:
            raise OptimizationV2Error("positive max cost is required")

    def _load_or_create_session(
        self,
        spec: OptimizationV2Spec,
        preflight: OptimizationV2PreflightResult,
        quality: CandidateQualityReport,
        agent_spec: RealAgentEvidenceSpec,
        *,
        require_existing: bool,
    ) -> OptimizationV2Session:
        snapshot = self._input_snapshot(spec, preflight, quality, agent_spec)
        path = self._session_path()
        if path.exists():
            session = self._load_session()
            if session.inputs != snapshot:
                raise OptimizationV2Error(
                    "resume rejected: Proposal, Skill, Dataset, Agent configuration, "
                    "or Runner hash changed"
                )
            return session
        if require_existing:
            raise OptimizationV2Error("no Optimization v2 session exists in this workspace")
        now = self._now()
        session_id = stable_sha256(
            {
                "schema_version": "ase/optimization-evaluation-v2-session/v1alpha1",
                "name": spec.name,
                "inputs": snapshot.model_dump(mode="json"),
                "case_ids": spec.case_ids,
            }
        )
        ledger = []
        for case_id in spec.case_ids:
            ledger.append(
                OptimizationV2LedgerEntry(
                    run_key=self._run_key(session_id, "baseline", None, case_id),
                    arm="baseline",
                    case_id=case_id,
                    status="PENDING",
                )
            )
        candidate_states = []
        for candidate_id in quality.accepted_candidate_ids:
            candidate = next(
                item for item in quality.candidates if item.candidate_id == candidate_id
            )
            candidate_states.append(
                OptimizationV2CandidateState(
                    candidate_id=candidate_id,
                    skill_sha256=candidate.skill_sha256,
                    status="PENDING",
                )
            )
            for case_id in spec.case_ids:
                ledger.append(
                    OptimizationV2LedgerEntry(
                        run_key=self._run_key(session_id, "candidate", candidate_id, case_id),
                        arm="candidate",
                        candidate_id=candidate_id,
                        case_id=case_id,
                        status="PENDING",
                    )
                )
        session = OptimizationV2Session(
            session_id=session_id,
            name=spec.name,
            status="READY",
            inputs=snapshot,
            proposal_job_id=preflight.report.proposal_job_id,
            provider=preflight.report.provider,
            model=preflight.report.model,
            case_ids=spec.case_ids,
            planned_agent_runs=preflight.report.planned_agent_runs,
            expected_new_agent_runs=preflight.report.expected_new_agent_runs,
            ledger=tuple(ledger),
            candidates=tuple(candidate_states),
            created_at=now,
            updated_at=now,
        )
        self._save_session(session)
        return session

    def _input_snapshot(
        self,
        spec: OptimizationV2Spec,
        preflight: OptimizationV2PreflightResult,
        quality: CandidateQualityReport,
        agent_spec: RealAgentEvidenceSpec,
    ) -> OptimizationV2InputSnapshot:
        quality_root = self.workspace / "preflight" / "candidate-quality"
        candidate_hashes = {
            item.candidate_id: item.skill_sha256
            for item in quality.candidates
            if item.accepted
        }
        runner_path = agent_spec.runner.path.resolve(strict=True)
        return OptimizationV2InputSnapshot(
            spec_sha256=stable_sha256(spec.model_dump(mode="json")),
            proposal_manifest_sha256=preflight.report.proposal_manifest_sha256,
            failure_bundle_sha256=self._sha(spec.failure_bundle_path.read_bytes()),
            parent_skill_sha256=preflight.report.parent_skill_sha256,
            candidate_quality_report_sha256=self._sha(
                (quality_root / "candidate-quality-report.json").read_bytes()
            ),
            candidate_skill_sha256=candidate_hashes,
            dataset_sha256=preflight.report.dataset_sha256,
            agent_config_sha256=stable_sha256(agent_spec.model_dump(mode="json")),
            runner_sha256=self._sha(runner_path.read_bytes()),
        )

    def _record_evaluation_entries(
        self,
        session: OptimizationV2Session,
        candidate_id: str,
        observed_baseline: Dict[str, SearchCaseResult],
        treatment: Sequence[SearchCaseResult],
    ) -> OptimizationV2Session:
        treatment_by_case = {item.case_id: item for item in treatment}
        entries = []
        for entry in session.ledger:
            if entry.arm == "baseline" and entry.status == "PENDING":
                result = observed_baseline.get(entry.case_id)
                if result is not None:
                    error_type = self._result_error_type(result)
                    entries.append(
                        entry.model_copy(
                            update={
                                "status": self._entry_status(error_type),
                                "result": result,
                                "error_type": error_type,
                            }
                        )
                    )
                    continue
            if entry.candidate_id == candidate_id:
                result = treatment_by_case.get(entry.case_id)
                if result is not None:
                    error_type = self._result_error_type(result)
                    entries.append(
                        entry.model_copy(
                            update={
                                "status": self._entry_status(error_type),
                                "result": result,
                                "error_type": error_type,
                            }
                        )
                    )
                    continue
            entries.append(entry)
        return session.model_copy(update={"ledger": tuple(entries), "updated_at": self._now()})

    def _replace_candidate_entries_without_results(
        self,
        session: OptimizationV2Session,
        candidate_id: str,
        error_type: OptimizationV2ErrorType,
    ) -> OptimizationV2Session:
        status = self._entry_status(error_type)
        entries = tuple(
            item.model_copy(
                update={
                    "status": status,
                    "error_type": error_type,
                }
            )
            if item.candidate_id == candidate_id and item.status == "PENDING"
            else item
            for item in session.ledger
        )
        return session.model_copy(update={"ledger": entries, "updated_at": self._now()})

    def _rearm_retryable_provider_blocks(
        self, session: OptimizationV2Session
    ) -> OptimizationV2Session:
        """Re-open only a provider-rejected candidate on an explicit resume.

        This is intentionally not used by :meth:`run`.  The prior rejected
        result remains in the state/ledger history while the next attempt gets
        a new immutable runner configuration identity.
        """
        retryable_ids = {
            state.candidate_id
            for state in session.candidates
            if state.status == "PROVIDER_BLOCKED" and state.retryable_provider_error
        }
        pending_exists = any(state.status == "PENDING" for state in session.candidates)
        if not retryable_ids and not pending_exists:
            raise OptimizationV2Error(
                "session is provider-blocked by partially observed work; automatic pair replay "
                "is forbidden"
            )
        candidates = []
        for state in session.candidates:
            if state.candidate_id not in retryable_ids:
                candidates.append(state)
                continue
            candidates.append(
                state.model_copy(
                    update={
                        "status": "PENDING",
                        "evaluation": None,
                        "newly_consumed_agent_runs": 0,
                        "newly_observed_cost_microusd": 0,
                        "reused_baseline_runs": 0,
                        "error_history": state.error_history + state.error_types,
                        "error_types": (),
                        "retryable_provider_error": False,
                    }
                )
            )
        entries = []
        for entry in session.ledger:
            should_rearm = entry.status == "PROVIDER_BLOCKED" and (
                entry.arm == "baseline" or entry.candidate_id in retryable_ids
            )
            if not should_rearm:
                entries.append(entry)
                continue
            entries.append(
                entry.model_copy(
                    update={
                        "status": "PENDING",
                        "result": None,
                        "error_history": entry.error_history
                        + ((entry.error_type,) if entry.error_type is not None else ()),
                        "error_type": None,
                    }
                )
            )
        return session.model_copy(
            update={
                "status": "PARTIAL",
                "candidates": tuple(candidates),
                "ledger": tuple(entries),
                "last_error_type": None,
                "updated_at": self._now(),
            }
        )

    def _replace_candidate(
        self,
        session: OptimizationV2Session,
        replacement: OptimizationV2CandidateState,
        authorization: RealEvaluationAuthorization,
        before_runs: int,
        before_cost: int,
    ) -> OptimizationV2Session:
        candidates = tuple(
            replacement if item.candidate_id == replacement.candidate_id else item
            for item in session.candidates
        )
        return session.model_copy(
            update={
                "candidates": candidates,
                "observed_agent_runs": (
                    session.observed_agent_runs + authorization.consumed_agent_runs - before_runs
                ),
                "observed_cost_microusd": (
                    session.observed_cost_microusd
                    + authorization.consumed_cost_microusd
                    - before_cost
                ),
                "updated_at": self._now(),
            }
        )

    def _terminal_partial_status(
        self,
        session: OptimizationV2Session,
        error_type: OptimizationV2ErrorType,
    ) -> OptimizationV2Session:
        return session.model_copy(
            update={
                "status": "BLOCKED" if self._provider_blocked(error_type) else "PARTIAL",
                "last_error_type": error_type,
                "updated_at": self._now(),
            }
        )

    def _completed_status(self, session: OptimizationV2Session) -> OptimizationV2Session:
        pending = any(item.status == "PENDING" for item in session.candidates)
        failed = any(item.status != "COMPLETED" for item in session.candidates)
        return session.model_copy(
            update={
                "status": "PARTIAL" if pending or failed else "COMPLETED",
                "updated_at": self._now(),
            }
        )

    def _baseline_replay_cache(
        self, session: OptimizationV2Session, agent_spec: RealAgentEvidenceSpec
    ) -> Dict[str, BaselineReplay]:
        evaluator_sha = stable_sha256(
            {
                "type": "real_agent",
                "config": agent_spec.model_dump(
                    mode="json", exclude={"name", "dataset_path", "skill_path", "case_ids"}
                ),
            }
        )
        namespace = stable_sha256(
            {
                "baseline_skill_sha256": session.inputs.parent_skill_sha256,
                "dataset_sha256": session.inputs.dataset_sha256,
                "evaluator_sha256": evaluator_sha,
            }
        )
        cache: Dict[str, BaselineReplay] = {}
        for entry in session.ledger:
            if entry.arm != "baseline" or entry.status != "COMPLETED" or entry.result is None:
                continue
            cache[stable_sha256({"namespace": namespace, "case_id": entry.case_id})] = (
                self._replay_from_archived_baseline(entry.result)
            )
        return cache

    def _replay_from_archived_baseline(self, result: SearchCaseResult) -> BaselineReplay:
        if (
            result.experiment_id is None
            or result.run_id is None
            or result.attempt_id is None
        ):
            raise OptimizationV2Error("baseline ledger entry has incomplete replay evidence")
        workspace = self.workspace / "runtime" / "real-optimizer-evidence"
        store = LocalExperimentStore(workspace)
        run = store.load_run(result.experiment_id, result.run_id)
        attempt = store.load_selected_attempt(result.experiment_id, run)
        if attempt is None or attempt.id != result.attempt_id:
            raise OptimizationV2Error("baseline replay attempt is missing or changed")
        manifest = load_model(
            ExperimentLayout(workspace, result.experiment_id).artifact_manifest(
                result.run_id, attempt.attempt_no
            ).read_bytes(),
            ArtifactManifest,
        )
        root = ExperimentLayout(workspace, result.experiment_id).attempt_root(
            result.run_id, attempt.attempt_no
        ) / "raw-runner"
        artifacts = []
        observations = []
        for artifact in manifest.artifacts:
            if not artifact.path.startswith("raw-runner/"):
                continue
            relative = artifact.path[len("raw-runner/") :]
            if relative in {"platform-stdout.log", "platform-stderr.log"}:
                continue
            path = root / relative
            if (
                not path.is_file()
                or path.is_symlink()
                or self._sha(path.read_bytes()) != artifact.sha256
            ):
                raise OptimizationV2Error("baseline replay artifact no longer matches its manifest")
            content = path.read_bytes()
            artifacts.append((relative, content))
            observations.append(
                ArtifactObservation(relative, artifact.sha256, artifact.size_bytes)
            )
        runner_result = RunnerResult(
            execution_id="replayed-baseline",
            case_id=result.case_id,
            status=RunnerStatus.PASS if result.passed else RunnerStatus.FAIL,
            exit_reason=ExitReason.COMPLETED if result.passed else ExitReason.CASE_FAILED,
            process_exit_code=0,
            duration_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_microusd=result.cost_microusd,
            artifacts=tuple(observations),
        )
        return BaselineReplay(result=runner_result, artifacts=tuple(artifacts))

    def _write_report(
        self, session: OptimizationV2Session
    ) -> Tuple[OptimizationV2ScreeningReport, Path, Path]:
        baseline_by_case = {
            item.case_id: item.result
            for item in session.ledger
            if item.arm == "baseline" and item.result is not None
        }
        candidates = tuple(
            self._candidate_report(session, state, baseline_by_case)
            for state in session.candidates
        )
        errors = tuple(
            item
            for state in session.candidates
            for item in state.error_history + state.error_types
        ) + tuple(
            error
            for item in session.ledger
            for error in item.error_history
            + ((item.error_type,) if item.error_type is not None else ())
        )
        error_counts: Dict[str, int] = {}
        for error in sorted(set(errors)):
            error_counts[str(error)] = sum(item == error for item in errors)
        status: Literal["COMPLETED", "PARTIAL", "BLOCKED", "BUDGET_EXHAUSTED"]
        status = "PARTIAL" if session.status == "READY" else session.status
        report = OptimizationV2ScreeningReport(
            name=session.name,
            status=status,
            proposal_job_id=session.proposal_job_id,
            parent_skill_sha256=session.inputs.parent_skill_sha256,
            provider=session.provider,
            model=session.model,
            case_ids=session.case_ids,
            candidates=candidates,
            planned_agent_runs=session.planned_agent_runs,
            expected_new_agent_runs=session.expected_new_agent_runs,
            observed_agent_runs=session.observed_agent_runs,
            observed_cost_microusd=session.observed_cost_microusd,
            baseline_reused_runs=sum(
                item.reused_baseline_runs for item in session.candidates
            ),
            completed_candidate_ids=tuple(
                item.candidate_id for item in session.candidates if item.status == "COMPLETED"
            ),
            invalid_candidate_ids=tuple(
                item.candidate_id for item in session.candidates if item.status == "INVALID"
            ),
            provider_blocked_candidate_ids=tuple(
                item.candidate_id
                for item in session.candidates
                if item.status == "PROVIDER_BLOCKED"
            ),
            remaining_candidate_ids=tuple(
                item.candidate_id for item in session.candidates if item.status == "PENDING"
            ),
            error_counts=error_counts,
            session_path=str(self._session_path()),
        )
        report_path = self.workspace / "optimization-v2-screening.json"
        html_path = self.workspace / "optimization-v2-screening.html"
        self.writer.write(report_path, canonical_json(report.model_dump(mode="json")) + b"\n")
        self.writer.write(html_path, self._html(report).encode("utf-8"))
        return report, report_path, html_path

    def _candidate_report(
        self,
        session: OptimizationV2Session,
        state: OptimizationV2CandidateState,
        baseline_by_case: Dict[str, SearchCaseResult],
    ) -> OptimizationV2CandidateResult:
        run_keys = tuple(
            item.run_key
            for item in session.ledger
            if item.candidate_id == state.candidate_id
        )
        baseline = tuple(
            baseline_by_case[case_id] for case_id in session.case_ids if case_id in baseline_by_case
        )
        evaluation = state.evaluation
        baseline_is_valid = all(item.outcome != "invalid" for item in baseline)
        if (
            evaluation is None
            or state.status != "COMPLETED"
            or len(baseline) != len(session.case_ids)
            or not baseline_is_valid
        ):
            return OptimizationV2CandidateResult(
                candidate_id=state.candidate_id,
                skill_sha256=state.skill_sha256,
                status=state.status,
                baseline_results=baseline,
                evaluation=evaluation,
                wtl={"win": 0, "tie": 0, "loss": 0},
                invalid_runs=sum(
                    item.outcome == "invalid"
                    for item in baseline + (evaluation.results if evaluation else ())
                ),
                newly_consumed_agent_runs=state.newly_consumed_agent_runs,
                newly_observed_cost_microusd=state.newly_observed_cost_microusd,
                reused_baseline_runs=state.reused_baseline_runs,
                error_types=state.error_types,
                error_history=state.error_history,
                run_keys=run_keys,
            )
        baseline_pass_rate = sum(item.passed for item in baseline) / len(baseline)
        treatment = evaluation.results
        wtl = {"win": 0, "tie": 0, "loss": 0}
        if state.status == "COMPLETED":
            for base, candidate in zip(baseline, treatment):
                if candidate.passed and not base.passed:
                    wtl["win"] += 1
                elif candidate.passed == base.passed:
                    wtl["tie"] += 1
                else:
                    wtl["loss"] += 1
        baseline_costs = [item.cost_microusd for item in baseline]
        treatment_costs = [item.cost_microusd for item in treatment]
        return OptimizationV2CandidateResult(
            candidate_id=state.candidate_id,
            skill_sha256=state.skill_sha256,
            status=state.status,
            baseline_results=baseline,
            evaluation=evaluation,
            baseline_pass_rate=baseline_pass_rate,
            candidate_pass_rate=evaluation.pass_rate,
            absolute_gain=evaluation.pass_rate - baseline_pass_rate,
            wtl=wtl,
            invalid_runs=sum(item.outcome == "invalid" for item in baseline + treatment),
            token_summary={
                "baseline": sum(item.input_tokens + item.output_tokens for item in baseline),
                "candidate": evaluation.total_tokens,
            },
            latency_summary={
                "baseline": sum(item.latency_ms for item in baseline),
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
            newly_consumed_agent_runs=state.newly_consumed_agent_runs,
            newly_observed_cost_microusd=state.newly_observed_cost_microusd,
            reused_baseline_runs=state.reused_baseline_runs,
            error_types=state.error_types,
            error_history=state.error_history,
            run_keys=run_keys,
        )

    def _load_session(self) -> OptimizationV2Session:
        path = self._session_path()
        if not path.is_file():
            raise OptimizationV2Error("no Optimization v2 session exists in this workspace")
        try:
            return load_model(path.read_bytes(), OptimizationV2Session)
        except (OSError, ValueError) as exc:
            raise OptimizationV2Error(f"invalid Optimization v2 session: {exc}") from exc

    def _save_session(self, session: OptimizationV2Session) -> None:
        self.writer.write(self._session_path(), model_bytes(session))

    def _session_path(self) -> Path:
        return self.workspace / "optimization-v2-session.json"

    @staticmethod
    def _run_key(
        session_id: str,
        arm: Literal["baseline", "candidate"],
        candidate_id: Optional[str],
        case_id: str,
    ) -> str:
        return stable_sha256(
            {
                "session_id": session_id,
                "arm": arm,
                "candidate_id": candidate_id,
                "case_id": case_id,
            }
        )

    @staticmethod
    def _entry_status(
        error_type: Optional[OptimizationV2ErrorType],
    ) -> Literal["COMPLETED", "INVALID", "PROVIDER_BLOCKED"]:
        if error_type is None or error_type == "task_failed":
            return "COMPLETED"
        if error_type in {"insufficient_balance", "rate_limited", "provider_timeout"}:
            return "PROVIDER_BLOCKED"
        return "INVALID"

    def _evaluation_error_types(
        self, results: Sequence[SearchCaseResult]
    ) -> Tuple[OptimizationV2ErrorType, ...]:
        return tuple(
            sorted(
                {
                    error
                    for result in results
                    for error in (self._result_error_type(result),)
                    if error is not None
                }
            )
        )

    def _result_error_type(
        self, result: SearchCaseResult
    ) -> Optional[OptimizationV2ErrorType]:
        if result.outcome == "fail":
            return "task_failed"
        if result.outcome != "invalid":
            return None
        return self._classify_error_text(self._result_error_text(result))

    def _result_error_text(self, result: SearchCaseResult) -> str:
        values = []
        if result.failure_diagnosis_ref:
            path = Path(result.failure_diagnosis_ref)
            with suppress(OSError):
                values.append(path.read_text(encoding="utf-8", errors="replace"))
        if result.trace_ref:
            path = Path(result.trace_ref).parent / "raw-runner" / "result.json"
            with suppress(OSError):
                values.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(values)

    @staticmethod
    def _classify_error_text(value: str) -> OptimizationV2ErrorType:
        text = value.lower()
        if (
            re.search(r"(?<![0-9a-f])402(?![0-9a-f])", text)
            or "insufficient balance" in text
            or "insufficient_balance" in text
        ):
            return "insufficient_balance"
        if (
            re.search(r"(?<![0-9a-f])429(?![0-9a-f])", text)
            or "rate limit" in text
            or "rate_limited" in text
        ):
            return "rate_limited"
        if "timeout" in text or "timed out" in text:
            return "provider_timeout"
        if "budget exhausted" in text or "budget_exhausted" in text:
            return "budget_exhausted"
        return "agent_invalid"

    @staticmethod
    def _provider_blocked(error_type: OptimizationV2ErrorType) -> bool:
        return error_type in {"insufficient_balance", "rate_limited", "provider_timeout"}

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _candidate_config(
        agent_spec: RealAgentEvidenceSpec,
        candidate_path: Path,
        candidate_id: str,
        attempt_count: int,
    ) -> Path:
        root = candidate_path.parents[1].parent / "generated-configs"
        root.mkdir(parents=True, exist_ok=True)
        config_path = root / f"{candidate_id}.yaml"
        payload = agent_spec.model_copy(
            update={
                "name": f"{agent_spec.name} {candidate_id} attempt {attempt_count + 1}",
                "skill_path": candidate_path,
            }
        ).model_dump(mode="json")
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return config_path

    @staticmethod
    def _html(report: OptimizationV2ScreeningReport) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        rows = []
        for candidate in report.candidates:
            traces = ", ".join(
                item.trace_ref or "unavailable"
                for item in (candidate.evaluation.results if candidate.evaluation else ())
            )
            tokens = candidate.token_summary
            latency = candidate.latency_summary
            candidate_rate = ""
            if candidate.candidate_pass_rate is not None:
                candidate_rate = f"{candidate.candidate_pass_rate:.3f}"
            baseline_rate = ""
            if candidate.baseline_pass_rate is not None:
                baseline_rate = f"{candidate.baseline_pass_rate:.3f}"
            gain = "" if candidate.absolute_gain is None else f"{candidate.absolute_gain:+.3f}"
            rows.append(
                "<tr>"
                f"<td>{esc(candidate.candidate_id)}</td>"
                f"<td>{esc(candidate.status)}</td>"
                f"<td>{candidate_rate}</td>"
                f"<td>{baseline_rate}</td>"
                f"<td>{gain}</td>"
                f"<td>{esc(candidate.wtl)}</td>"
                f"<td>{candidate.invalid_runs}</td>"
                f"<td>{tokens.get('baseline', 0)} / {tokens.get('candidate', 0)}</td>"
                f"<td>{latency.get('baseline', 0)} / {latency.get('candidate', 0)}</td>"
                f"<td>{esc(candidate.cost_summary)}</td>"
                f"<td>{candidate.newly_consumed_agent_runs}</td>"
                f"<td>{esc(', '.join(candidate.error_types) or 'none')}</td>"
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
<dt>Completed candidates</dt><dd>{esc(', '.join(report.completed_candidate_ids) or 'none')}</dd>
<dt>Invalid candidates</dt><dd>{esc(', '.join(report.invalid_candidate_ids) or 'none')}</dd>
<dt>Provider blocked candidates</dt><dd>
{esc(', '.join(report.provider_blocked_candidate_ids) or 'none')}</dd>
<dt>Remaining candidates</dt><dd>{esc(', '.join(report.remaining_candidate_ids) or 'none')}</dd>
<dt>Error types</dt><dd>{esc(report.error_counts)}</dd>
<dt>Session ledger</dt><dd><code>{esc(report.session_path)}</code></dd>
</dl>
<table><thead><tr><th>Candidate</th><th>Status</th><th>Candidate pass rate</th>
<th>v1 pass rate</th><th>Gain</th><th>W/T/L</th><th>Invalid</th>
<th>Tokens v1/candidate</th><th>Latency v1/candidate (ms)</th>
<th>Cost v1/candidate</th><th>New Runs</th><th>Error types</th><th>Trace refs</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
