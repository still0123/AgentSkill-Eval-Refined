"""AgentSkill-Eval command-line interface."""

import asyncio
import json
from pathlib import Path
from typing import List, Optional
from uuid import UUID

import typer

from agentskill_eval_benchmark_gen import (
    DatasetLoader,
    DemoExperimentRunner,
    DemoMode,
    DemoRunConfig,
)
from agentskill_eval_cli import __version__
from agentskill_eval_contracts import export_schema_bundle
from agentskill_eval_experiment import (
    AnalysisConfig,
    ExecutionRecord,
    ExperimentAnalyzer,
    LocalExperimentStore,
    StaticReportWriter,
)

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
