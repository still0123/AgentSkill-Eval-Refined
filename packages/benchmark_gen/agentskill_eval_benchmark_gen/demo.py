"""One-command, service-free P0 demo experiment orchestration."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import NAMESPACE_URL, UUID, uuid5

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
    RunnerResult,
    RunnerStatus,
)

_PACKAGE_ROOT = Path(__file__).resolve().parent
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def default_demo_dataset_root() -> Path:
    bundled = _PACKAGE_ROOT / "resources" / "python-review-demo"
    source = _REPOSITORY_ROOT / "examples/datasets/python-review-demo"
    return bundled if bundled.is_dir() else source


def default_demo_skill_root() -> Path:
    bundled = _PACKAGE_ROOT / "resources" / "python-review-v1"
    return bundled if bundled.is_dir() else _REPOSITORY_ROOT / "examples/skills/python-review-v1"


@dataclass(frozen=True)
class DemoRunConfig:
    workspace: Path
    dataset_root: Path
    skill_root: Path
    repeats: int = 3
    random_seed: int = 2026
    bootstrap_resamples: int = 10_000
    experiment_id: Optional[UUID] = None
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
        experiment_id = config.experiment_id or self._stable_experiment_id(
            dataset, skill_hash, config
        )
        runner_snapshot = RunnerSnapshot(
            name="mock",
            version="1",
            binary_sha256="0" * 64,
            config={"simulated": True},
        )
        baseline, treatment = self._variants(
            experiment_id,
            runner_snapshot,
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
                engine={"name": "mock"},
                environment={"type": "none"},
                timeout_seconds=config.timeout_seconds,
                max_turns=config.max_turns,
            ),
            VariantRuntimeSpec(
                variant_id=treatment.id,
                engine={"name": "mock"},
                environment={"type": "none"},
                skill_path=config.skill_root.resolve(strict=True),
                timeout_seconds=config.timeout_seconds,
                max_turns=config.max_turns,
            ),
        )
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
            simulated=True,
            dataset_sha256=dataset.dataset_sha256,
            case_count=case_count,
            report_paths=reports,
        )
        # Generate the standalone evidence pack at the workspace root.
        DemoEvidencePack.generate(config.workspace, experiment_id, result)
        return result

    @staticmethod
    def _variants(
        experiment_id: UUID,
        runner: RunnerSnapshot,
        skill_hash: str,
    ) -> Tuple[ExperimentVariant, ExperimentVariant]:
        agent_snapshot = AgentSnapshot(engine="mock", model="simulated")
        tool_snapshot = ToolSnapshot()
        sandbox_snapshot = SandboxSnapshot(profile="mock")
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
        return ExperimentManifest(
            id=experiment_id,
            name="Python review demo (SIMULATED)",
            code_revision="p0-demo-v1",
            created_at=config.created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
            dataset_version_id=dataset.dataset_id,
            dataset_sha256=dataset.dataset_sha256,
            protocol_snapshot={
                "demo_only": True,
                "evidence_mode": "simulated_fixture",
                "evaluation_split": ",".join(
                    sorted({item.metadata.split.value for item in dataset.cases})
                ),
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
    def _stable_experiment_id(
        dataset: LoadedDataset, skill_hash: str, config: DemoRunConfig
    ) -> UUID:
        run_key = _sha256_json(
            {
                "protocol": "portfolio-demo/v1",
                "dataset_sha256": dataset.dataset_sha256,
                "skill_sha256": skill_hash,
                "repeats": config.repeats,
                "random_seed": config.random_seed,
                "bootstrap_resamples": config.bootstrap_resamples,
                "timeout_seconds": config.timeout_seconds,
                "max_turns": config.max_turns,
            }
        )
        return uuid5(NAMESPACE_URL, f"agentskill-eval:portfolio-demo:{run_key}")

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

    PAIRED_SCHEMA = "ase/demo-paired-results/v1alpha1"
    INDEX_SCHEMA = "ase/demo-evidence-index/v1alpha1"

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
            "schema_version": DemoEvidencePack.PAIRED_SCHEMA,
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
            "invalid": wtl.get("invalid", 0),
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
        if trace_dir.exists():
            shutil.rmtree(trace_dir)
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
        indexed_runs: List[Dict[str, str]] = [
            {
                "run_id": str(run.id),
                "trace_path": f"trace/{run.id}.json",
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
            "schema_version": DemoEvidencePack.INDEX_SCHEMA,
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
        missing = [
            name
            for name in required_files
            if (workspace / name).is_symlink() or not (workspace / name).is_file()
        ]
        if missing:
            raise ValueError(f"evidence pack missing files: {', '.join(missing)}")
        if (workspace / "trace").is_symlink() or not (workspace / "trace").is_dir():
            raise ValueError("evidence pack is missing the trace directory")

        bundle_path = workspace / "audit-bundle.tar"
        try:
            audit_manifest = ReplayBundleWriter.verify(bundle_path)
        except Exception as exc:
            raise ValueError(f"audit bundle verification failed: {exc}") from exc

        index = json.loads((workspace / "evidence-index.json").read_text(encoding="utf-8"))
        if index.get("schema_version") != DemoEvidencePack.INDEX_SCHEMA:
            raise ValueError("unexpected Demo evidence-index schema")
        if index.get("evidence_class") != "SIMULATED_DEMO":
            raise ValueError("evidence class must be SIMULATED_DEMO")
        if index.get("simulated") is not True:
            raise ValueError("portfolio Demo evidence must declare simulated=true")

        experiment_id = _uuid_value(index.get("experiment_id"), "evidence experiment_id")
        if audit_manifest.experiment_id != experiment_id:
            raise ValueError("audit bundle belongs to another experiment")

        report_path = workspace / "experiment-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("report_schema_version") != "ase/report/v1alpha1":
            raise ValueError("unexpected report schema version")
        report_experiment = _dict_value(report.get("experiment"), "report experiment")
        report_statistics = _dict_value(report.get("statistics"), "report statistics")
        if _uuid_value(report_experiment.get("id"), "report experiment id") != experiment_id:
            raise ValueError("report belongs to another experiment")
        if _uuid_value(report_statistics.get("experiment_id"), "statistics experiment_id") != (
            experiment_id
        ):
            raise ValueError("statistics belong to another experiment")

        bundle_entries = {entry.path: entry for entry in audit_manifest.files}
        bundle_prefix = f"experiments/{experiment_id}/reports/"
        for filename in ("report.json", "report.html"):
            archive_entry = bundle_entries.get(bundle_prefix + filename)
            root_path = workspace / f"experiment-{filename}"
            if archive_entry is None or _sha256_file(root_path) != archive_entry.sha256:
                raise ValueError(f"{root_path.name} is not anchored by the audit bundle")

        paired = json.loads((workspace / "paired-results.json").read_text(encoding="utf-8"))
        expected_paired = _paired_results_from_report(report)
        if paired != expected_paired:
            raise ValueError("paired-results.json does not match the audited experiment report")

        expected_hashes = _lineage_hashes_from_report(report)
        if index.get("hashes") != expected_hashes:
            raise ValueError("evidence lineage hashes do not match the audited report")
        if index.get("total_runs") != paired["logical_runs"]:
            raise ValueError("evidence index total_runs does not match paired results")
        if index.get("invalid_runs") != paired["invalid_runs"]:
            raise ValueError("evidence index invalid_runs does not match paired results")

        trace_data = _dict_value(report.get("trace_intelligence"), "trace intelligence")
        traces = _list_value(trace_data.get("traces"), "report traces")
        trace_run_ids = {
            _string_value(_dict_value(item, "trace").get("run_id"), "trace run_id")
            for item in traces
        }
        indexed_runs = _list_value(index.get("runs"), "evidence runs")
        expected_runs = sorted(
            (
                {"run_id": run_id, "trace_path": f"trace/{run_id}.json"}
                for run_id in trace_run_ids
            ),
            key=lambda item: item["run_id"],
        )
        normalized_runs = sorted(
            (_dict_value(item, "indexed run") for item in indexed_runs),
            key=lambda item: _string_value(item.get("run_id"), "indexed run_id"),
        )
        if normalized_runs != expected_runs:
            raise ValueError("evidence run index does not match audited traces")

        expected_files = {
            "audit-bundle.tar",
            "experiment-report.html",
            "experiment-report.json",
            "paired-results.json",
            "skill-diff.patch",
            *(f"trace/{run_id}.json" for run_id in trace_run_ids),
        }
        actual_trace_files = {
            path.relative_to(workspace).as_posix()
            for path in (workspace / "trace").glob("*.json")
        }
        if actual_trace_files != expected_files - {
            "audit-bundle.tar",
            "experiment-report.html",
            "experiment-report.json",
            "paired-results.json",
            "skill-diff.patch",
        }:
            raise ValueError("trace directory does not match the audited report")

        file_entries = _list_value(index.get("files"), "evidence files")
        indexed_paths: Dict[str, Dict[str, Any]] = {}
        for raw_entry in file_entries:
            entry = _dict_value(raw_entry, "evidence file")
            relative = _safe_relative_path(entry.get("path"))
            relative_text = relative.as_posix()
            if relative_text in indexed_paths:
                raise ValueError(f"duplicate evidence path: {relative_text}")
            indexed_paths[relative_text] = entry
            path = workspace.joinpath(*relative.parts)
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"indexed evidence file is missing: {relative_text}")
            if path.stat().st_size != entry.get("size_bytes"):
                raise ValueError(f"evidence size mismatch: {relative_text}")
            if _sha256_file(path) != entry.get("sha256"):
                raise ValueError(f"evidence digest mismatch: {relative_text}")
        if set(indexed_paths) != expected_files:
            raise ValueError("evidence file index does not match the required file set")

        html_content = (workspace / "experiment-report.html").read_text(encoding="utf-8")
        if "SIMULATED DEMO" not in html_content and "SIMULATED" not in html_content:
            raise ValueError("HTML report must contain SIMULATED marker")

        return {
            "valid": True,
            "experiment_id": paired.get("experiment_id"),
            "total_runs": paired.get("logical_runs"),
            "invalid_runs": paired.get("invalid_runs"),
            "simulated": True,
            "evidence_class": "SIMULATED_DEMO",
            "dataset_sha256": paired.get("dataset_sha256"),
            "audit_bundle_verified": True,
            "wtl": {
                "win": paired.get("win", 0),
                "tie_positive": paired.get("tie_positive", 0),
                "tie_negative": paired.get("tie_negative", 0),
                "loss": paired.get("loss", 0),
                "invalid": paired.get("invalid", 0),
            },
        }


def _paired_results_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    experiment = _dict_value(report.get("experiment"), "report experiment")
    statistics = _dict_value(report.get("statistics"), "report statistics")
    protocol = _dict_value(experiment.get("protocol_snapshot"), "protocol snapshot")
    wtl = _dict_value(statistics.get("wtl"), "W/T/L summary")
    cases = _list_value(statistics.get("cases"), "case comparisons")
    variants = _list_value(statistics.get("variants"), "variant summaries")
    invalid_runs = sum(
        _int_value(_dict_value(item, "variant summary").get("invalid_runs"), "invalid_runs")
        for item in variants
    )
    logical_runs = _int_value(statistics.get("run_count"), "run_count")
    return {
        "schema_version": DemoEvidencePack.PAIRED_SCHEMA,
        "experiment_id": _string_value(experiment.get("id"), "experiment id"),
        "logical_runs": logical_runs,
        "completed_runs": logical_runs - invalid_runs,
        "invalid_runs": invalid_runs,
        "simulated": True,
        "dataset_sha256": _string_value(experiment.get("dataset_sha256"), "dataset sha256"),
        "case_count": _int_value(statistics.get("case_count"), "case_count"),
        "repeats": _int_value(protocol.get("repeats"), "repeats"),
        "win": _int_value(wtl.get("win"), "win"),
        "tie_positive": _int_value(wtl.get("tie_positive"), "tie_positive"),
        "tie_negative": _int_value(wtl.get("tie_negative"), "tie_negative"),
        "loss": _int_value(wtl.get("loss"), "loss"),
        "invalid": _int_value(wtl.get("invalid", 0), "invalid"),
        "cases": [
            {
                "case_id": _dict_value(item, "case comparison").get("case_id"),
                "classification": _dict_value(item, "case comparison").get("classification"),
                "control_pass_rate": _dict_value(item, "case comparison").get(
                    "control_pass_rate"
                ),
                "treatment_pass_rate": _dict_value(item, "case comparison").get(
                    "treatment_pass_rate"
                ),
                "absolute_gain": _dict_value(item, "case comparison").get("absolute_gain"),
            }
            for item in cases
        ],
    }


def _lineage_hashes_from_report(report: Dict[str, Any]) -> Dict[str, str]:
    experiment = _dict_value(report.get("experiment"), "report experiment")
    variants = _list_value(report.get("variants"), "report variants")
    treatment = next(
        (
            _dict_value(item, "variant")
            for item in variants
            if _dict_value(item, "variant").get("skill_snapshot") is not None
        ),
        None,
    )
    if treatment is None:
        raise ValueError("audited report is missing its treatment Skill")
    skill = _dict_value(treatment.get("skill_snapshot"), "Skill snapshot")
    runner = _dict_value(treatment.get("runner_snapshot"), "Runner snapshot")
    sandbox = _dict_value(treatment.get("sandbox_snapshot"), "Sandbox snapshot")
    return {
        "dataset_sha256": _string_value(
            experiment.get("dataset_sha256"), "dataset sha256"
        ),
        "skill_sha256": _string_value(skill.get("content_sha256"), "Skill sha256"),
        "runner_sha256": _string_value(runner.get("binary_sha256"), "Runner sha256"),
        "environment_sha256": _sha256_json({"runner": runner, "sandbox": sandbox}),
    }


def _dict_value(value: object, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _list_value(value: object, name: str) -> List[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _string_value(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _int_value(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _uuid_value(value: object, name: str) -> UUID:
    try:
        return UUID(_string_value(value, name))
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _safe_relative_path(value: object) -> PurePosixPath:
    text = _string_value(value, "evidence path")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        raise ValueError(f"unsafe evidence path: {text}")
    return path


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
