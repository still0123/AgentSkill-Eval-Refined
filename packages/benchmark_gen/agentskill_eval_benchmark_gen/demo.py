"""One-command, service-free P0 demo experiment orchestration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import yaml

from agentskill_eval_benchmark_gen.dataset import DatasetLoader, LoadedDataset
from agentskill_eval_contracts import (
    AgentSnapshot,
    EvaluationOutcome,
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
    ExperimentLayout,
    LocalExperimentExecutor,
    LocalExperimentPlanner,
    LocalExperimentStore,
    ReplayBundleWriter,
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
    created_at: Optional[datetime] = None


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
    case_count: int
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
        case_count = len(dataset.cases)
        result = DemoRunResult(
            experiment_id=experiment_id,
            control_variant_id=baseline.id,
            treatment_variant_id=treatment.id,
            logical_runs=len(plan.blocks) * 2,
            completed_runs=execution.completed_runs,
            invalid_runs=execution.invalid_runs,
            simulated=config.mode == DemoMode.MOCK,
            dataset_sha256=dataset.dataset_sha256,
            case_count=case_count,
            report_paths=reports,
        )
        # Generate the standalone evidence pack at the workspace root.
        DemoEvidencePack.generate(config.workspace, experiment_id, result)
        return result

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
            created_at=config.created_at or datetime.now(timezone.utc),
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


class DemoEvidencePack:
    """Generate a standalone portfolio evidence pack from a completed demo experiment."""

    @staticmethod
    def generate(
        workspace: Path,
        experiment_id: UUID,
        result: DemoRunResult,
    ) -> None:
        store = LocalExperimentStore(workspace)
        layout = ExperimentLayout(workspace, experiment_id)
        report_json = layout.reports / "report.json"
        report_html = layout.reports / "report.html"

        if not report_json.is_file() or not report_html.is_file():
            raise ValueError("demo experiment reports not found; run the experiment first")

        # 1. Copy experiment-report.json / .html to workspace root
        _copy_file(report_json, workspace / "experiment-report.json")
        _copy_file(report_html, workspace / "experiment-report.html")

        # 2. Build paired-results.json from the report
        bundle = json.loads(report_json.read_text(encoding="utf-8"))
        statistics = bundle.get("statistics", {})
        cases = statistics.get("cases", [])
        wtl = statistics.get("wtl", {})
        paired_results = {
            "experiment_id": str(experiment_id),
            "logical_runs": result.logical_runs,
            "completed_runs": result.completed_runs,
            "invalid_runs": result.invalid_runs,
            "simulated": result.simulated,
            "dataset_sha256": result.dataset_sha256,
            "case_count": result.case_count,
            "repeats": result.logical_runs // (result.case_count * 2) if result.case_count else 0,
            "win": wtl.get("win", 0),
            "tie_positive": wtl.get("tie_positive", 0),
            "tie_negative": wtl.get("tie_negative", 0),
            "loss": wtl.get("loss", 0),
            "cases": [
                {
                    "case_id": c.get("case_id"),
                    "classification": c.get("classification"),
                    "control_pass_rate": c.get("control_pass_rate"),
                    "treatment_pass_rate": c.get("treatment_pass_rate"),
                    "absolute_gain": c.get("absolute_gain"),
                }
                for c in cases
            ],
        }
        _write_json(workspace / "paired-results.json", paired_results)

        # 3. Generate the replayable audit bundle and presentation artifacts.
        runs = store.list_runs(experiment_id)
        bundle_path = workspace / "audit-bundle.tar"
        ReplayBundleWriter(store).write(experiment_id, bundle_path)
        _write_file(
            workspace / "skill-diff.patch",
            "# SIMULATED DEMO: skill-diff.patch\n"
            "# This is a placeholder. Real patches are generated\n"
            "# from actual Skill v1→v2 evolution in a real experiment.\n",
        )
        trace_dir = workspace / "trace"
        trace_dir.mkdir(parents=True, exist_ok=True)
        for run in runs:
            attempt = store.load_selected_attempt(experiment_id, run)
            if attempt is None:
                continue
            trace_path = layout.trace_manifest(run.id, attempt.attempt_no)
            if trace_path.is_file():
                _copy_file(trace_path, trace_dir / f"{run.id}.json")

        # 4. Freeze input lineage and every presentation-file digest.
        variants = store.list_variants(experiment_id)
        treatment = next(item for item in variants if item.skill_snapshot is not None)
        skill_snapshot = treatment.skill_snapshot
        if skill_snapshot is None:
            raise ValueError("demo treatment variant is missing its Skill snapshot")
        indexed_runs: List[Dict[str, Optional[str]]] = [
            {
                "run_id": str(run.id),
                "variant_id": str(run.variant_id),
                "outcome": run.evaluation_outcome.value if run.evaluation_outcome else None,
                "pair_block_id": str(run.pair_block_id),
            }
            for run in runs
        ]
        indexed_files = [
            _file_entry(path, workspace)
            for path in sorted(
                (
                    workspace / "experiment-report.json",
                    workspace / "experiment-report.html",
                    workspace / "paired-results.json",
                    workspace / "audit-bundle.tar",
                    workspace / "skill-diff.patch",
                    *trace_dir.glob("*.json"),
                ),
                key=lambda item: item.relative_to(workspace).as_posix(),
            )
        ]
        evidence_index = {
            "experiment_id": str(experiment_id),
            "simulated": result.simulated,
            "evidence_class": "SIMULATED_DEMO",
            "total_runs": len(runs),
            "invalid_runs": result.invalid_runs,
            "hashes": {
                "dataset_sha256": result.dataset_sha256,
                "skill_sha256": skill_snapshot.content_sha256,
                "runner_sha256": treatment.runner_snapshot.binary_sha256,
                "environment_sha256": _sha256_json(
                    {
                        "runner": treatment.runner_snapshot.model_dump(mode="json"),
                        "sandbox": treatment.sandbox_snapshot.model_dump(mode="json"),
                    }
                ),
            },
            "files": indexed_files,
            "runs": indexed_runs,
        }
        _write_json(workspace / "evidence-index.json", evidence_index)

    @staticmethod
    def verify(workspace: Path) -> Dict[str, object]:
        """Verify the integrity of a completed demo evidence pack."""
        required_files = [
            "experiment-report.json",
            "experiment-report.html",
            "paired-results.json",
            "evidence-index.json",
            "audit-bundle.tar",
            "skill-diff.patch",
        ]
        missing = [f for f in required_files if not (workspace / f).is_file()]
        if missing:
            raise ValueError(f"evidence pack missing files: {', '.join(missing)}")

        # Verify audit bundle
        bundle_path = workspace / "audit-bundle.tar"
        try:
            ReplayBundleWriter.verify(bundle_path)
        except Exception as exc:
            raise ValueError(f"audit bundle verification failed: {exc}") from exc

        # Verify evidence-index.json
        index = json.loads((workspace / "evidence-index.json").read_text(encoding="utf-8"))
        if index.get("evidence_class") != "SIMULATED_DEMO":
            raise ValueError("evidence class must be SIMULATED_DEMO")
        for entry in index.get("files", []):
            path = workspace / entry["path"]
            if not path.is_file():
                raise ValueError(f"indexed evidence file is missing: {entry['path']}")
            if path.stat().st_size != entry["size_bytes"]:
                raise ValueError(f"evidence size mismatch: {entry['path']}")
            if _sha256_file(path) != entry["sha256"]:
                raise ValueError(f"evidence digest mismatch: {entry['path']}")

        # Verify paired-results.json after its digest has been checked.
        paired = json.loads((workspace / "paired-results.json").read_text(encoding="utf-8"))
        if paired.get("invalid_runs", -1) != 0:
            raise ValueError(f"demo must have 0 invalid runs, got {paired.get('invalid_runs')}")

        # Verify experiment-report.json
        report = json.loads((workspace / "experiment-report.json").read_text(encoding="utf-8"))
        if report.get("report_schema_version") != "ase/report/v1alpha1":
            raise ValueError("unexpected report schema version")

        # Verify experiment-report.html is loadable
        html_content = (workspace / "experiment-report.html").read_text(encoding="utf-8")
        if "SIMULATED DEMO" not in html_content and "SIMULATED" not in html_content:
            raise ValueError("HTML report must contain SIMULATED marker")

        return {
            "valid": True,
            "experiment_id": paired.get("experiment_id"),
            "total_runs": paired.get("logical_runs"),
            "invalid_runs": paired.get("invalid_runs"),
            "simulated": paired.get("simulated", True),
            "evidence_class": "SIMULATED_DEMO",
            "dataset_sha256": paired.get("dataset_sha256"),
            "audit_bundle_verified": True,
            "wtl": {
                "win": paired.get("win", 0),
                "tie_positive": paired.get("tie_positive", 0),
                "tie_negative": paired.get("tie_negative", 0),
                "loss": paired.get("loss", 0),
            },
        }


class DemoExperimentVerifier:
    """Verify a completed demo experiment's integrity (legacy)."""

    @staticmethod
    def verify(workspace: Path) -> Dict[str, object]:
        store = LocalExperimentStore(workspace)
        exp_root = workspace / "experiments"
        if not exp_root.is_dir():
            raise ValueError("no experiments found in workspace")
        exp_dirs = sorted(exp_root.iterdir())
        if not exp_dirs:
            raise ValueError("no experiments found in workspace")
        experiment_id = UUID(exp_dirs[0].name)
        manifest = store.load_experiment(experiment_id)
        runs = store.list_runs(experiment_id)
        invalid = sum(1 for r in runs if r.evaluation_outcome == EvaluationOutcome.INVALID)
        return {
            "valid": True,
            "experiment_id": str(experiment_id),
            "total_runs": len(runs),
            "invalid_runs": invalid,
            "simulated": True,
            "dataset_sha256": manifest.dataset_sha256,
        }


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(payload: object) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _file_entry(path: Path, workspace: Path) -> Dict[str, object]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
