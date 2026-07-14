"""AgentSkill-Eval command-line interface."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import List, Optional
from uuid import UUID

import typer

from agentskill_eval_benchmark_gen import (
    AutomaticBenchmarkGenerator,
    BenchmarkGenerationSpec,
    BenchmarkSplitPlan,
    BenchmarkStore,
    DatasetLoader,
    DatasetSplit,
    DemoExperimentRunner,
    DemoMode,
    DemoRunConfig,
    OptimizationBenchmarkPlan,
    OptimizationBenchmarkPublisher,
)
from agentskill_eval_cli import __version__
from agentskill_eval_contracts import (
    RealEvidenceClass,
    RealRunMode,
    ReviewDecision,
    export_schema_bundle,
)
from agentskill_eval_experiment import (
    AnalysisConfig,
    ExecutionRecord,
    ExperimentAnalyzer,
    LocalExperimentStore,
    ReplayBundleWriter,
    StaticReportWriter,
)
from agentskill_eval_mcp_lab import LabConfig, McpDataset, McpLabRunner, find_trace, load_report
from agentskill_eval_memory_rag_lab import (
    LabConfig as MemoryRagLabConfig,
)
from agentskill_eval_memory_rag_lab import (
    MemoryRagDataset,
    MemoryRagLabRunner,
)
from agentskill_eval_memory_rag_lab import (
    find_trace as find_memory_rag_trace,
)
from agentskill_eval_memory_rag_lab import (
    load_report as load_memory_rag_report,
)
from agentskill_eval_real_evidence import (
    RealAgentEvidenceRunner,
    RealAgentEvidenceSpec,
    RealEvidenceStore,
)
from agentskill_eval_scenarios import UnifiedScenarioRunner, UnifiedScenarioSpec
from agentskill_eval_skill_optimizer import (
    BenchmarkGuidedSkillSearch,
    DeepSeekGeneratorAuthorization,
    EvolutionEvidenceReleasePreparer,
    EvolutionExecutionPlanSpec,
    EvolutionReleaseConfig,
    FailureBridgeError,
    FailureGuidedEvolutionSpec,
    FailureGuidedSkillEvolution,
    FinalEvaluationStore,
    IndependentFinalEvaluationSpec,
    IndependentFinalEvaluator,
    ObservedFailureEvidenceBridge,
    OptimizationSearchSpec,
    OptimizationStore,
    PromotionWorkflow,
    PromotionWorkflowResult,
    RealEvaluationAuthorization,
    RealEvolutionExecutionPlanner,
    RealLLMProposalService,
    RealLLMProposalSpec,
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
benchmark_split_app = typer.Typer(
    help="Validate and publish the immutable five-way optimization benchmark."
)
benchmark_app.add_typer(benchmark_split_app, name="split")
optimize_app = typer.Typer(help="Search validation data for a frozen Skill candidate.")
app.add_typer(optimize_app, name="optimize")
evolution_app = typer.Typer(help="Package and inspect frozen Skill evolution evidence.")
app.add_typer(evolution_app, name="evolution")
evolution_release_app = typer.Typer(help="Prepare and verify offline evolution releases.")
evolution_app.add_typer(evolution_release_app, name="release")
evolution_plan_app = typer.Typer(help="Freeze a no-execution real evolution run and cost plan.")
evolution_app.add_typer(evolution_plan_app, name="plan")
evolve_app = typer.Typer(help="Generate Skill candidates from train failure diagnoses.")
optimize_app.add_typer(evolve_app, name="evolve")
proposal_app = typer.Typer(help="Generate audited real-LLM proposals without running search.")
optimize_app.add_typer(proposal_app, name="proposal")
final_app = typer.Typer(help="Evaluate a frozen base/winner pair on an independent split.")
app.add_typer(final_app, name="final")
skill_app = typer.Typer(help="Inspect and promote immutable Agent Skill versions.")
app.add_typer(skill_app, name="skill")
skill_promote_app = typer.Typer(help="Run the Fake-evidence Stage 4b promotion workflow.")
skill_app.add_typer(skill_promote_app, name="promote")
real_app = typer.Typer(help="Preflight and run budgeted observed-Agent evidence experiments.")
app.add_typer(real_app, name="real")
scenario_app = typer.Typer(
    help="Validate and run heterogeneous evaluations through one audited protocol."
)
app.add_typer(scenario_app, name="scenario")


mcp_app = typer.Typer(help="Validate and run auditable MCP tool-evaluation experiments.")
app.add_typer(mcp_app, name="mcp")
mcp_lab_app = typer.Typer(help="Run the deterministic offline MCP lab.")
mcp_app.add_typer(mcp_lab_app, name="lab")
memory_rag_app = typer.Typer(help="Validate and run auditable Memory/RAG evaluations.")
app.add_typer(memory_rag_app, name="memory-rag")
memory_rag_lab_app = typer.Typer(help="Run the deterministic offline Memory/RAG lab.")
memory_rag_app.add_typer(memory_rag_lab_app, name="lab")


@scenario_app.command("validate")
def scenario_validate(
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
) -> None:
    """Validate a unified scenario and print its frozen execution plan."""
    spec = UnifiedScenarioSpec.load(spec_path)
    plan = UnifiedScenarioRunner(Path(".")).validate(spec)
    payload = plan.model_dump(mode="json")
    payload["plan_sha256"] = plan.plan_sha256
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


@scenario_app.command("run")
def scenario_run(
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
) -> None:
    """Run one scenario without erasing its native metrics or evidence boundary."""
    spec = UnifiedScenarioSpec.load(spec_path)
    runner = UnifiedScenarioRunner(workspace)
    try:
        result = runner.run(spec, allow_simulation=allow_simulation)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="CONFIG") from exc
    output = runner.output_dir(result.experiment_id)
    typer.echo(
        json.dumps(
            {
                "experiment_id": str(result.experiment_id),
                "scenario": result.plan.scenario.value,
                "comparison": result.plan.comparison.value,
                "simulated": result.simulated,
                "evidence_class": result.evidence_class.value,
                "report_json": str(output / "unified-report.json"),
                "report_html": str(output / "unified-report.html"),
                "claim_limit": result.claim_limit,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@scenario_app.command("report")
def scenario_report(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    experiment_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Read one persisted unified result envelope."""
    result = UnifiedScenarioRunner(workspace).load(experiment_id)
    typer.echo(result.model_dump_json(indent=2))


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


def _load_observed_real_spec(spec_path: Path) -> RealAgentEvidenceSpec:
    spec = RealAgentEvidenceSpec.load(spec_path)
    if spec.evidence_class != RealEvidenceClass.OBSERVED_AGENT or spec.simulated:
        raise typer.BadParameter(
            "real execution commands require observed_agent with simulated=false",
            param_hint="CONFIG",
        )
    return spec


def _authorization_summary(
    spec: RealAgentEvidenceSpec,
    mode: RealRunMode,
    max_cost_microusd: int,
    max_agent_runs: int,
) -> dict[str, object]:
    preflight = RealAgentEvidenceRunner(Path(".")).preflight(spec)
    runs = preflight.smoke_runs if mode == RealRunMode.SMOKE else preflight.evidence_runs
    return {
        "event": "real_run_authorization",
        "mode": mode.value,
        "provider": spec.agent.provider,
        "model": spec.agent.model,
        "planned_runs": runs,
        "maximum_agent_runs": max_agent_runs,
        "maximum_cost_microusd": max_cost_microusd,
        "estimated_input_tokens": runs * preflight.estimated_input_tokens_per_run,
        "estimated_output_tokens": runs * preflight.estimated_output_tokens_per_run,
        "estimated_cost_microusd": runs * preflight.estimated_cost_per_run_microusd,
    }


def _run_observed_evidence(
    spec_path: Path,
    workspace: Path,
    mode: RealRunMode,
    confirm_real_run: bool,
    max_cost_microusd: int,
    max_agent_runs: int,
) -> None:
    spec = _load_observed_real_spec(spec_path)
    if not confirm_real_run:
        raise typer.BadParameter(
            "observed Agent execution requires --confirm-real-run",
            param_hint="--confirm-real-run",
        )
    summary = _authorization_summary(spec, mode, max_cost_microusd, max_agent_runs)
    typer.echo(json.dumps(summary, sort_keys=True))
    result = asyncio.run(
        RealAgentEvidenceRunner(workspace).run(
            spec,
            mode,
            confirm_real_run=True,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
        )
    )
    typer.echo(
        json.dumps(
            {
                "experiment_id": str(result.manifest.experiment_id),
                "status": result.manifest.status.value,
                "completed_runs": result.manifest.completed_runs,
                "invalid_runs": result.manifest.invalid_runs,
                "observed_or_reserved_cost_microusd": (
                    result.manifest.observed_or_reserved_cost_microusd
                ),
                "report_json": str(result.report_json) if result.report_json else None,
                "report_html": str(result.report_html) if result.report_html else None,
                "replay_bundle": str(result.replay_bundle) if result.replay_bundle else None,
            },
            sort_keys=True,
        )
    )


@real_app.command("preflight")
def real_preflight(
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
) -> None:
    """Validate immutable inputs and print cost estimates without invoking an Agent."""
    spec = RealAgentEvidenceSpec.load(spec_path)
    report = RealAgentEvidenceRunner(Path(".")).preflight(spec)
    typer.echo(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))


@real_app.command("smoke")
def real_smoke(
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    confirm_real_run: bool = typer.Option(False, "--confirm-real-run"),  # noqa: B008
    max_cost_microusd: int = typer.Option(..., "--max-cost-microusd", min=1),  # noqa: B008
    max_agent_runs: int = typer.Option(..., "--max-agent-runs", min=1),  # noqa: B008
) -> None:
    """Run two cases once per arm after explicit budget authorization."""
    _run_observed_evidence(
        spec_path,
        workspace,
        RealRunMode.SMOKE,
        confirm_real_run,
        max_cost_microusd,
        max_agent_runs,
    )


@real_app.command("run")
def real_run(
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    confirm_real_run: bool = typer.Option(False, "--confirm-real-run"),  # noqa: B008
    max_cost_microusd: int = typer.Option(..., "--max-cost-microusd", min=1),  # noqa: B008
    max_agent_runs: int = typer.Option(..., "--max-agent-runs", min=1),  # noqa: B008
) -> None:
    """Run the repeated paired evidence protocol after explicit authorization."""
    _run_observed_evidence(
        spec_path,
        workspace,
        RealRunMode.EVIDENCE,
        confirm_real_run,
        max_cost_microusd,
        max_agent_runs,
    )


@real_app.command("status")
def real_status(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    experiment_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show the immutable real-evidence run manifest."""
    run = RealEvidenceStore(workspace).load_run(experiment_id)
    typer.echo(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))


@real_app.command("report")
def real_report(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    experiment_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Locate and validate a completed offline real-evidence report."""
    store = RealEvidenceStore(workspace)
    report = store.load_report(experiment_id)
    typer.echo(
        json.dumps(
            {
                "experiment_id": str(experiment_id),
                "report_json": str(store.report_json(experiment_id)),
                "report_html": str(store.report_html(experiment_id)),
                "simulated": report.simulated,
                "evidence_class": report.evidence_class.value,
                "claim_limit": report.claim_limit,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


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


@memory_rag_app.command("validate")
def memory_rag_validate(
    dataset: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
) -> None:
    """Validate a strict Memory/RAG evaluation dataset."""
    loaded = MemoryRagDataset.load(dataset, allowed_root=dataset.parent)
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


@memory_rag_lab_app.command("run")
def memory_rag_lab_run(
    config: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(..., "--workspace", file_okay=False),  # noqa: B008
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
) -> None:
    """Run deterministic paired Memory/RAG controller validation."""
    loaded = MemoryRagLabConfig.load(config)
    if loaded.simulated and not allow_simulation:
        raise typer.BadParameter(
            "Memory/RAG Lab requires --allow-simulation and cannot support real-agent claims",
            param_hint="--allow-simulation",
        )
    artifacts = MemoryRagLabRunner(workspace).run(loaded)
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


@memory_rag_app.command("report")
def memory_rag_report(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    experiment_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show one persisted Memory/RAG paired report."""
    typer.echo(load_memory_rag_report(workspace, experiment_id).model_dump_json(indent=2))


@memory_rag_app.command("trace")
def memory_rag_trace(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    run_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Show one normalized Memory/RAG trace by run ID."""
    typer.echo(
        json.dumps(find_memory_rag_trace(workspace, run_id), ensure_ascii=False, sort_keys=True)
    )


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


@evolution_release_app.command("prepare")
def evolution_release_prepare(
    config_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        ..., "--workspace", file_okay=False, help="Output root for evolution-release/."
    ),
) -> None:
    """Prepare one deterministic offline release from frozen Fake evidence."""
    try:
        config = EvolutionReleaseConfig.load(config_path)
        result = EvolutionEvidenceReleasePreparer(workspace).prepare(config)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="CONFIG") from exc
    typer.echo(
        json.dumps(
            {
                "release_dir": str(result.release_dir),
                "manifest_sha256": result.manifest_sha256,
                "report_json": str(result.report_json),
                "report_html": str(result.report_html),
                "audit_bundle": str(result.audit_bundle),
                "idempotent_replay": result.idempotent_replay,
                "simulated": True,
                "evidence_class": "simulated",
            },
            sort_keys=True,
        )
    )


@evolution_release_app.command("verify")
def evolution_release_verify(
    release_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Verify manifest, members, parent lineage, and deterministic audit tar."""
    try:
        preparer = EvolutionEvidenceReleasePreparer(release_dir.parent)
        manifest = preparer.verify(release_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="RELEASE_DIR") from exc
    typer.echo(
        json.dumps(
            {
                "valid": True,
                "release_dir": str(release_dir.resolve()),
                "manifest_sha256": hashlib.sha256(
                    (release_dir / "release-manifest.json").read_bytes()
                ).hexdigest(),
                "input_fingerprint": manifest["input_fingerprint"],
            },
            sort_keys=True,
        )
    )


@evolution_release_app.command("inspect")
def evolution_release_inspect(
    release_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Verify and print a compact, claim-limited release summary."""
    try:
        summary = EvolutionEvidenceReleasePreparer(release_dir.parent).inspect(release_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="RELEASE_DIR") from exc
    typer.echo(json.dumps(summary, ensure_ascii=False, sort_keys=True))


@evolution_plan_app.command("preflight")
def evolution_plan_preflight(
    config_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
) -> None:
    """Calculate exact stage run and cost envelopes without writing or executing."""
    try:
        spec = EvolutionExecutionPlanSpec.load(config_path)
        plan = RealEvolutionExecutionPlanner(Path(".")).preflight(spec)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="CONFIG") from exc
    typer.echo(plan.model_dump_json(indent=2))


@evolution_plan_app.command("prepare")
def evolution_plan_prepare(
    config_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
) -> None:
    """Write an immutable execution plan; never invoke a model or Agent."""
    try:
        spec = EvolutionExecutionPlanSpec.load(config_path)
        result = RealEvolutionExecutionPlanner(workspace).prepare(spec)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="CONFIG") from exc
    typer.echo(
        json.dumps(
            {
                "plan_id": str(result.plan.plan_id),
                "directory": str(result.directory),
                "total_agent_runs": result.plan.total_agent_runs,
                "total_estimated_cost_microusd": (
                    result.plan.total_estimated_cost_microusd
                ),
                "real_calls_executed": False,
                "locked_content_accessed": False,
            },
            sort_keys=True,
        )
    )


@evolution_plan_app.command("inspect")
def evolution_plan_inspect(
    plan_directory: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Verify and print the complete immutable execution plan."""
    try:
        result = RealEvolutionExecutionPlanner(plan_directory.parent.parent).verify(
            plan_directory
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="PLAN_DIR") from exc
    typer.echo(result.plan.model_dump_json(indent=2))


@evolution_plan_app.command("verify")
def evolution_plan_verify(
    plan_directory: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Detect any modification to a prepared execution plan."""
    try:
        result = RealEvolutionExecutionPlanner(plan_directory.parent.parent).verify(
            plan_directory
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="PLAN_DIR") from exc
    typer.echo(
        json.dumps(
            {
                "valid": True,
                "plan_id": str(result.plan.plan_id),
                "real_calls_executed": result.plan.real_calls_executed,
                "locked_content_accessed": result.plan.locked_content_accessed,
            },
            sort_keys=True,
        )
    )


def _promotion_summary(result: PromotionWorkflowResult) -> dict[str, object]:
    workflow = result.workflow
    release = result.release_manifest
    publication = result.publication
    return {
        "workflow_id": str(workflow.id),
        "promotion_id": str(workflow.promotion_id),
        "status": workflow.status.value,
        "simulated": workflow.simulated,
        "claim_limit": workflow.claim_limit,
        "release_decision": release.decision if release is not None else None,
        "skill_version_manifest": (
            str(publication.manifest_path) if publication is not None else None
        ),
        "release_manifest_sha256": workflow.release_manifest_sha256,
    }


@skill_promote_app.command("begin")
def skill_promote_begin(
    handoff_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    skill_name: str = typer.Option(..., "--skill-name"),  # noqa: B008
    target_version: str = typer.Option(..., "--target-version"),  # noqa: B008
    actor: str = typer.Option("fixture-evolution", "--actor"),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
) -> None:
    """Accept one Fake AWAITING_INDEPENDENT_FINAL_EVALUATION handoff."""
    try:
        workflow = PromotionWorkflow(workspace).begin(
            handoff_path,
            skill_name=skill_name,
            target_version=target_version,
            actor=actor,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="HANDOFF") from exc
    typer.echo(
        json.dumps(
            {
                "workflow_id": str(workflow.id),
                "promotion_id": str(workflow.promotion_id),
                "status": workflow.status.value,
                "simulated": workflow.simulated,
                "claim_limit": workflow.claim_limit,
            },
            sort_keys=True,
        )
    )


def _run_promotion_final_step(
    workflow_id: UUID,
    spec_path: Path,
    workspace: Path,
    *,
    locked: bool,
    allow_simulation: bool,
) -> None:
    spec = IndependentFinalEvaluationSpec.load(spec_path)
    if not allow_simulation:
        raise typer.BadParameter(
            "Stage 4b requires --allow-simulation and cannot run real evidence",
            param_hint="--allow-simulation",
        )
    service = PromotionWorkflow(workspace)
    try:
        result = (
            service.locked_test(workflow_id, spec) if locked else service.confirm(workflow_id, spec)
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="CONFIG") from exc
    typer.echo(json.dumps(_promotion_summary(result), sort_keys=True))


@skill_promote_app.command("confirm")
def skill_promote_confirm(
    workflow_id: UUID = typer.Argument(...),  # noqa: B008
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
) -> None:
    """Run Fake validation_confirm through IndependentFinalEvaluator."""
    _run_promotion_final_step(
        workflow_id, spec_path, workspace, locked=False, allow_simulation=allow_simulation
    )


@skill_promote_app.command("locked")
def skill_promote_locked(
    workflow_id: UUID = typer.Argument(...),  # noqa: B008
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
) -> None:
    """Consume the one-shot Fake locked test through IndependentFinalEvaluator."""
    _run_promotion_final_step(
        workflow_id, spec_path, workspace, locked=True, allow_simulation=allow_simulation
    )


@skill_promote_app.command("approve")
def skill_promote_approve(
    workflow_id: UUID = typer.Argument(...),  # noqa: B008
    reviewer: str = typer.Option(..., "--reviewer"),  # noqa: B008
    reason: str = typer.Option(..., "--reason"),  # noqa: B008
    confirm_human_review: bool = typer.Option(False, "--confirm-human-review"),  # noqa: B008
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
) -> None:
    """Approve and locally publish a Fake-only immutable SkillVersion fixture."""
    if not confirm_human_review or not allow_simulation:
        raise typer.BadParameter(
            "approval requires --confirm-human-review and --allow-simulation",
            param_hint="--confirm-human-review/--allow-simulation",
        )
    try:
        result = PromotionWorkflow(workspace).approve(workflow_id, reviewer=reviewer, reason=reason)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="WORKFLOW_ID") from exc
    typer.echo(json.dumps(_promotion_summary(result), sort_keys=True))


@skill_promote_app.command("reject")
def skill_promote_reject(
    workflow_id: UUID = typer.Argument(...),  # noqa: B008
    reviewer: str = typer.Option(..., "--reviewer"),  # noqa: B008
    reason: str = typer.Option(..., "--reason"),  # noqa: B008
    confirm_human_review: bool = typer.Option(False, "--confirm-human-review"),  # noqa: B008
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
) -> None:
    """Reject a Fake promotion after locked-test completion."""
    if not confirm_human_review or not allow_simulation:
        raise typer.BadParameter(
            "rejection requires --confirm-human-review and --allow-simulation",
            param_hint="--confirm-human-review/--allow-simulation",
        )
    try:
        result = PromotionWorkflow(workspace).reject(workflow_id, reviewer=reviewer, reason=reason)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="WORKFLOW_ID") from exc
    typer.echo(json.dumps(_promotion_summary(result), sort_keys=True))


@skill_promote_app.command("status")
def skill_promote_status(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    workflow_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Read the current workflow and immutable terminal release decision."""
    try:
        result = PromotionWorkflow(workspace).status(workflow_id)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="WORKFLOW_ID") from exc
    typer.echo(
        json.dumps(
            {
                "workflow": result.workflow.model_dump(mode="json"),
                "release": (
                    result.release_manifest.model_dump(mode="json")
                    if result.release_manifest is not None
                    else None
                ),
            },
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
    confirm_real_run: bool = typer.Option(  # noqa: B008
        False,
        "--confirm-real-run",
        help="Explicitly authorize observed-Agent calls for a real_agent evaluator.",
    ),
    max_cost_microusd: Optional[int] = typer.Option(  # noqa: B008
        None, "--max-cost-microusd", min=1
    ),
    max_agent_runs: Optional[int] = typer.Option(  # noqa: B008
        None, "--max-agent-runs", min=1
    ),
) -> None:
    """Run successive halving and freeze one validation-only Pareto winner."""
    spec = OptimizationSearchSpec.load(spec_path)
    if spec.evaluator.type == "simulated_keyword" and not allow_simulation:
        raise typer.BadParameter(
            "simulated evaluator requires --allow-simulation",
            param_hint="--allow-simulation",
        )
    authorization = None
    if spec.evaluator.type == "real_agent":
        if not confirm_real_run:
            raise typer.BadParameter(
                "real_agent evaluator requires --confirm-real-run",
                param_hint="--confirm-real-run",
            )
        if max_cost_microusd is None:
            raise typer.BadParameter(
                "real_agent evaluator requires --max-cost-microusd",
                param_hint="--max-cost-microusd",
            )
        if max_agent_runs is None:
            raise typer.BadParameter(
                "real_agent evaluator requires --max-agent-runs",
                param_hint="--max-agent-runs",
            )
        if spec.evaluator.real_agent_config_path is None:
            raise typer.BadParameter("real_agent config path is missing", param_hint="SPEC")
        real_spec = RealAgentEvidenceSpec.load(spec.evaluator.real_agent_config_path)
        candidate_count = 3 + len(spec.mutations)
        unique_candidate_case_evaluations = candidate_count * spec.search.subset_size + (
            3 + spec.search.promote_search_candidates
        ) * (len(DatasetLoader().load(spec.validation_search_path).cases) - spec.search.subset_size)
        worst_case_runs = unique_candidate_case_evaluations * 2
        estimated_cost = worst_case_runs * real_spec.pricing.estimated_cost_per_run_microusd
        typer.echo(
            json.dumps(
                {
                    "event": "real_optimizer_preflight",
                    "provider": real_spec.agent.provider,
                    "model": real_spec.agent.model,
                    "candidate_count": candidate_count,
                    "case_count": len(DatasetLoader().load(spec.validation_search_path).cases),
                    "worst_case_agent_runs": worst_case_runs,
                    "estimated_cost_microusd": estimated_cost,
                    "authorized_agent_runs": max_agent_runs,
                    "authorized_cost_microusd": max_cost_microusd,
                },
                sort_keys=True,
            ),
            err=True,
        )
        authorization = RealEvaluationAuthorization(
            confirm_real_run=True,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
        )
    result = BenchmarkGuidedSkillSearch(workspace).run(spec, real_authorization=authorization)
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
                "real_cost_consumed_microusd": (
                    authorization.consumed_cost_microusd if authorization else None
                ),
                "real_agent_runs_consumed": (
                    authorization.consumed_agent_runs if authorization else None
                ),
            },
            sort_keys=True,
        )
    )


@optimize_app.command("prepare-failures")
def optimize_prepare_failures(
    workspace: Path = typer.Argument(  # noqa: B008
        ..., exists=True, file_okay=False, help="Workspace containing an observed experiment."
    ),
    experiment_id: UUID = typer.Argument(  # noqa: B008
        ..., help="Completed observed-Agent Skill v1 experiment UUID."
    ),
    output: Path = typer.Option(  # noqa: B008
        ..., "--output", dir_okay=False, help="Destination train FailureEvidenceBundle YAML."
    ),
    review: Optional[Path] = typer.Option(  # noqa: B008
        None,
        "--review",
        exists=True,
        dir_okay=False,
        help="Optional include/exclude and label-override YAML.",
    ),
) -> None:
    """Export trace-linked Skill treatment failures for failure-guided evolution."""
    try:
        result = ObservedFailureEvidenceBridge(workspace).prepare(
            experiment_id, output, review_path=review
        )
    except FailureBridgeError as exc:
        raise typer.BadParameter(str(exc), param_hint="EXPERIMENT_ID") from exc
    typer.echo(
        json.dumps(
            {
                "audit_report": str(result.report_path),
                "bundle": str(result.bundle_path) if result.bundle_path else None,
                "cluster_count": len(result.report.clusters),
                "eligible_findings": len(result.report.eligible),
                "excluded_findings": len(result.report.excluded),
                "experiment_id": str(result.report.experiment_id),
                "insufficiency_reason": result.report.insufficiency_reason,
                "status": result.report.status,
            },
            ensure_ascii=False,
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
                "candidates": [item.model_dump(mode="json") for item in store.list_candidates(job)],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@evolve_app.command("run")
def evolve_run(
    spec_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Frozen failure-guided evolution spec."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    allow_simulation: bool = typer.Option(False, "--allow-simulation"),  # noqa: B008
    confirm_real_run: bool = typer.Option(False, "--confirm-real-run"),  # noqa: B008
    max_cost_microusd: Optional[int] = typer.Option(  # noqa: B008
        None, "--max-cost-microusd", min=1
    ),
    max_agent_runs: Optional[int] = typer.Option(  # noqa: B008
        None, "--max-agent-runs", min=1
    ),
    confirm_generator_run: bool = typer.Option(  # noqa: B008
        False, "--confirm-generator-run"
    ),
    max_generator_cost_microusd: Optional[int] = typer.Option(  # noqa: B008
        None, "--max-generator-cost-microusd", min=1
    ),
    max_generator_calls: Optional[int] = typer.Option(  # noqa: B008
        None, "--max-generator-calls", min=1
    ),
) -> None:
    """Generate auditable hypotheses, run existing search, and freeze a final handoff."""
    spec = FailureGuidedEvolutionSpec.load(spec_path)
    if spec.evaluator.simulated and not allow_simulation:
        raise typer.BadParameter(
            "simulated evaluator requires --allow-simulation",
            param_hint="--allow-simulation",
        )
    authorization = None
    if not spec.evaluator.simulated:
        if not confirm_real_run:
            raise typer.BadParameter(
                "real evolution requires --confirm-real-run",
                param_hint="--confirm-real-run",
            )
        if max_cost_microusd is None or max_agent_runs is None:
            raise typer.BadParameter(
                "real evolution requires cost and Agent Run limits",
                param_hint="--max-cost-microusd/--max-agent-runs",
            )
        authorization = RealEvaluationAuthorization(
            confirm_real_run=True,
            max_cost_microusd=max_cost_microusd,
            max_agent_runs=max_agent_runs,
        )
    generator_authorization = None
    if spec.generator.type == "deepseek":
        if not confirm_generator_run:
            raise typer.BadParameter(
                "DeepSeek proposal generation requires --confirm-generator-run",
                param_hint="--confirm-generator-run",
            )
        if max_generator_cost_microusd is None or max_generator_calls is None:
            raise typer.BadParameter(
                "DeepSeek proposal generation requires cost and call limits",
                param_hint="--max-generator-cost-microusd/--max-generator-calls",
            )
        approximate_input_bytes = (
            spec.base_skill_path.joinpath("SKILL.md").stat().st_size
            + spec.failure_bundle_path.stat().st_size
            + 4_000
        )
        typer.echo(
            json.dumps(
                {
                    "event": "deepseek_generator_preflight",
                    "provider": "deepseek",
                    "model": spec.generator.model,
                    "planned_calls": 1,
                    "approximate_input_bytes": approximate_input_bytes,
                    "max_output_tokens": spec.generator.max_output_tokens,
                    "authorized_calls": max_generator_calls,
                    "authorized_cost_microusd": max_generator_cost_microusd,
                },
                sort_keys=True,
            ),
            err=True,
        )
        generator_authorization = DeepSeekGeneratorAuthorization(
            confirm_real_run=True,
            max_calls=max_generator_calls,
            max_cost_microusd=max_generator_cost_microusd,
        )
    result = FailureGuidedSkillEvolution(workspace).run(
        spec,
        real_authorization=authorization,
        generator_authorization=generator_authorization,
    )
    typer.echo(
        json.dumps(
            {
                "evolution_id": str(result.report.evolution_id),
                "optimization_job_id": str(result.report.optimization_job_id),
                "hypothesis_count": len(result.report.hypotheses),
                "generator_type": spec.generator.type,
                "generator_evidence_present": result.report.generator_evidence is not None,
                "candidate_count": result.report.candidate_count,
                "winner_candidate_id": str(result.report.winner_candidate_id),
                "winner_skill_sha256": result.report.winner_skill_sha256,
                "report_json": str(result.report_json),
                "report_html": str(result.report_html),
                "final_handoff": str(result.handoff_path),
                "locked_test_accessed": result.report.locked_test_accessed,
                "simulated": result.report.simulated,
                "generator_calls_consumed": (
                    generator_authorization.calls_consumed if generator_authorization else None
                ),
                "generator_cost_microusd": (
                    generator_authorization.observed_or_reserved_cost_microusd
                    if generator_authorization
                    else None
                ),
            },
            sort_keys=True,
        )
    )


@evolve_app.command("status")
def evolve_status(
    workspace: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    evolution_id: UUID = typer.Argument(...),  # noqa: B008
) -> None:
    """Read one immutable failure-guided evolution report."""
    typer.echo(FailureGuidedSkillEvolution(workspace).load(evolution_id).model_dump_json(indent=2))


@proposal_app.command("preflight")
def proposal_preflight(
    spec_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Frozen real-LLM proposal-only spec."
    ),
) -> None:
    """Show the exact model identity, hashes, call count, and conservative cost bound."""
    spec = RealLLMProposalSpec.load(spec_path)
    preflight = RealLLMProposalService(Path(".")).preflight(spec)
    typer.echo(preflight.model_dump_json(indent=2))


@proposal_app.command("run")
def proposal_run(
    spec_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Frozen real-LLM proposal-only spec."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    confirm_real_run: bool = typer.Option(False, "--confirm-real-run"),  # noqa: B008
    max_cost_microusd: Optional[int] = typer.Option(  # noqa: B008
        None, "--max-cost-microusd", min=1
    ),
    max_calls: Optional[int] = typer.Option(None, "--max-calls", min=1),  # noqa: B008
) -> None:
    """Perform one explicitly authorized proposal call and persist immutable evidence."""
    spec = RealLLMProposalSpec.load(spec_path)
    service = RealLLMProposalService(workspace)
    preflight = service.preflight(spec)
    if not confirm_real_run:
        raise typer.BadParameter(
            "real LLM proposal generation requires --confirm-real-run",
            param_hint="--confirm-real-run",
        )
    if max_cost_microusd is None or max_calls is None:
        raise typer.BadParameter(
            "real LLM proposal generation requires cost and call limits",
            param_hint="--max-cost-microusd/--max-calls",
        )
    typer.echo(
        json.dumps(
            {
                "event": "real_llm_proposal_authorization",
                "provider": preflight.provider,
                "model": preflight.model,
                "planned_calls": preflight.planned_calls,
                "candidate_count": preflight.candidate_count,
                "estimated_max_cost_microusd": preflight.estimated_max_cost_microusd,
                "authorized_calls": max_calls,
                "authorized_cost_microusd": max_cost_microusd,
                "search_will_execute": False,
                "locked_test_will_execute": False,
            },
            sort_keys=True,
        ),
        err=True,
    )
    authorization = DeepSeekGeneratorAuthorization(
        confirm_real_run=True,
        max_calls=max_calls,
        max_cost_microusd=max_cost_microusd,
    )
    result = service.run(spec, authorization)
    typer.echo(
        json.dumps(
            {
                "proposal_job_id": str(result.manifest.proposal_job_id),
                "provider": result.manifest.provider,
                "model": result.manifest.model,
                "proposal_count": result.manifest.proposal_count,
                "cost_microusd": result.manifest.invocation_evidence.cost_microusd,
                "calls_consumed": authorization.calls_consumed,
                "search_executed": result.manifest.search_executed,
                "locked_test_accessed": result.manifest.locked_test_accessed,
                "manifest": str(result.directory / "proposal-manifest.json"),
                "report_json": str(result.report_json),
                "report_html": str(result.report_html),
            },
            sort_keys=True,
        )
    )


@proposal_app.command("inspect")
def proposal_inspect(
    proposal_directory: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Inspect a completed proposal job after verifying all immutable artifacts."""
    result = RealLLMProposalService(proposal_directory.parent.parent).verify(
        proposal_directory
    )
    typer.echo(result.manifest.model_dump_json(indent=2))


@proposal_app.command("verify")
def proposal_verify(
    proposal_directory: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Verify proposal artifact hashes and cross-file semantic consistency."""
    result = RealLLMProposalService(proposal_directory.parent.parent).verify(
        proposal_directory
    )
    typer.echo(
        json.dumps(
            {
                "passed": True,
                "proposal_job_id": str(result.manifest.proposal_job_id),
                "proposal_count": result.manifest.proposal_count,
            },
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
    spec = BenchmarkGenerationSpec.load(spec_path)
    result = AutomaticBenchmarkGenerator(workspace).generate(spec)
    typer.echo(
        json.dumps(
            {
                "job_id": str(result.job.id),
                "status": result.job.status.value,
                "source_count": len(spec.repository_sources()),
                "provenance_families": sorted(
                    candidate.provenance_family or candidate.after_commit
                    for candidate in spec.candidates
                ),
                "candidates": [
                    {"id": str(item.id), "key": item.key, "status": item.status.value}
                    for item in result.candidates
                ],
            },
            sort_keys=True,
        )
    )


@benchmark_app.command("audit-split-plan")
def audit_benchmark_split_plan(
    plan_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Complete five-split benchmark allocation."
    ),
) -> None:
    """Fail closed unless every candidate is assigned once and exposure isolation passes."""
    plan = BenchmarkSplitPlan.load(plan_path)
    report = plan.audit()
    report.require_passed()
    inventory = {
        split.value: len(case_ids) for split, case_ids in plan.splits.by_split().items()
    }
    typer.echo(
        json.dumps(
            {
                "name": plan.name,
                "version": plan.version,
                "repository_isolation": plan.repository_isolation,
                "locked_test_visibility": plan.locked_test_visibility,
                "case_count": len(report.entries),
                "split_inventory": inventory,
                "passed": report.passed,
                "claim_limit": plan.claim_limit,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@benchmark_app.command("generate-split")
def generate_benchmark_split(
    plan_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Audited five-split benchmark allocation."
    ),
    split: DatasetSplit = typer.Argument(..., help="One split to materialize."),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
) -> None:
    """Generate one DatasetVersion input from an already-audited complete split plan."""
    plan = BenchmarkSplitPlan.load(plan_path)
    spec = plan.generation_spec(split)
    result = AutomaticBenchmarkGenerator(workspace).generate(spec)
    typer.echo(
        json.dumps(
            {
                "job_id": str(result.job.id),
                "status": result.job.status.value,
                "plan": f"{plan.name}@{plan.version}",
                "split": split.value,
                "candidate_count": len(result.candidates),
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


@benchmark_split_app.command("validate")
def validate_optimization_benchmark_split(
    plan_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Five-way optimization benchmark plan."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
) -> None:
    """Validate counts, source catalogs, bundle identities, and split isolation."""
    plan = OptimizationBenchmarkPlan.load(plan_path)
    publisher = OptimizationBenchmarkPublisher(workspace)
    specs = publisher.validate_plan(plan, plan_path)
    typer.echo(
        json.dumps(
            {
                "name": plan.name,
                "version": plan.version,
                "case_count": sum(len(spec.candidates) for spec in specs),
                "splits": {
                    item.split.value: {
                        "case_count": len(spec.candidates),
                        "repositories": [
                            source.repository_url for source in spec.repository_sources()
                        ],
                        "optimizer_visible": item.optimizer_visible,
                    }
                    for item, spec in zip(plan.splits, specs)
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@benchmark_split_app.command("publish")
def publish_optimization_benchmark_split(
    plan_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Five-way optimization benchmark plan."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        Path(".agentskill-eval-workspace"), "--workspace", file_okay=False
    ),
    reviewer: str = typer.Option(..., "--reviewer"),  # noqa: B008
    publisher_name: str = typer.Option(..., "--publisher"),  # noqa: B008
    confirm: bool = typer.Option(False, "--confirm-offline-publication"),  # noqa: B008
) -> None:
    """Run 240 offline verifier commands and publish five immutable DatasetVersions."""
    if not confirm:
        raise typer.BadParameter(
            "publication requires --confirm-offline-publication (no model calls or fees)"
        )
    plan = OptimizationBenchmarkPlan.load(plan_path)
    release, directory = OptimizationBenchmarkPublisher(workspace).publish(
        plan,
        plan_path,
        reviewer=reviewer,
        publisher=publisher_name,
    )
    typer.echo(
        json.dumps(
            {
                "release": str(directory / "release-manifest.json"),
                "content_sha256": release.content_sha256,
                "case_count": release.total_case_count,
                "repository_count": release.repository_count,
                "independence_group_count": release.independence_group_count,
                "split_counts": {
                    item.split.value: item.case_count for item in release.splits
                },
                "locked_policy": release.locked_policy,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@benchmark_split_app.command("verify")
def verify_optimization_benchmark_split(
    release_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Immutable release-manifest.json."
    ),
    workspace: Path = typer.Option(  # noqa: B008
        ..., "--workspace", exists=True, file_okay=False
    ),
) -> None:
    """Re-hash every DatasetVersion and rerun the cross-split leakage audit."""
    publisher = OptimizationBenchmarkPublisher(workspace)
    release = publisher.load_release(release_path)
    publisher.verify(release)
    typer.echo(
        json.dumps(
            {
                "verified": True,
                "content_sha256": release.content_sha256,
                "case_count": release.total_case_count,
            },
            sort_keys=True,
        )
    )


@benchmark_split_app.command("inspect")
def inspect_optimization_benchmark_split(
    release_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, help="Immutable release-manifest.json."
    ),
) -> None:
    """Print the immutable release without opening withheld DatasetVersion paths."""
    release = OptimizationBenchmarkPublisher.load_release(release_path)
    typer.echo(release.model_dump_json(indent=2))


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
