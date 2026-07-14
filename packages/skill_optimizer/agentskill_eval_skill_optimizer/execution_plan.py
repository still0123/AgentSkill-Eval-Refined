"""Immutable, planning-only contracts for a real Skill evolution experiment."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator

from agentskill_eval_contracts import FrozenModel, canonical_json, stable_sha256
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_real_evidence import RealAgentEvidenceSpec
from agentskill_eval_skill_optimizer.proposal import RealLLMProposalService

StageName = Literal[
    "validation_search", "regression_dev", "validation_confirm", "locked_test"
]


class EvolutionExecutionPlanError(RuntimeError):
    """Raised when a plan is inconsistent, under-budgeted, or has been modified."""


class DatasetPlanDescriptor(FrozenModel):
    """Metadata-only split binding; deliberately has no dataset content path."""

    split: Literal[
        "validation_search", "regression_dev", "validation_confirm", "locked_test"
    ]
    dataset_version_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)
    independent_group_count: int = Field(ge=1)
    content_access: Literal["metadata_only"] = "metadata_only"


class EvolutionDatasetPlan(FrozenModel):
    validation_search: DatasetPlanDescriptor
    regression_dev: DatasetPlanDescriptor
    validation_confirm: DatasetPlanDescriptor
    locked_test: DatasetPlanDescriptor

    @model_validator(mode="after")
    def enforce_split_isolation(self) -> "EvolutionDatasetPlan":
        entries = {
            "validation_search": self.validation_search,
            "regression_dev": self.regression_dev,
            "validation_confirm": self.validation_confirm,
            "locked_test": self.locked_test,
        }
        for expected, descriptor in entries.items():
            if descriptor.split != expected:
                raise ValueError(f"dataset descriptor {expected} declares {descriptor.split}")
        plan_hashes = {item.split_plan_sha256 for item in entries.values()}
        if len(plan_hashes) != 1:
            raise ValueError("all split descriptors must bind the same split plan")
        version_hashes = [item.dataset_version_sha256 for item in entries.values()]
        if len(set(version_hashes)) != len(version_hashes):
            raise ValueError("dataset versions must be distinct across evaluation splits")
        return self


class SearchPlanSpec(FrozenModel):
    subset_size: int = Field(ge=1)
    promote_search_candidates: int = Field(ge=1)
    random_seed: int = 2026


class FinalPlanSpec(FrozenModel):
    validation_confirm_repeats: int = Field(default=3, ge=1, le=20)
    locked_test_repeats: int = Field(default=1, ge=1, le=20)


class StageBudgetCap(FrozenModel):
    max_agent_runs: int = Field(ge=1)
    max_cost_microusd: int = Field(ge=1)


class EvolutionBudgetCaps(FrozenModel):
    validation_search: StageBudgetCap
    regression_dev: StageBudgetCap
    validation_confirm: StageBudgetCap
    locked_test: StageBudgetCap


class EvolutionExecutionPlanSpec(FrozenModel):
    schema_version: Literal["ase/real-evolution-execution-plan-spec/v1alpha1"]
    name: str = Field(min_length=1)
    proposal_directory: Path
    real_agent_config_path: Path
    datasets: EvolutionDatasetPlan
    search: SearchPlanSpec
    final: FinalPlanSpec = FinalPlanSpec()
    budgets: EvolutionBudgetCaps
    claim_limit: str = Field(min_length=1)

    @model_validator(mode="after")
    def sizes_fit_search(self) -> "EvolutionExecutionPlanSpec":
        cases = self.datasets.validation_search.case_count
        if self.search.subset_size >= cases:
            raise ValueError("search subset_size must be smaller than validation_search")
        return self

    @classmethod
    def load(cls, path: Path) -> "EvolutionExecutionPlanSpec":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = cls.model_validate(payload)
            root = path.resolve(strict=True).parent

            def resolved(value: Path) -> Path:
                candidate = value if value.is_absolute() else root / value
                if candidate.is_symlink():
                    raise EvolutionExecutionPlanError("symbolic-link plan inputs are not allowed")
                return candidate.resolve(strict=True)

            return spec.model_copy(
                update={
                    "proposal_directory": resolved(spec.proposal_directory),
                    "real_agent_config_path": resolved(spec.real_agent_config_path),
                }
            )
        except EvolutionExecutionPlanError:
            raise
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise EvolutionExecutionPlanError(f"invalid evolution plan spec {path}: {exc}") from exc


class PlannedTokenUsage(FrozenModel):
    input_tokens: int = Field(ge=0)
    cache_hit_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class EvolutionStagePlan(FrozenModel):
    stage: StageName
    ordinal: int = Field(ge=1, le=4)
    dataset_version_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)
    candidate_case_evaluations: int = Field(ge=1)
    agent_runs: int = Field(ge=1)
    estimated_cost_microusd: int = Field(ge=1)
    estimated_tokens: PlannedTokenUsage
    budget_cap: StageBudgetCap
    explicit_authorization_required: Literal[True] = True
    locked_receipt_required: bool = False
    will_execute: Literal[False] = False


class FrozenAgentIdentity(FrozenModel):
    provider: str
    model: str
    engine: str
    engine_version: str
    agent_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runner_name: str
    runner_version: str
    runner_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temperature: float
    seed: Optional[int]
    max_turns: int
    timeout_seconds: int
    tool_capabilities: Tuple[str, ...]
    sandbox_profile: str
    network_policy: str
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvolutionExecutionPlan(FrozenModel):
    schema_version: Literal["ase/real-evolution-execution-plan/v1alpha1"] = (
        "ase/real-evolution-execution-plan/v1alpha1"
    )
    plan_id: UUID
    name: str
    proposal_job_id: UUID
    proposal_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_input_evidence_class: str
    base_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_count: int = Field(ge=3, le=5)
    total_candidate_count: int = Field(ge=6, le=8)
    agent: FrozenAgentIdentity
    datasets: EvolutionDatasetPlan
    stages: Tuple[EvolutionStagePlan, ...] = Field(min_length=4, max_length=4)
    total_agent_runs: int = Field(ge=1)
    total_estimated_cost_microusd: int = Field(ge=1)
    execution_order: Tuple[str, ...]
    capability_requirements: Tuple[str, ...]
    claim_limit: str
    real_calls_executed: Literal[False] = False
    locked_content_accessed: Literal[False] = False
    locked_authorization_checkpoint: Literal[True] = True
    evidence_class: Literal["execution_plan_only"] = "execution_plan_only"


class EvolutionExecutionPlanManifest(FrozenModel):
    schema_version: Literal["ase/real-evolution-execution-plan-manifest/v1alpha1"] = (
        "ase/real-evolution-execution-plan-manifest/v1alpha1"
    )
    plan_id: UUID
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: Dict[str, str]
    immutable: Literal[True] = True
    real_calls_executed: Literal[False] = False
    locked_content_accessed: Literal[False] = False


class EvolutionExecutionPlanResult(FrozenModel):
    plan: EvolutionExecutionPlan
    directory: Path
    manifest_path: Path
    plan_path: Path
    report_path: Path


class RealEvolutionExecutionPlanner:
    """Calculate exact run/cost envelopes without loading cases or executing an Agent."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def preflight(self, spec: EvolutionExecutionPlanSpec) -> EvolutionExecutionPlan:
        proposal_result = RealLLMProposalService(
            spec.proposal_directory.parent.parent
        ).verify(spec.proposal_directory)
        proposal = proposal_result.manifest
        real_spec = RealAgentEvidenceSpec.load(spec.real_agent_config_path)
        agent = self._agent_identity(real_spec, spec.real_agent_config_path)
        candidate_count = proposal.proposal_count + 3
        promoted_count = 3 + spec.search.promote_search_candidates
        if promoted_count > candidate_count:
            raise EvolutionExecutionPlanError(
                "promote_search_candidates exceeds the available generated candidates"
            )

        search_cases = spec.datasets.validation_search.case_count
        subset = spec.search.subset_size
        search_evaluations = candidate_count * subset + promoted_count * (
            search_cases - subset
        )
        counts: Tuple[Tuple[StageName, int], ...] = (
            ("validation_search", search_evaluations),
            ("regression_dev", 2 * spec.datasets.regression_dev.case_count),
            (
                "validation_confirm",
                2
                * spec.datasets.validation_confirm.case_count
                * spec.final.validation_confirm_repeats,
            ),
            (
                "locked_test",
                2 * spec.datasets.locked_test.case_count * spec.final.locked_test_repeats,
            ),
        )
        descriptor_map = {
            "validation_search": spec.datasets.validation_search,
            "regression_dev": spec.datasets.regression_dev,
            "validation_confirm": spec.datasets.validation_confirm,
            "locked_test": spec.datasets.locked_test,
        }
        budget_map = {
            "validation_search": spec.budgets.validation_search,
            "regression_dev": spec.budgets.regression_dev,
            "validation_confirm": spec.budgets.validation_confirm,
            "locked_test": spec.budgets.locked_test,
        }
        stages = []
        for ordinal, (stage, evaluations) in enumerate(counts, 1):
            runs = evaluations * 2
            cost = runs * real_spec.pricing.estimated_cost_per_run_microusd
            cap = budget_map[stage]
            if cap.max_agent_runs < runs or cap.max_cost_microusd < cost:
                raise EvolutionExecutionPlanError(
                    f"{stage} budget is below the required envelope: "
                    f"runs={runs}, cost_microusd={cost}"
                )
            pricing = real_spec.pricing
            stages.append(
                EvolutionStagePlan(
                    stage=stage,
                    ordinal=ordinal,
                    dataset_version_sha256=descriptor_map[stage].dataset_version_sha256,
                    case_count=descriptor_map[stage].case_count,
                    candidate_case_evaluations=evaluations,
                    agent_runs=runs,
                    estimated_cost_microusd=cost,
                    estimated_tokens=PlannedTokenUsage(
                        input_tokens=runs * pricing.estimated_input_tokens_per_run,
                        cache_hit_tokens=runs * pricing.estimated_cache_hit_tokens_per_run,
                        output_tokens=runs * pricing.estimated_output_tokens_per_run,
                    ),
                    budget_cap=cap,
                    locked_receipt_required=stage == "locked_test",
                )
            )

        proposal_manifest_bytes = (spec.proposal_directory / "proposal-manifest.json").read_bytes()
        proposal_sha = self._sha(proposal_manifest_bytes)
        semantic = {
            "name": spec.name,
            "proposal_manifest_sha256": proposal_sha,
            "agent": agent.model_dump(mode="json"),
            "datasets": spec.datasets.model_dump(mode="json"),
            "search": spec.search.model_dump(mode="json"),
            "final": spec.final.model_dump(mode="json"),
            "budgets": spec.budgets.model_dump(mode="json"),
        }
        plan_id = uuid5(NAMESPACE_URL, "agentskill-eval:evolution-plan:" + stable_sha256(semantic))
        return EvolutionExecutionPlan(
            plan_id=plan_id,
            name=spec.name,
            proposal_job_id=proposal.proposal_job_id,
            proposal_manifest_sha256=proposal_sha,
            proposal_input_evidence_class=proposal.input_evidence_class,
            base_skill_sha256=proposal.base_skill_sha256,
            proposal_count=proposal.proposal_count,
            total_candidate_count=candidate_count,
            agent=agent,
            datasets=spec.datasets,
            stages=tuple(stages),
            total_agent_runs=sum(stage.agent_runs for stage in stages),
            total_estimated_cost_microusd=sum(
                stage.estimated_cost_microusd for stage in stages
            ),
            execution_order=(
                "proposal_completed",
                "validation_search",
                "regression_dev",
                "validation_confirm",
                "locked_test_once",
                "human_review",
                "publish_skill_v2",
            ),
            capability_requirements=(
                "bind published Stage 2 DatasetVersions before execution",
                "run executable/version/hash and environment preflight before authorization",
                "wire Independent Final Evaluation to the paired real-Agent adapter",
                "obtain a separate explicit authorization for every paid stage",
                "reserve and consume the locked-test receipt exactly once",
            ),
            claim_limit=spec.claim_limit,
        )

    def prepare(self, spec: EvolutionExecutionPlanSpec) -> EvolutionExecutionPlanResult:
        plan = self.preflight(spec)
        directory = self.workspace / "evolution-execution-plans" / str(plan.plan_id)
        plan_bytes = canonical_json(plan.model_dump(mode="json")) + b"\n"
        report_bytes = self._markdown(plan).encode("utf-8")
        artifacts = {
            "execution-plan.json": self._sha(plan_bytes),
            "execution-plan.md": self._sha(report_bytes),
        }
        manifest = EvolutionExecutionPlanManifest(
            plan_id=plan.plan_id,
            plan_sha256=artifacts["execution-plan.json"],
            input_fingerprint=stable_sha256(
                {
                    "proposal_manifest_sha256": plan.proposal_manifest_sha256,
                    "agent_config_sha256": plan.agent.config_sha256,
                    "datasets": plan.datasets.model_dump(mode="json"),
                }
            ),
            artifacts=artifacts,
        )
        self._write_once(directory / "execution-plan.json", plan_bytes)
        self._write_once(directory / "execution-plan.md", report_bytes)
        self._write_once(
            directory / "execution-plan-manifest.json",
            canonical_json(manifest.model_dump(mode="json")) + b"\n",
        )
        return self.verify(directory)

    def verify(self, directory: Path) -> EvolutionExecutionPlanResult:
        root = directory.resolve(strict=True)
        try:
            manifest_path = root / "execution-plan-manifest.json"
            manifest = EvolutionExecutionPlanManifest.model_validate_json(
                manifest_path.read_bytes()
            )
            for name, expected in manifest.artifacts.items():
                path = root / name
                if not path.is_file() or self._sha(path.read_bytes()) != expected:
                    raise EvolutionExecutionPlanError(f"execution plan artifact mismatch: {name}")
            plan_path = root / "execution-plan.json"
            plan = EvolutionExecutionPlan.model_validate_json(plan_path.read_bytes())
        except EvolutionExecutionPlanError:
            raise
        except (OSError, ValueError) as exc:
            raise EvolutionExecutionPlanError(f"invalid execution plan {directory}: {exc}") from exc
        identity_mismatch = plan.plan_id != manifest.plan_id
        digest_mismatch = self._sha(plan_path.read_bytes()) != manifest.plan_sha256
        if identity_mismatch or digest_mismatch:
            raise EvolutionExecutionPlanError("execution plan manifest identity mismatch")
        if plan.real_calls_executed or plan.locked_content_accessed:
            raise EvolutionExecutionPlanError(
                "planning artifact claims forbidden execution or access"
            )
        return EvolutionExecutionPlanResult(
            plan=plan,
            directory=root,
            manifest_path=root / "execution-plan-manifest.json",
            plan_path=plan_path,
            report_path=root / "execution-plan.md",
        )

    @staticmethod
    def _agent_identity(spec: RealAgentEvidenceSpec, config_path: Path) -> FrozenAgentIdentity:
        return FrozenAgentIdentity(
            provider=spec.agent.provider,
            model=spec.agent.model,
            engine=spec.agent.engine,
            engine_version=spec.agent.engine_version,
            agent_executable_sha256=spec.agent.expected_sha256,
            runner_name=spec.runner.name,
            runner_version=spec.runner.expected_version,
            runner_executable_sha256=spec.runner.expected_sha256,
            temperature=spec.agent.temperature,
            seed=spec.agent.seed,
            max_turns=spec.agent.max_turns,
            timeout_seconds=spec.agent.timeout_seconds,
            tool_capabilities=spec.agent.tool_capabilities,
            sandbox_profile=spec.sandbox_profile,
            network_policy=spec.network_policy,
            config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        )

    def _write_once(self, path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise EvolutionExecutionPlanError(f"immutable execution plan changed: {path}")
            return
        self.writer.write(path, content)

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _markdown(plan: EvolutionExecutionPlan) -> str:
        rows = "\n".join(
            f"| {item.ordinal} | {item.stage} | {item.case_count} | "
            f"{item.candidate_case_evaluations} | {item.agent_runs} | "
            f"{item.estimated_cost_microusd} |"
            for item in plan.stages
        )
        requirements = "\n".join(f"- {item}" for item in plan.capability_requirements)
        return f"""# Real Skill Evolution Execution Plan

- Plan: `{plan.plan_id}`
- Provider/model: `{plan.agent.provider}` / `{plan.agent.model}`
- Proposals: {plan.proposal_count}; total search candidates: {plan.total_candidate_count}
- Real calls executed: **false**
- Locked content accessed: **false**

| # | Stage | Cases | Candidate-case evaluations | Agent runs | Estimated cost (microusd) |
|---:|---|---:|---:|---:|---:|
{rows}

Total Agent runs: **{plan.total_agent_runs}**.

Maximum estimated cost: **{plan.total_estimated_cost_microusd} microusd**.

## Required before execution

{requirements}

## Claim limit

{plan.claim_limit}
"""
