"""AgentSkill-Eval command-line interface."""

import asyncio
import json
from pathlib import Path
from typing import List, Optional
from uuid import UUID

import typer

from agentskill_eval_benchmark_gen import (
    AutomaticBenchmarkGenerator,
    BenchmarkGenerationSpec,
    BenchmarkStore,
    DatasetLoader,
    DemoExperimentRunner,
    DemoMode,
    DemoRunConfig,
)
from agentskill_eval_cli import __version__
from agentskill_eval_contracts import ReviewDecision, export_schema_bundle
from agentskill_eval_experiment import (
    AnalysisConfig,
    ExecutionRecord,
    ExperimentAnalyzer,
    LocalExperimentStore,
    ReplayBundleWriter,
    StaticReportWriter,
)
from agentskill_eval_mcp_lab import LabConfig, McpDataset, McpLabRunner, find_trace, load_report
from agentskill_eval_skill_optimizer import (
    BenchmarkGuidedSkillSearch,
    FinalEvaluationStore,
    IndependentFinalEvaluationSpec,
    IndependentFinalEvaluator,
    OptimizationSearchSpec,
    OptimizationStore,
)
from agentskill_eval_trace_intelligence import compare_traces

app = typer.Typer(
    name="agentskill-eval",
    help="Run reproducible Agent Skill evaluation and regression experiments.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
schema_app = typer.Typer(help="Inspect and export public data-contract schemas.")
app.add_typer(schema_app, name="schema")
storage_app = typer.Typer(help="Inspect and recover the service-free P0 manifest store.")
app.add_typer(storage_app, name="storage")
report_app = typer.Typer(help="Analyze a completed paired experiment and write static reports.")
app.add_typer(report_app, name="report")
dataset_app = typer.Typer(help="Validate and inspect curated evaluation datasets.")
app.add_typer(dataset_app, name="dataset")
demo_app = typer.Typer(help="Run the service-free P0 demonstration experiment.")
app.add_typer(demo_app, name="demo")
experiment_app = typer.Typer(help="Package and inspect persisted experiments.")
app.add_typer(experiment_app, name="experiment")
trace_app = typer.Typer(help="Inspect normalized traces and rule-based diagnoses.")
app.add_typer(trace_app, name="trace")
benchmark_app = typer.Typer(help="Generate, review, and publish audited benchmark candidates.")
app.add_typer(benchmark_app, name="benchmark")
optimize_app = typer.Typer(help="Search validation data for a frozen Skill candidate.")
app.add_typer(optimize_app, name="optimize")
final_app = typer.Typer(help="Evaluate a frozen base/winner pair on an independent split.")
app.add_typer(final_app, name="final")
mcp_app = typer.Typer(help="Validate and run auditable MCP tool-evaluation experiments.")
app.add_typer(mcp_app, name="mcp")
mcp_lab_app = typer.Typer(help="Run the deterministic offline MCP lab.")
mcp_app.add_typer(mcp_lab_app, name="lab")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Optional[bool] = typer.Option(  # noqa: B008
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed AgentSkill-Eval version and exit.",
    ),
) -> None:
    """Run reproducible Agent Skill evaluation and regression experiments."""


@app.command()
def version() -> None:
    """Show the installed AgentSkill-Eval version."""
    typer.echo(__version__)


@mcp_app.command("validate")
def mcp_validate(
    dataset: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
) -> None:
    """Validate a strict MCP evaluation dataset contract."""
    loaded = McpDataset.load(dataset, allowed_root=dataset.parent)
    typer.echo(
        json.dumps(
            {
                "name": loaded.name,
                "case_count": len(loaded.cases),
                "case_ids": [case.case_id for case in loaded.cases],
                "simulated": loaded.simulated,
            },
            sort_keys=True,
        )
    )


@mcp_lab_app.command("run")
def mcp_lab_run(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(..., "--workspace", file_okay=False),  # noqa: B008
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
) -> None:
    """Run the deterministic paired Mock MCP experiment."""
    loaded = LabConfig.load(config)
    if loaded.simulated and not allow_simulation:
        raise typer.BadParameter(
            "Mock MCP Lab requires --allow-simulation and cannot support real-agent claims",
            param_hint="--allow-simulation",
        )
    artifacts = McpLabRunner(workspace).run(loaded)
    typer.echo(
        json.dumps(
            {
                "experiment_id": str(artifacts.report.experiment_id),
                "report_json": str(artifacts.report_json),
                "report_html": str(artifacts.report_html),
                "simulated": artifacts.report.simulated,
                "claim_limit": artifacts.report.claim_limit,
            },
            sort_keys=True,
        )
    )


@mcp_app.command("report")
def mcp_report(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    experiment_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show one persisted MCP paired report."""
    typer.echo(load_report(workspace, experiment_id).model_dump_json(indent=2))


@mcp_app.command("trace")
def mcp_trace(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    run_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show one normalized MCP trace by run ID."""
    typer.echo(json.dumps(find_trace(workspace, run_id), ensure_ascii=False, sort_keys=True))


@final_app.command("evaluate")
def final_evaluate(
    spec_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Independent final-evaluation spec."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    allow_simulation: bool = typer.Option(  # noqa: B008
        False,
        "--allow-simulation",
        help="Required for simulated evaluators; results are never performance evidence.",
    ),
) -> None:
    """Run paired confirmation or one-shot locked-test evaluation."""
    spec = IndependentFinalEvaluationSpec.load(spec_path)
    if spec.evaluator.simulated and not allow_simulation:
        raise typer.BadParameter(
            "simulated evaluator requires --allow-simulation",
            param_hint="--allow-simulation",
        )
    result = IndependentFinalEvaluator(workspace).run(spec)
    typer.echo(
        json.dumps(
            {
                "absolute_gain": result.report.absolute_gain,
                "base_pass_rate": result.report.base_pass_rate,
                "decision": result.report.decision.value,
                "job_id": str(result.job.id),
                "loss_count": result.report.loss_count,
                "report_html": str(result.report_html),
                "report_json": str(result.report_json),
                "simulated": result.job.simulated,
                "stage": result.job.stage.value,
                "winner_pass_rate": result.report.winner_pass_rate,
            },
            sort_keys=True,
        )
    )


@final_app.command("status")
def final_status(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    job_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show one persisted independent final-evaluation report."""
    store = FinalEvaluationStore(workspace)
    job = store.load_job(job_id)
    report = store.load_report(job_id)
    typer.echo(
        json.dumps(
            {"job": job.model_dump(mode="json"), "report": report.model_dump(mode="json")},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@optimize_app.command("search")
def optimize_search(
    spec_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Frozen benchmark-guided search spec."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    allow_simulation: bool = typer.Option(  # noqa: B008
        False,
        "--allow-simulation",
        help="Required for simulated evaluators; results are never performance evidence.",
    ),
) -> None:
    """Run successive halving and freeze one validation-only Pareto winner."""
    spec = OptimizationSearchSpec.load(spec_path)
    if spec.evaluator.type == "simulated_keyword" and not allow_simulation:
        raise typer.BadParameter(
            "simulated evaluator requires --allow-simulation",
            param_hint="--allow-simulation",
        )
    result = BenchmarkGuidedSkillSearch(workspace).run(spec)
    typer.echo(
        json.dumps(
            {
                "candidate_count": len(result.candidates),
                "evaluations_used": result.job.evaluations_used,
                "job_id": str(result.job.id),
                "locked_test_accessed": result.job.locked_test_accessed,
                "report_html": str(result.report_html),
                "report_json": str(result.report_json),
                "simulated": result.job.simulated,
                "status": result.job.status.value,
                "winner_id": str(result.winner.id),
                "winner_name": result.winner.name,
            },
            sort_keys=True,
        )
    )


@optimize_app.command("status")
def optimize_status(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    job_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show the frozen optimization job and complete candidate lineage."""
    store = OptimizationStore(workspace)
    job = store.load_job(job_id)
    typer.echo(
        json.dumps(
            {
                "job": job.model_dump(mode="json"),
                "candidates": [
                    item.model_dump(mode="json") for item in store.list_candidates(job)
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@benchmark_app.command("generate")
def generate_benchmark(
    spec_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Pinned local Git-history generation spec."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
) -> None:
    """Reconstruct, repeatedly verify, and deduplicate benchmark candidates."""
    result = AutomaticBenchmarkGenerator(workspace).generate(
        BenchmarkGenerationSpec.load(spec_path)
    )
    typer.echo(
        json.dumps(
            {
                "job_id": str(result.job.id),
                "status": result.job.status.value,
                "candidates": [
                    {"id": str(item.id), "key": item.key, "status": item.status.value}
                    for item in result.candidates
                ],
            },
            sort_keys=True,
        )
    )


@benchmark_app.command("status")
def benchmark_status(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    job_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show candidate lifecycles, gates, and retained rejection reasons."""
    store = BenchmarkStore(workspace)
    job = store.load_job(job_id)
    typer.echo(
        json.dumps(
            {
                "job": job.model_dump(mode="json"),
                "candidates": [item.model_dump(mode="json") for item in store.list_candidates(job)],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@benchmark_app.command("review")
def review_benchmark_candidate(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    job_id: UUID = typer.Argument(...),  # noqa: B008
    candidate_id: UUID = typer.Argument(...),  # noqa: B008
    reviewer: str = typer.Option(..., "--reviewer"),  # noqa: B008
    approve: bool = typer.Option(False, "--approve/--reject"),  # noqa: B008
    reason: str = typer.Option(..., "--reason"),  # noqa: B008
) -> None:
    """Record an explicit human approval or rejection decision."""
    decision = ReviewDecision.APPROVED if approve else ReviewDecision.REJECTED
    candidate = AutomaticBenchmarkGenerator(workspace).review(
        job_id, candidate_id, reviewer, decision, reason
    )
    typer.echo(json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))


@benchmark_app.command("publish")
def publish_benchmark(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    job_id: UUID = typer.Argument(...),  # noqa: B008
    publisher: str = typer.Option(..., "--publisher"),  # noqa: B008
) -> None:
    """Publish approved candidates as an immutable DatasetVersion."""
    version, path = AutomaticBenchmarkGenerator(workspace).publish(job_id, publisher)
    typer.echo(
        json.dumps(
            {
                "dataset_version_id": str(version.id),
                "content_sha256": version.content_sha256,
                "path": str(path),
            },
            sort_keys=True,
        )
    )


@dataset_app.command("validate")
def validate_dataset(
    dataset_root: Path = typer.Argument(  # noqa: B008
        ...,
        help="Directory containing dataset.yaml and evals/.",
        exists=True,
        file_okay=False,
    ),
) -> None:
    """Validate sidecars, fixtures, graders, hashes, and category gates."""
    dataset = DatasetLoader().load(dataset_root)
    typer.echo(
        json.dumps(
            {
                "case_count": len(dataset.cases),
                "case_ids": [item.metadata.case_id for item in dataset.cases],
                "category_counts": {
                    category.value: count
                    for category, count in sorted(
                        dataset.category_counts.items(), key=lambda item: item[0].value
                    )
                },
                "dataset_id": str(dataset.dataset_id),
                "dataset_sha256": dataset.dataset_sha256,
                "demo_only": dataset.manifest.demo_only,
                "independence_groups": list(dataset.independence_groups),
                "name": dataset.manifest.name,
                "version": dataset.manifest.version,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@experiment_app.command("bundle")
def bundle_experiment(
    workspace: Path = typer.Argument(  # noqa: B008
        ..., exists=True, file_okay=False, help="AgentSkill-Eval workspace root."
    ),
    experiment_id: UUID = typer.Argument(..., help="Experiment UUID."),  # noqa: B008
    destination: Path = typer.Argument(  # noqa: B008
        ..., dir_okay=False, help="Destination deterministic .tar file."
    ),
) -> None:
    """Create an audit/reanalysis bundle without external runtime state."""
    result = ReplayBundleWriter(LocalExperimentStore(workspace)).write(experiment_id, destination)
    typer.echo(
        json.dumps(
            {
                "bundle": str(result.path),
                "bundle_sha256": result.manifest.bundle_sha256,
                "experiment_id": str(result.manifest.experiment_id),
                "file_count": len(result.manifest.files),
                "scope": result.manifest.scope,
            },
            sort_keys=True,
        )
    )


@experiment_app.command("verify-bundle")
def verify_experiment_bundle(
    bundle: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Replay bundle to verify."
    ),
) -> None:
    """Verify member safety, file set, sizes, and SHA-256 digests."""
    manifest = ReplayBundleWriter.verify(bundle)
    typer.echo(
        json.dumps(
            {
                "bundle_sha256": manifest.bundle_sha256,
                "experiment_id": str(manifest.experiment_id),
                "file_count": len(manifest.files),
                "scope": manifest.scope,
                "valid": True,
            },
            sort_keys=True,
        )
    )


@trace_app.command("show")
def show_trace(
    workspace: Path = typer.Argument(  # noqa: B008
        ..., exists=True, file_okay=False, help="AgentSkill-Eval workspace root."
    ),
    experiment_id: UUID = typer.Argument(..., help="Experiment UUID."),  # noqa: B008
    run_id: UUID = typer.Argument(..., help="Logical Run UUID."),  # noqa: B008
) -> None:
    """Print the selected Attempt trace and diagnosis as JSON."""
    store = LocalExperimentStore(workspace)
    run = store.load_run(experiment_id, run_id)
    attempt = store.load_selected_attempt(experiment_id, run)
    if attempt is None:
        raise typer.BadParameter("run has no selected Attempt", param_hint="run_id")
    trace = store.load_trace_manifest(experiment_id, run_id, attempt.attempt_no)
    diagnosis = store.load_failure_diagnosis(experiment_id, run_id, attempt.attempt_no)
    typer.echo(
        json.dumps(
            {
                "diagnosis": diagnosis.model_dump(mode="json", round_trip=True),
                "trace": trace.model_dump(mode="json", round_trip=True),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@trace_app.command("compare")
def compare_trace_pair(
    workspace: Path = typer.Argument(  # noqa: B008
        ..., exists=True, file_okay=False, help="AgentSkill-Eval workspace root."
    ),
    experiment_id: UUID = typer.Argument(..., help="Experiment UUID."),  # noqa: B008
    pair_block_id: UUID = typer.Argument(..., help="PairBlock UUID."),  # noqa: B008
    control_variant_id: UUID = typer.Option(..., "--control"),  # noqa: B008
    treatment_variant_id: UUID = typer.Option(..., "--treatment"),  # noqa: B008
) -> None:
    """Compare normalized event-kind sequences for one paired block."""
    store = LocalExperimentStore(workspace)
    by_variant = {
        run.variant_id: run
        for run in store.list_runs(experiment_id)
        if run.pair_block_id == pair_block_id
    }
    try:
        control_run = by_variant[control_variant_id]
        treatment_run = by_variant[treatment_variant_id]
    except KeyError as exc:
        raise typer.BadParameter(
            "pair block does not contain both requested variants",
            param_hint="pair_block_id",
        ) from exc
    control_attempt = store.load_selected_attempt(experiment_id, control_run)
    treatment_attempt = store.load_selected_attempt(experiment_id, treatment_run)
    if control_attempt is None or treatment_attempt is None:
        raise typer.BadParameter("both runs require selected Attempts", param_hint="pair_block_id")
    diff = compare_traces(
        pair_block_id,
        store.load_trace_manifest(experiment_id, control_run.id, control_attempt.attempt_no),
        store.load_trace_manifest(experiment_id, treatment_run.id, treatment_attempt.attempt_no),
    )
    typer.echo(
        json.dumps(
            diff.model_dump(mode="json", round_trip=True),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@demo_app.command("run")
def run_demo(
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"),
        "--workspace",
        help="Workspace for immutable manifests, evidence, and reports.",
        file_okay=False,
    ),
    dataset_root: Path = typer.Option(  # noqa: B008
        Path("examples/datasets/python-review-demo"),
        "--dataset",
        help="Curated demo dataset root.",
        exists=True,
        file_okay=False,
    ),
    skill_root: Path = typer.Option(  # noqa: B008
        Path("examples/skills/python-review-v1"),
        "--skill",
        help="Versioned demo Skill root.",
        exists=True,
        file_okay=False,
    ),
    mode: DemoMode = typer.Option(DemoMode.MOCK, "--mode"),  # noqa: B008
    repeats: int = typer.Option(3, "--repeats", min=1),  # noqa: B008
    random_seed: int = typer.Option(2026, "--random-seed"),  # noqa: B008
    bootstrap_resamples: int = typer.Option(  # noqa: B008
        10_000, "--bootstrap-resamples", min=1
    ),
    engine: str = typer.Option("codex", "--engine"),  # noqa: B008
    model_provider: str = typer.Option("openai", "--model-provider"),  # noqa: B008
    model_name: Optional[str] = typer.Option(None, "--model"),  # noqa: B008
    runner_binary: Optional[Path] = typer.Option(  # noqa: B008
        None,
        "--runner-bin",
        exists=True,
        dir_okay=False,
        help="Pinned skill-up binary; managed installation is auto-discovered.",
    ),
    inherit_secret_env: Optional[List[str]] = typer.Option(  # noqa: B008
        None,
        "--inherit-secret-env",
        help="Secret variable name to pass to the isolated Runner; repeat as needed.",
    ),
    timeout_seconds: int = typer.Option(300, "--timeout-seconds", min=1),  # noqa: B008
    max_turns: int = typer.Option(10, "--max-turns", min=1),  # noqa: B008
    confirm_real_run: bool = typer.Option(  # noqa: B008
        False,
        "--confirm-real-run",
        help="Required for skill-up mode because it can consume Agent quota and money.",
    ),
) -> None:
    """Run 12 cases × 2 variants × 3 repeats and write JSON/HTML reports."""
    if mode == DemoMode.SKILL_UP and not confirm_real_run:
        raise typer.BadParameter(
            "skill-up mode may consume Agent quota; pass --confirm-real-run explicitly",
            param_hint="--confirm-real-run",
        )

    def progress(record: ExecutionRecord, completed: int, total: int) -> None:
        outcome = record.evaluation_outcome.value if record.evaluation_outcome else "none"
        typer.echo(
            f"[{completed}/{total}] run={record.run_id} outcome={outcome}",
            err=True,
        )

    result = asyncio.run(
        DemoExperimentRunner().run(
            DemoRunConfig(
                workspace=workspace,
                dataset_root=dataset_root,
                skill_root=skill_root,
                mode=mode,
                repeats=repeats,
                random_seed=random_seed,
                bootstrap_resamples=bootstrap_resamples,
                runner_binary=runner_binary,
                engine=engine,
                model_provider=model_provider,
                model_name=model_name,
                inherited_secret_env=tuple(inherit_secret_env or ()),
                timeout_seconds=timeout_seconds,
                max_turns=max_turns,
            ),
            progress_sink=progress if mode == DemoMode.SKILL_UP else None,
        )
    )
    typer.echo(
        json.dumps(
            {
                "completed_runs": result.completed_runs,
                "control_variant_id": str(result.control_variant_id),
                "dataset_sha256": result.dataset_sha256,
                "experiment_id": str(result.experiment_id),
                "html_report": str(result.report_paths.html_path),
                "invalid_runs": result.invalid_runs,
                "json_report": str(result.report_paths.json_path),
                "logical_runs": result.logical_runs,
                "simulated": result.simulated,
                "treatment_variant_id": str(result.treatment_variant_id),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@schema_app.command("export")
def export_schema(
    destination: Path = typer.Argument(  # noqa: B008
        ...,
        help="Destination JSON file.",
        dir_okay=False,
        writable=True,
    ),
) -> None:
    """Export the versioned AgentSkill-Eval JSON Schema bundle."""
    exported = export_schema_bundle(destination)
    typer.echo(str(exported))


@storage_app.command("recover")
def recover_storage(
    workspace: Path = typer.Argument(  # noqa: B008
        ...,
        help="AgentSkill-Eval workspace root.",
        file_okay=False,
    ),
) -> None:
    """Recover valid staged manifests and quarantine corrupt files."""
    report = LocalExperimentStore(workspace).recover()
    typer.echo(
        json.dumps(
            {
                "promoted_temporary_files": list(report.promoted_temporary_files),
                "removed_duplicate_temporary_files": list(report.removed_duplicate_temporary_files),
                "quarantined_files": list(report.quarantined_files),
                "unfinished_run_ids": [str(run_id) for run_id in report.unfinished_run_ids],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@storage_app.command("rebuild-index")
def rebuild_storage_index(
    workspace: Path = typer.Argument(  # noqa: B008
        ...,
        help="AgentSkill-Eval workspace root.",
        exists=True,
        file_okay=False,
    ),
    experiment_id: UUID = typer.Argument(..., help="Experiment UUID."),  # noqa: B008
) -> None:
    """Rebuild a disposable SQLite index from manifest truth."""
    records = LocalExperimentStore(workspace).rebuild_index(experiment_id)
    typer.echo(json.dumps({"indexed_manifests": len(records)}, sort_keys=True))


@report_app.command("generate")
def generate_report(
    workspace: Path = typer.Argument(  # noqa: B008
        ...,
        help="AgentSkill-Eval workspace root.",
        exists=True,
        file_okay=False,
    ),
    experiment_id: UUID = typer.Argument(..., help="Experiment UUID."),  # noqa: B008
    control_variant_id: UUID = typer.Option(  # noqa: B008
        ...,
        "--control",
        help="Control/baseline Variant UUID.",
    ),
    treatment_variant_id: UUID = typer.Option(  # noqa: B008
        ...,
        "--treatment",
        help="Treatment/candidate Variant UUID.",
    ),
    bootstrap_resamples: int = typer.Option(  # noqa: B008
        10_000, "--bootstrap-resamples", min=1
    ),
    bootstrap_seed: int = typer.Option(2026, "--bootstrap-seed"),  # noqa: B008
    majority_threshold: float = typer.Option(  # noqa: B008
        0.5, "--majority-threshold", min=0.000001, max=1.0
    ),
    min_independent_groups: int = typer.Option(  # noqa: B008
        2, "--min-independent-groups", min=1
    ),
) -> None:
    """Generate machine-readable JSON and script-free offline HTML reports."""
    store = LocalExperimentStore(workspace)
    statistics = ExperimentAnalyzer(store).analyze(
        experiment_id,
        AnalysisConfig(
            control_variant_id=control_variant_id,
            treatment_variant_id=treatment_variant_id,
            bootstrap_resamples=bootstrap_resamples,
            bootstrap_seed=bootstrap_seed,
            majority_threshold=majority_threshold,
            min_independent_groups=min_independent_groups,
        ),
    )
    paths = StaticReportWriter(store).write(experiment_id, statistics)
    typer.echo(
        json.dumps(
            {
                "html_report": str(paths.html_path),
                "inference_ready": statistics.inference_ready,
                "json_report": str(paths.json_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
