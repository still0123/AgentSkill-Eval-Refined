"""One-command, service-free P0 demo experiment orchestration."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import yaml

from agentskill_eval_benchmark_gen.dataset import DatasetLoader, LoadedDataset
from agentskill_eval_contracts import (
    AgentSnapshot,
    ExperimentManifest,
    ExperimentStatus,
    ExperimentVariant,
    RunnerSnapshot,
    SandboxSnapshot,
    SkillSnapshot,
    ToolSnapshot,
    VariantReference,
    VariantRole,
)
from agentskill_eval_experiment import (
    AnalysisConfig,
    ExecutionRecord,
    ExperimentAnalyzer,
    LocalExperimentExecutor,
    LocalExperimentPlanner,
    LocalExperimentStore,
    StaticReportPaths,
    StaticReportWriter,
    VariantRuntimeSpec,
)
from agentskill_eval_runner_adapters import (
    ExitReason,
    MockRunnerAdapter,
    RunnerAdapter,
    RunnerResult,
    RunnerStatus,
    SkillUpRunnerAdapter,
    discover_skill_up_binary,
)


class DemoMode(str, Enum):
    MOCK = "mock"
    SKILL_UP = "skill-up"


@dataclass(frozen=True)
class DemoRunConfig:
    workspace: Path
    dataset_root: Path
    skill_root: Path
    mode: DemoMode = DemoMode.MOCK
    repeats: int = 3
    random_seed: int = 2026
    bootstrap_resamples: int = 10_000
    experiment_id: Optional[UUID] = None
    runner_binary: Optional[Path] = None
    engine: str = "codex"
    model_provider: str = "openai"
    model_name: Optional[str] = None
    inherited_secret_env: Tuple[str, ...] = ()
    timeout_seconds: int = 300
    max_turns: int = 10


@dataclass(frozen=True)
class DemoRunResult:
    experiment_id: UUID
    control_variant_id: UUID
    treatment_variant_id: UUID
    logical_runs: int
    completed_runs: int
    invalid_runs: int
    simulated: bool
    dataset_sha256: str
    report_paths: StaticReportPaths


class DemoExperimentRunner:
    """Build and run the frozen 12×2×3 demo without service dependencies."""

    async def run(
        self,
        config: DemoRunConfig,
        progress_sink: Optional[Callable[[ExecutionRecord, int, int], None]] = None,
    ) -> DemoRunResult:
        if config.repeats < 1:
            raise ValueError("repeats must be positive")
        if config.bootstrap_resamples < 1:
            raise ValueError("bootstrap_resamples must be positive")
        dataset = DatasetLoader().load(config.dataset_root)
        if not dataset.manifest.demo_only:
            raise ValueError("demo run only accepts a dataset declared demo_only")
        skill_hash = self._verified_skill_hash(config.skill_root)
        experiment_id = config.experiment_id or uuid4()
        adapter, runner_snapshot, engine = self._runtime(config)
        baseline, treatment = self._variants(
            experiment_id,
            runner_snapshot,
            config,
            skill_hash,
        )
        experiment = self._experiment(
            experiment_id,
            dataset,
            baseline,
            treatment,
            config,
        )
        runtimes = (
            VariantRuntimeSpec(
                variant_id=baseline.id,
                engine=engine,
                environment={"type": "none"},
                timeout_seconds=config.timeout_seconds,
                max_turns=config.max_turns,
                secret_env=self._secret_env(config.inherited_secret_env),
            ),
            VariantRuntimeSpec(
                variant_id=treatment.id,
                engine=engine,
                environment={"type": "none"},
                skill_path=config.skill_root.resolve(strict=True),
                timeout_seconds=config.timeout_seconds,
                max_turns=config.max_turns,
                secret_env=self._secret_env(config.inherited_secret_env),
            ),
        )
        if config.mode == DemoMode.MOCK:
            adapter = self._mock_adapter(dataset, baseline.id, treatment.id)
        store = LocalExperimentStore(config.workspace)
        planner = LocalExperimentPlanner(store)
        plan = planner.build(
            experiment,
            (baseline, treatment),
            runtimes,
            dataset.execution_specs(),
            repeats=config.repeats,
            random_seed=config.random_seed,
        )
        planner.persist(plan)
        execution = await LocalExperimentExecutor(
            store, adapter, progress_sink=progress_sink
        ).execute(plan)
        statistics = ExperimentAnalyzer(store).analyze(
            experiment_id,
            AnalysisConfig(
                control_variant_id=baseline.id,
                treatment_variant_id=treatment.id,
                bootstrap_resamples=config.bootstrap_resamples,
                bootstrap_seed=config.random_seed,
                # Synthetic groups exercise the method but do not support inference claims.
                min_independent_groups=50,
            ),
        )
        reports = StaticReportWriter(store).write(experiment_id, statistics)
        return DemoRunResult(
            experiment_id=experiment_id,
            control_variant_id=baseline.id,
            treatment_variant_id=treatment.id,
            logical_runs=len(plan.blocks) * 2,
            completed_runs=execution.completed_runs,
            invalid_runs=execution.invalid_runs,
            simulated=config.mode == DemoMode.MOCK,
            dataset_sha256=dataset.dataset_sha256,
            report_paths=reports,
        )

    @staticmethod
    def _runtime(
        config: DemoRunConfig,
    ) -> Tuple[RunnerAdapter, RunnerSnapshot, Mapping[str, object]]:
        if config.mode == DemoMode.MOCK:
            mock_adapter: RunnerAdapter = MockRunnerAdapter()
            snapshot = RunnerSnapshot(
                name="mock",
                version="1",
                binary_sha256="0" * 64,
                config={"simulated": True},
            )
            return mock_adapter, snapshot, {"name": "mock"}
        binary = config.runner_binary or discover_skill_up_binary()
        if binary is None:
            raise ValueError("pinned skill-up binary was not found")
        skill_up_adapter = SkillUpRunnerAdapter(binary)
        skill_up_adapter.verify_binary()
        snapshot = RunnerSnapshot(
            name="skill-up",
            version=skill_up_adapter.compatibility.version,
            binary_sha256=skill_up_adapter.compatibility.binary_sha256,
            config={"upstream_benchmark_enabled": False},
        )
        model: Dict[str, str] = {"provider": config.model_provider}
        if config.model_name:
            model["name"] = config.model_name
        engine: Mapping[str, object] = {"name": config.engine, "model": model}
        return skill_up_adapter, snapshot, engine

    @staticmethod
    def _variants(
        experiment_id: UUID,
        runner: RunnerSnapshot,
        config: DemoRunConfig,
        skill_hash: str,
    ) -> Tuple[ExperimentVariant, ExperimentVariant]:
        agent_snapshot = AgentSnapshot(
            engine="mock" if config.mode == DemoMode.MOCK else config.engine,
            model=(
                "simulated"
                if config.mode == DemoMode.MOCK
                else config.model_name or "engine-default"
            ),
        )
        tool_snapshot = ToolSnapshot()
        sandbox_snapshot = SandboxSnapshot(
            profile="mock" if config.mode == DemoMode.MOCK else "runner_default"
        )
        baseline = ExperimentVariant(
            id=uuid5(experiment_id, "variant:without-skill"),
            experiment_id=experiment_id,
            name="without-skill",
            role=VariantRole.BASELINE,
            runner_snapshot=runner,
            agent_snapshot=agent_snapshot,
            tool_snapshot=tool_snapshot,
            sandbox_snapshot=sandbox_snapshot,
        )
        treatment = ExperimentVariant(
            id=uuid5(experiment_id, "variant:python-review-v1"),
            experiment_id=experiment_id,
            name="python-review-v1",
            role=VariantRole.TREATMENT,
            runner_snapshot=runner,
            agent_snapshot=agent_snapshot,
            skill_snapshot=SkillSnapshot(
                skill_id=uuid5(NAMESPACE_URL, "agentskill-eval:skill:python-review"),
                version_id=uuid5(NAMESPACE_URL, f"agentskill-eval:skill-version:{skill_hash}"),
                name="python-review-v1",
                version="1.0.0",
                content_sha256=skill_hash,
                injection_mode="native_install",
            ),
            tool_snapshot=tool_snapshot,
            sandbox_snapshot=sandbox_snapshot,
        )
        return baseline, treatment

    @staticmethod
    def _experiment(
        experiment_id: UUID,
        dataset: LoadedDataset,
        baseline: ExperimentVariant,
        treatment: ExperimentVariant,
        config: DemoRunConfig,
    ) -> ExperimentManifest:
        simulated = config.mode == DemoMode.MOCK
        return ExperimentManifest(
            id=experiment_id,
            name=(
                "Python review demo (SIMULATED)"
                if simulated
                else f"Python review demo ({config.engine}/{config.model_name or 'engine-default'})"
            ),
            code_revision="p0-demo-v1",
            dataset_version_id=dataset.dataset_id,
            dataset_sha256=dataset.dataset_sha256,
            protocol_snapshot={
                "demo_only": True,
                "evidence_mode": "simulated_fixture" if simulated else "observed_agent",
                "repeats": config.repeats,
                "random_seed": config.random_seed,
                "skill_protocol": "natural_trigger",
            },
            statistics_plan={
                "primary": "assignment_based_absolute_gain",
                "weighting": "equal_group",
                "inference": "descriptive_only",
            },
            budget_snapshot={"max_runs": len(dataset.cases) * 2 * config.repeats},
            variants=tuple(
                VariantReference(
                    variant_id=variant.id,
                    variant_sha256=variant.variant_sha256,
                    manifest_path=f"variants/{variant.id}.json",
                )
                for variant in (baseline, treatment)
            ),
            status=ExperimentStatus.FROZEN,
        )

    @staticmethod
    def _verified_skill_hash(skill_root: Path) -> str:
        root = skill_root.resolve(strict=True)
        skill_file = root / "SKILL.md"
        metadata_file = root / "metadata.yaml"
        if not skill_file.is_file() or skill_file.is_symlink() or not metadata_file.is_file():
            raise ValueError("demo Skill requires regular SKILL.md and metadata.yaml files")
        metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or not isinstance(metadata.get("skill_md_sha256"), str):
            raise ValueError("demo Skill metadata must freeze skill_md_sha256")
        digest = hashlib.sha256(skill_file.read_bytes()).hexdigest()
        if digest != metadata["skill_md_sha256"]:
            raise ValueError("demo Skill content does not match metadata hash")
        return digest

    @staticmethod
    def _secret_env(names: Tuple[str, ...]) -> Mapping[str, str]:
        values: Dict[str, str] = {}
        for name in names:
            if not name or name in values:
                raise ValueError("inherited secret environment names must be unique and non-empty")
            value = os.environ.get(name)
            if value is None:
                raise ValueError(f"requested secret environment variable is not set: {name}")
            values[name] = value
        return values

    @staticmethod
    def _mock_adapter(
        dataset: LoadedDataset,
        baseline_id: UUID,
        treatment_id: UUID,
    ) -> MockRunnerAdapter:
        baseline_pass = {
            "python-off-by-one",
            "python-clean-optional",
            "python-clean-context-manager",
            "python-comment-bug-distractor",
            "python-deprecated-dir-distractor",
            "python-retry-budget",
        }
        treatment_fail = {"python-clean-context-manager", "python-normalization-contract"}
        results: Dict[str, RunnerResult] = {}
        for case in dataset.cases:
            case_id = case.metadata.case_id
            for variant_id, passed, tokens, duration in (
                (baseline_id, case_id in baseline_pass, (120, 30), 80),
                (treatment_id, case_id not in treatment_fail, (220, 40), 100),
            ):
                status = RunnerStatus.PASS if passed else RunnerStatus.FAIL
                results[f"{case_id}:{variant_id}"] = RunnerResult(
                    execution_id="configured-at-runtime",
                    case_id=case_id,
                    status=status,
                    exit_reason=(ExitReason.COMPLETED if passed else ExitReason.CASE_FAILED),
                    process_exit_code=0,
                    duration_ms=duration,
                    turns=2,
                    input_tokens=tokens[0],
                    output_tokens=tokens[1],
                    final_message="SIMULATED fixture outcome; not an Agent response.",
                    grading={"score": 1.0 if passed else 0.0, "simulated": True},
                )
        return MockRunnerAdapter(results)
