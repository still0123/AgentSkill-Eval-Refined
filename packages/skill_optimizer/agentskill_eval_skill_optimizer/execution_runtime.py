"""Budgeted execution of the adaptive stages in a frozen Skill evolution plan."""

from __future__ import annotations

import hashlib
import html
import os
from pathlib import Path
from typing import Dict, Literal, Optional, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator

from agentskill_eval_benchmark_gen import DatasetLoader, DatasetSplit
from agentskill_eval_contracts import (
    FrozenModel,
    SearchEvaluationStage,
    canonical_json,
    stable_sha256,
)
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_real_evidence import RealAgentEvidenceSpec
from agentskill_eval_skill_optimizer.evolution import (
    EvolutionHandoff,
    HypothesisArtifact,
    RegressionGateResult,
)
from agentskill_eval_skill_optimizer.execution_dry_run import (
    EvolutionDryRunOrchestrator,
    EvolutionDryRunReport,
)
from agentskill_eval_skill_optimizer.execution_plan import (
    EvolutionExecutionPlan,
    EvolutionStagePlan,
    RealEvolutionExecutionPlanner,
)
from agentskill_eval_skill_optimizer.proposal import RealLLMProposalService
from agentskill_eval_skill_optimizer.real_evaluator import (
    RealAgentCandidateEvaluator,
    RealEvaluationAuthorization,
)
from agentskill_eval_skill_optimizer.search import (
    BenchmarkGuidedSkillSearch,
    SkillSearchError,
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
)


class EvolutionRuntimeError(RuntimeError):
    """Raised when paid adaptive execution violates its frozen plan or evidence."""


class EvolutionRuntimeSpec(FrozenModel):
    schema_version: Literal["ase/real-evolution-runtime-spec/v1alpha1"]
    name: str = Field(min_length=1, max_length=120)
    execution_plan_directory: Path
    dry_run_directory: Path
    benchmark_workspace: Path
    proposal_directory: Path
    base_skill_path: Path
    manual_skill_path: Path
    real_agent_config_path: Path
    search: SearchAlgorithmSpec
    constraints: SearchConstraintSpec = SearchConstraintSpec()
    timeout_seconds: int = Field(default=900, ge=1, le=7200)
    claim_limit: str = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> "EvolutionRuntimeSpec":
        try:
            spec = cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            root = path.resolve(strict=True).parent

            def resolved(value: Path, *, directory: Optional[bool] = None) -> Path:
                candidate = value if value.is_absolute() else root / value
                if candidate.is_symlink():
                    raise EvolutionRuntimeError("symbolic-link runtime inputs are not allowed")
                result = candidate.expanduser().resolve(strict=True)
                if directory is True and not result.is_dir():
                    raise EvolutionRuntimeError(f"expected directory: {result}")
                if directory is False and not result.is_file():
                    raise EvolutionRuntimeError(f"expected regular file: {result}")
                return result

            return spec.model_copy(
                update={
                    "execution_plan_directory": resolved(
                        spec.execution_plan_directory, directory=True
                    ),
                    "dry_run_directory": resolved(spec.dry_run_directory, directory=True),
                    "benchmark_workspace": resolved(spec.benchmark_workspace, directory=True),
                    "proposal_directory": resolved(spec.proposal_directory, directory=True),
                    "base_skill_path": resolved(spec.base_skill_path),
                    "manual_skill_path": resolved(spec.manual_skill_path),
                    "real_agent_config_path": resolved(
                        spec.real_agent_config_path, directory=False
                    ),
                }
            )
        except EvolutionRuntimeError:
            raise
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise EvolutionRuntimeError(
                f"invalid real evolution runtime spec {path}: {exc}"
            ) from exc


class EvolutionRuntimePreflight(FrozenModel):
    schema_version: Literal["ase/real-evolution-runtime-preflight/v1alpha1"] = (
        "ase/real-evolution-runtime-preflight/v1alpha1"
    )
    execution_id: UUID
    plan_id: UUID
    dry_run_id: UUID
    proposal_job_id: UUID
    provider: str
    model: str
    validation_search_dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    regression_dev_dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    search_agent_runs: int = Field(ge=1)
    search_max_cost_microusd: int = Field(ge=1)
    regression_agent_runs: int = Field(ge=1)
    regression_max_cost_microusd: int = Field(ge=1)
    status: Literal["READY_FOR_SEARCH_AUTHORIZATION"] = "READY_FOR_SEARCH_AUTHORIZATION"
    validation_confirm_accessed: Literal[False] = False
    locked_test_accessed: Literal[False] = False
    claim_limit: str


class AdaptiveStageReceipt(FrozenModel):
    schema_version: Literal["ase/adaptive-stage-receipt/v1alpha1"] = (
        "ase/adaptive-stage-receipt/v1alpha1"
    )
    execution_id: UUID
    stage: Literal["validation_search", "regression_dev"]
    plan_id: UUID
    dataset_version_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["COMPLETED", "NO_WINNER", "REGRESSION_REJECTED"] = "COMPLETED"
    authorized_agent_runs: int = Field(ge=1)
    authorized_cost_microusd: int = Field(ge=1)
    consumed_agent_runs: int = Field(ge=0)
    consumed_cost_microusd: int = Field(ge=0)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    winner_candidate_id: Optional[UUID] = None
    winner_skill_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    real_run_confirmed: Literal[True] = True
    simulated: Literal[False] = False
    evidence_class: Literal["observed_agent_adaptive_evolution"] = (
        "observed_agent_adaptive_evolution"
    )
    validation_confirm_accessed: Literal[False] = False
    locked_test_accessed: Literal[False] = False

    @model_validator(mode="after")
    def winner_matches_status(self) -> "AdaptiveStageReceipt":
        winner = (self.winner_candidate_id, self.winner_skill_sha256)
        if self.status == "NO_WINNER" and any(value is not None for value in winner):
            raise ValueError("NO_WINNER receipt cannot declare a winner")
        if self.status != "NO_WINNER" and any(value is None for value in winner):
            raise ValueError(f"{self.status} receipt requires a winner")
        return self


class EvolutionRuntimeManifest(FrozenModel):
    schema_version: Literal["ase/real-evolution-runtime-manifest/v1alpha1"] = (
        "ase/real-evolution-runtime-manifest/v1alpha1"
    )
    execution_id: UUID
    plan_id: UUID
    stage: Literal["validation_search", "regression_dev"]
    artifacts: Dict[str, str]
    immutable: Literal[True] = True
    simulated: Literal[False] = False
    validation_confirm_accessed: Literal[False] = False
    locked_test_accessed: Literal[False] = False


class EvolutionRuntimeResult(FrozenModel):
    preflight: EvolutionRuntimePreflight
    directory: Path
    search_receipt: Optional[AdaptiveStageReceipt] = None
    regression_receipt: Optional[AdaptiveStageReceipt] = None
    handoff_path: Optional[Path] = None


class BudgetedRealEvolutionExecutor:
    """Execute search and regression separately; confirmation and locked stay withheld."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def preflight(self, spec: EvolutionRuntimeSpec) -> EvolutionRuntimePreflight:
        plan_result = RealEvolutionExecutionPlanner(
            spec.execution_plan_directory.parent.parent
        ).verify(spec.execution_plan_directory)
        plan = plan_result.plan
        dry = EvolutionDryRunOrchestrator(spec.dry_run_directory.parent.parent).verify(
            spec.dry_run_directory
        )
        proposal = RealLLMProposalService(spec.proposal_directory.parent.parent).verify(
            spec.proposal_directory
        )
        if dry.report.execution_plan_id != plan.plan_id:
            raise EvolutionRuntimeError("dry-run does not bind the selected execution plan")
        if dry.report.execution_plan_sha256 != self._sha(plan_result.plan_path.read_bytes()):
            raise EvolutionRuntimeError("dry-run execution-plan digest mismatch")
        if proposal.manifest.proposal_job_id != plan.proposal_job_id:
            raise EvolutionRuntimeError("proposal job does not match execution plan")
        proposal_manifest_sha = self._sha(
            (spec.proposal_directory / "proposal-manifest.json").read_bytes()
        )
        if proposal_manifest_sha != plan.proposal_manifest_sha256:
            raise EvolutionRuntimeError("proposal manifest digest does not match execution plan")
        base = self._skill_file(spec.base_skill_path)
        if self._sha(base.read_bytes()) != plan.base_skill_sha256:
            raise EvolutionRuntimeError("base Skill digest does not match execution plan")
        self._skill_file(spec.manual_skill_path)
        if self._sha(spec.real_agent_config_path.read_bytes()) != plan.agent.config_sha256:
            raise EvolutionRuntimeError("real Agent config digest does not match execution plan")
        real_spec = RealAgentEvidenceSpec.load(spec.real_agent_config_path)
        if real_spec.simulated:
            raise EvolutionRuntimeError("adaptive real execution refuses simulated Agent config")
        if (real_spec.agent.provider, real_spec.agent.model) != (
            plan.agent.provider,
            plan.agent.model,
        ):
            raise EvolutionRuntimeError("real Agent provider/model does not match execution plan")
        search_root = self._dataset_root(spec, dry.report, "validation_search")
        regression_root = self._dataset_root(spec, dry.report, "regression_dev")
        search_stage = self._stage(plan, "validation_search")
        regression_stage = self._stage(plan, "regression_dev")
        self._validate_search_plan(plan, spec.search)
        semantic = {
            "name": spec.name,
            "plan_id": str(plan.plan_id),
            "dry_run_id": str(dry.report.dry_run_id),
            "proposal_job_id": str(proposal.manifest.proposal_job_id),
            "base_skill_sha256": plan.base_skill_sha256,
            "manual_skill_sha256": self._sha(self._skill_file(spec.manual_skill_path).read_bytes()),
            "constraints": spec.constraints.model_dump(mode="json"),
            "timeout_seconds": spec.timeout_seconds,
            "claim_limit": spec.claim_limit,
        }
        execution_id = uuid5(
            NAMESPACE_URL, "agentskill-eval:real-evolution-execution:" + stable_sha256(semantic)
        )
        return EvolutionRuntimePreflight(
            execution_id=execution_id,
            plan_id=plan.plan_id,
            dry_run_id=dry.report.dry_run_id,
            proposal_job_id=proposal.manifest.proposal_job_id,
            provider=plan.agent.provider,
            model=plan.agent.model,
            # Keep the execution receipt's historical loader digest stable.  The
            # DatasetVersion content hash is verified in _dataset_root, but old
            # real runs already froze this loader-level digest in their preflight.
            validation_search_dataset_sha256=DatasetLoader().load(search_root).dataset_sha256,
            regression_dev_dataset_sha256=DatasetLoader().load(regression_root).dataset_sha256,
            search_agent_runs=search_stage.agent_runs,
            search_max_cost_microusd=search_stage.budget_cap.max_cost_microusd,
            regression_agent_runs=regression_stage.agent_runs,
            regression_max_cost_microusd=regression_stage.budget_cap.max_cost_microusd,
            claim_limit=spec.claim_limit,
        )

    def run_search(
        self,
        spec: EvolutionRuntimeSpec,
        authorization: RealEvaluationAuthorization,
    ) -> EvolutionRuntimeResult:
        preflight = self.preflight(spec)
        directory = self._directory(preflight.execution_id)
        receipt_path = directory / "validation-search-receipt.json"
        if receipt_path.exists():
            return self.verify(directory)
        plan = self._plan(spec)
        self._authorize(authorization, self._stage(plan, "validation_search"))
        dry = EvolutionDryRunOrchestrator(spec.dry_run_directory.parent.parent).verify(
            spec.dry_run_directory
        )
        proposal = RealLLMProposalService(spec.proposal_directory.parent.parent).verify(
            spec.proposal_directory
        )
        artifact = HypothesisArtifact.model_validate_json(proposal.proposals_path.read_bytes())
        search_root = self._dataset_root(spec, dry.report, "validation_search")
        self._validate_search_plan(plan, spec.search)
        logical_budget = (
            plan.total_candidate_count * spec.search.subset_size
            + (3 + spec.search.promote_search_candidates)
            * plan.datasets.validation_search.case_count
        )
        search_spec = OptimizationSearchSpec(
            schema_version="ase/optimization-search/v1alpha1",
            name=f"{spec.name}-validation-search",
            base_skill_path=spec.base_skill_path,
            manual_skill_path=spec.manual_skill_path,
            validation_search_path=search_root,
            mutations=tuple(
                MutationSpec(
                    id=item.id,
                    hypothesis=item.hypothesis,
                    instruction=item.instruction,
                )
                for item in artifact.hypotheses
            ),
            search=spec.search,
            constraints=spec.constraints,
            budget=SearchBudgetSpec(
                max_candidate_case_evaluations=logical_budget,
                timeout_seconds=spec.timeout_seconds,
            ),
            evaluator=self._evaluator_spec(spec),
        )
        try:
            result = BenchmarkGuidedSkillSearch(self.workspace).run(
                search_spec, real_authorization=authorization
            )
        except SkillSearchError as exc:
            if str(exc) != "no search-origin candidate satisfies Pareto and hard constraints":
                raise
            result_payload = {
                "schema_version": "ase/validation-search-result/v1alpha1",
                "status": "NO_WINNER",
                "reason": str(exc),
                "simulated": False,
                "locked_test_accessed": False,
            }
            result_bytes = canonical_json(result_payload) + b"\n"
            receipt = AdaptiveStageReceipt(
                execution_id=preflight.execution_id,
                stage="validation_search",
                plan_id=plan.plan_id,
                dataset_version_sha256=preflight.validation_search_dataset_sha256,
                status="NO_WINNER",
                authorized_agent_runs=authorization.max_agent_runs,
                authorized_cost_microusd=authorization.max_cost_microusd,
                consumed_agent_runs=authorization.consumed_agent_runs,
                consumed_cost_microusd=authorization.consumed_cost_microusd,
                result_sha256=self._sha(result_bytes),
            )
            directory.mkdir(parents=True, exist_ok=True)
            self._write_once(directory / "validation-search-result.json", result_bytes)
            self._write_once(
                receipt_path, canonical_json(receipt.model_dump(mode="json")) + b"\n"
            )
            self._write_manifest(directory, preflight, "validation_search")
            return self.verify(directory)
        winner_path = BenchmarkGuidedSkillSearch(self.workspace).store.skill_path(result.winner)
        result_payload = self._search_payload(result, winner_path)
        result_bytes = canonical_json(result_payload) + b"\n"
        receipt = AdaptiveStageReceipt(
            execution_id=preflight.execution_id,
            stage="validation_search",
            plan_id=plan.plan_id,
            dataset_version_sha256=preflight.validation_search_dataset_sha256,
            authorized_agent_runs=authorization.max_agent_runs,
            authorized_cost_microusd=authorization.max_cost_microusd,
            consumed_agent_runs=authorization.consumed_agent_runs,
            consumed_cost_microusd=authorization.consumed_cost_microusd,
            result_sha256=self._sha(result_bytes),
            winner_candidate_id=result.winner.id,
            winner_skill_sha256=result.winner.content_sha256,
        )
        directory.mkdir(parents=True, exist_ok=True)
        self._write_once(directory / "validation-search-result.json", result_bytes)
        self._write_once(receipt_path, canonical_json(receipt.model_dump(mode="json")) + b"\n")
        self._write_manifest(directory, preflight, "validation_search")
        return self.verify(directory)

    def run_regression(
        self,
        spec: EvolutionRuntimeSpec,
        authorization: RealEvaluationAuthorization,
    ) -> EvolutionRuntimeResult:
        preflight = self.preflight(spec)
        directory = self._directory(preflight.execution_id)
        search = self.verify(directory)
        if search.search_receipt is None or search.search_receipt.status != "COMPLETED":
            raise EvolutionRuntimeError("validation_search must complete before regression_dev")
        receipt_path = directory / "regression-dev-receipt.json"
        if receipt_path.exists():
            return self.verify(directory)
        plan = self._plan(spec)
        self._authorize(authorization, self._stage(plan, "regression_dev"))
        dry = EvolutionDryRunOrchestrator(spec.dry_run_directory.parent.parent).verify(
            spec.dry_run_directory
        )
        regression_root = self._dataset_root(spec, dry.report, "regression_dev")
        search_payload = self._load_json(directory / "validation-search-result.json")
        winner_path = Path(cast(str, search_payload["winner_skill_path"])).resolve(strict=True)
        gate = self._regression_gate(
            spec,
            self._skill_file(spec.base_skill_path),
            winner_path,
            regression_root,
            authorization,
        )
        result_bytes = canonical_json(gate.model_dump(mode="json")) + b"\n"
        winner_candidate_id = search.search_receipt.winner_candidate_id
        winner_skill_sha256 = search.search_receipt.winner_skill_sha256
        if winner_candidate_id is None or winner_skill_sha256 is None:
            raise EvolutionRuntimeError("completed search receipt is missing its winner")
        receipt = AdaptiveStageReceipt(
            execution_id=preflight.execution_id,
            stage="regression_dev",
            plan_id=plan.plan_id,
            dataset_version_sha256=preflight.regression_dev_dataset_sha256,
            authorized_agent_runs=authorization.max_agent_runs,
            authorized_cost_microusd=authorization.max_cost_microusd,
            consumed_agent_runs=authorization.consumed_agent_runs,
            consumed_cost_microusd=authorization.consumed_cost_microusd,
            result_sha256=self._sha(result_bytes),
            status="COMPLETED" if gate.passed else "REGRESSION_REJECTED",
            winner_candidate_id=winner_candidate_id,
            winner_skill_sha256=winner_skill_sha256,
        )
        self._write_once(directory / "regression-dev-result.json", result_bytes)
        self._write_once(receipt_path, canonical_json(receipt.model_dump(mode="json")) + b"\n")
        if gate.passed:
            handoff = EvolutionHandoff(
                evolution_id=preflight.execution_id,
                optimization_job_id=UUID(cast(str, search_payload["job_id"])),
                base_skill_sha256=plan.base_skill_sha256,
                winner_candidate_id=winner_candidate_id,
                winner_skill_sha256=winner_skill_sha256,
                winner_skill_path=str(winner_path),
                status="AWAITING_INDEPENDENT_FINAL_EVALUATION",
            )
            self._write_once(
                directory / "confirmation-handoff.json",
                canonical_json(handoff.model_dump(mode="json")) + b"\n",
            )
        self._write_once(
            directory / "adaptive-execution-report.html",
            self._html(preflight, receipt, gate).encode("utf-8"),
        )
        self._write_manifest(
            directory, preflight, "regression_dev", include_handoff=gate.passed
        )
        return self.verify(directory)

    def verify(self, directory: Path) -> EvolutionRuntimeResult:
        root = directory.resolve(strict=True)
        try:
            manifest = EvolutionRuntimeManifest.model_validate_json(
                (root / "runtime-manifest.json").read_bytes()
            )
            expected_artifacts = {
                "runtime-preflight.json",
                "validation-search-result.json",
                "validation-search-receipt.json",
            }
            if manifest.stage == "regression_dev":
                expected_artifacts.update(
                    {
                        "regression-dev-result.json",
                        "regression-dev-receipt.json",
                        "adaptive-execution-report.html",
                    }
                )
                if (root / "confirmation-handoff.json").is_file():
                    expected_artifacts.add("confirmation-handoff.json")
            if set(manifest.artifacts) != expected_artifacts:
                raise EvolutionRuntimeError("adaptive execution artifact set mismatch")
            for name, expected in manifest.artifacts.items():
                path = root / name
                if not path.is_file() or self._sha(path.read_bytes()) != expected:
                    raise EvolutionRuntimeError(f"adaptive execution artifact mismatch: {name}")
            preflight = EvolutionRuntimePreflight.model_validate_json(
                (root / "runtime-preflight.json").read_bytes()
            )
            search_receipt = (
                AdaptiveStageReceipt.model_validate_json(
                    (root / "validation-search-receipt.json").read_bytes()
                )
                if (root / "validation-search-receipt.json").exists()
                else None
            )
            regression_receipt = (
                AdaptiveStageReceipt.model_validate_json(
                    (root / "regression-dev-receipt.json").read_bytes()
                )
                if (root / "regression-dev-receipt.json").exists()
                else None
            )
        except EvolutionRuntimeError:
            raise
        except (OSError, ValueError) as exc:
            raise EvolutionRuntimeError(f"invalid adaptive execution {directory}: {exc}") from exc
        if preflight.execution_id != manifest.execution_id or preflight.plan_id != manifest.plan_id:
            raise EvolutionRuntimeError("adaptive execution identity mismatch")
        if regression_receipt is not None and search_receipt is None:
            raise EvolutionRuntimeError("regression receipt exists without search receipt")
        for receipt in (search_receipt, regression_receipt):
            if receipt is not None and receipt.execution_id != preflight.execution_id:
                raise EvolutionRuntimeError("adaptive stage receipt identity mismatch")
        if search_receipt is not None:
            result = root / "validation-search-result.json"
            if self._sha(result.read_bytes()) != search_receipt.result_sha256:
                raise EvolutionRuntimeError("validation_search receipt result digest mismatch")
        if regression_receipt is not None:
            result = root / "regression-dev-result.json"
            if self._sha(result.read_bytes()) != regression_receipt.result_sha256:
                raise EvolutionRuntimeError("regression_dev receipt result digest mismatch")
        handoff = root / "confirmation-handoff.json"
        return EvolutionRuntimeResult(
            preflight=preflight,
            directory=root,
            search_receipt=search_receipt,
            regression_receipt=regression_receipt,
            handoff_path=handoff if handoff.is_file() else None,
        )

    def _regression_gate(
        self,
        spec: EvolutionRuntimeSpec,
        base_skill: Path,
        winner_skill: Path,
        dataset_root: Path,
        authorization: RealEvaluationAuthorization,
    ) -> RegressionGateResult:
        loaded = DatasetLoader().load(dataset_root)
        if any(item.metadata.split != DatasetSplit.REGRESSION_DEV for item in loaded.cases):
            raise EvolutionRuntimeError("regression DatasetVersion contains the wrong split")
        cases = tuple(SearchCase(id=item.metadata.case_id) for item in loaded.cases)
        dataset_file = dataset_root / "dataset.yaml"
        evaluator = RealAgentCandidateEvaluator(
            spec.real_agent_config_path,
            self.workspace,
            authorization,
            baseline_skill_path=base_skill,
        )
        evaluator.authorize_plan(len(cases))
        winner = evaluator.evaluate(
            winner_skill,
            dataset_file,
            loaded.dataset_sha256,
            cases,
            SearchEvaluationStage.REGRESSION_DEV,
            spec.timeout_seconds,
        )
        base = evaluator.baseline_evaluation(
            loaded.dataset_sha256,
            cases,
            SearchEvaluationStage.REGRESSION_DEV,
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
            dataset_sha256=loaded.dataset_sha256,
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

    def _write_manifest(
        self,
        directory: Path,
        preflight: EvolutionRuntimePreflight,
        stage: Literal["validation_search", "regression_dev"],
        *,
        include_handoff: bool = False,
    ) -> None:
        preflight_path = directory / "runtime-preflight.json"
        self._write_once(preflight_path, canonical_json(preflight.model_dump(mode="json")) + b"\n")
        names = [
            "runtime-preflight.json",
            "validation-search-result.json",
            "validation-search-receipt.json",
        ]
        if stage == "regression_dev":
            names.extend(
                [
                    "regression-dev-result.json",
                    "regression-dev-receipt.json",
                    "adaptive-execution-report.html",
                ]
            )
            if include_handoff:
                names.append("confirmation-handoff.json")
        artifacts = {name: self._sha((directory / name).read_bytes()) for name in names}
        manifest = EvolutionRuntimeManifest(
            execution_id=preflight.execution_id,
            plan_id=preflight.plan_id,
            stage=stage,
            artifacts=artifacts,
        )
        path = directory / "runtime-manifest.json"
        payload = canonical_json(manifest.model_dump(mode="json")) + b"\n"
        temporary = directory / "runtime-manifest.next.json"
        if temporary.exists():
            temporary.unlink()
        self.writer.write(temporary, payload)
        os.replace(temporary, path)

    def _dataset_root(
        self, spec: EvolutionRuntimeSpec, report: EvolutionDryRunReport, split: str
    ) -> Path:
        bindings = report.adaptive_bindings
        binding = next((item for item in bindings if item.split == split), None)
        if binding is None:
            raise EvolutionRuntimeError(f"dry-run is missing adaptive binding: {split}")
        root = (spec.benchmark_workspace / binding.relative_path).resolve(strict=True)
        if not root.is_relative_to(spec.benchmark_workspace):
            raise EvolutionRuntimeError("adaptive DatasetVersion path escapes benchmark workspace")
        loaded = DatasetLoader().load(root)
        # Published split bindings use BenchmarkDatasetVersion.content_sha256, while
        # DatasetLoader.dataset_sha256 is the loader's metadata digest.  They are
        # deliberately different identifiers; compare the immutable published
        # content hash when available and retain the loader digest for fixtures.
        published_version = getattr(loaded, "dataset_version", None)
        observed_digest = (
            published_version.content_sha256
            if published_version is not None
            else loaded.dataset_sha256
        )
        if observed_digest != binding.dataset_version_sha256:
            raise EvolutionRuntimeError(f"adaptive DatasetVersion digest mismatch: {split}")
        expected = DatasetSplit(split)
        if any(item.metadata.split != expected for item in loaded.cases):
            raise EvolutionRuntimeError(f"adaptive DatasetVersion contains wrong split: {split}")
        return root

    @staticmethod
    def _stage(plan: EvolutionExecutionPlan, name: str) -> EvolutionStagePlan:
        stage = next((item for item in plan.stages if item.stage == name), None)
        if stage is None:
            raise EvolutionRuntimeError(f"execution plan is missing stage: {name}")
        return stage

    @staticmethod
    def _validate_search_plan(plan: EvolutionExecutionPlan, search: SearchAlgorithmSpec) -> None:
        stage = BudgetedRealEvolutionExecutor._stage(plan, "validation_search")
        expected = plan.total_candidate_count * search.subset_size + (
            3 + search.promote_search_candidates
        ) * (plan.datasets.validation_search.case_count - search.subset_size)
        if expected != stage.candidate_case_evaluations:
            raise EvolutionRuntimeError("runtime search settings do not match frozen plan")

    @staticmethod
    def _authorize(authorization: RealEvaluationAuthorization, stage: EvolutionStagePlan) -> None:
        if not authorization.confirm_real_run:
            raise EvolutionRuntimeError(f"{stage.stage} requires explicit real-run confirmation")
        if authorization.max_agent_runs > stage.budget_cap.max_agent_runs:
            raise EvolutionRuntimeError(f"{stage.stage} authorization exceeds frozen Run cap")
        if authorization.max_cost_microusd > stage.budget_cap.max_cost_microusd:
            raise EvolutionRuntimeError(f"{stage.stage} authorization exceeds frozen cost cap")
        if authorization.max_agent_runs < stage.agent_runs:
            raise EvolutionRuntimeError(f"{stage.stage} authorization is below planned Agent Runs")
        if authorization.max_cost_microusd < stage.estimated_cost_microusd:
            raise EvolutionRuntimeError(f"{stage.stage} authorization is below planned cost")

    @staticmethod
    def _evaluator_spec(spec: EvolutionRuntimeSpec) -> EvaluatorSpec:
        return EvaluatorSpec(
            type="real_agent",
            real_agent_config_path=spec.real_agent_config_path,
            version="budgeted-real-evolution-v1",
            simulated=False,
        )

    def _plan(self, spec: EvolutionRuntimeSpec) -> EvolutionExecutionPlan:
        return (
            RealEvolutionExecutionPlanner(spec.execution_plan_directory.parent.parent)
            .verify(spec.execution_plan_directory)
            .plan
        )

    def _directory(self, execution_id: UUID) -> Path:
        return self.workspace / "real-evolution-executions" / str(execution_id)

    @staticmethod
    def _search_payload(result: SkillSearchResult, winner_path: Path) -> Dict[str, object]:
        return {
            "schema_version": "ase/validation-search-result/v1alpha1",
            "job_id": str(result.job.id),
            "candidate_count": len(result.candidates),
            "evaluations_used": result.job.evaluations_used,
            "winner_candidate_id": str(result.winner.id),
            "winner_skill_sha256": result.winner.content_sha256,
            "winner_skill_path": str(winner_path.resolve(strict=True)),
            "simulated": False,
            "locked_test_accessed": False,
        }

    @staticmethod
    def _skill_file(path: Path) -> Path:
        resolved = path.resolve(strict=True)
        candidate = resolved / "SKILL.md" if resolved.is_dir() else resolved
        if candidate.is_symlink() or not candidate.is_file() or candidate.name != "SKILL.md":
            raise EvolutionRuntimeError("Skill input must be a regular SKILL.md or directory")
        return candidate

    @staticmethod
    def _load_json(path: Path) -> Dict[str, object]:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise EvolutionRuntimeError(f"expected JSON object: {path}")
        return payload

    def _write_once(self, path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise EvolutionRuntimeError(
                    f"immutable adaptive execution artifact changed: {path}"
                )
            return
        self.writer.write(path, content)

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _html(
        preflight: EvolutionRuntimePreflight,
        receipt: AdaptiveStageReceipt,
        gate: RegressionGateResult,
    ) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">
<title>Adaptive Skill Evolution Execution</title></head><body>
<h1>Adaptive Skill Evolution Execution</h1>
<p>Provider/model: <strong>{esc(preflight.provider)} / {esc(preflight.model)}</strong></p>
<p>Execution: <code>{esc(preflight.execution_id)}</code></p>
<p>Search and regression: completed · simulated=false · locked accessed=false</p>
<p>Winner: <code>{esc(receipt.winner_skill_sha256)}</code></p>
<p>Regression passed: <strong>{esc(gate.passed)}</strong>; losses: {esc(len(gate.loss_cases))};
invalid: {esc(len(gate.invalid_cases))}</p>
<p>Runs: {esc(receipt.consumed_agent_runs)}; cost:
{esc(receipt.consumed_cost_microusd)} microusd</p>
<h2>Claim limit</h2><p>{esc(preflight.claim_limit)}</p></body></html>"""
