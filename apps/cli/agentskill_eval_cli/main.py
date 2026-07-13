"""AgentSkill-Eval command-line interface."""

import json
from pathlib import Path
from typing import Optional
from uuid import UUID

import typer

from agentskill_eval_cli import __version__
from agentskill_eval_contracts import export_schema_bundle
from agentskill_eval_experiment import LocalExperimentStore

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
