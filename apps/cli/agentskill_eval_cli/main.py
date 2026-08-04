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
from agentskill_eval_contracts import (
    RealEvidenceStatus,
    RealRunMode,
    ReviewDecision,
    export_schema_bundle,
)
from agentskill_eval_experiment import (
    ExecutionRecord,
)
from agentskill_eval_real_evidence import (
    RealAgentEvidenceRunner,
    RealAgentEvidenceSpec,
    RealEvidenceStore,
)
from agentskill_eval_scenarios import UnifiedScenarioRunner, UnifiedScenarioSpec
from agentskill_eval_skill_optimizer import (
    BenchmarkGuidedSkillSearch,
    FailureBridgeError,
    FinalEvaluationStore,
    IndependentFinalEvaluationSpec,
    IndependentFinalEvaluator,
    ObservedFailureEvidenceBridge,
    OptimizationSearchSpec,
    OptimizationStore,
    PromotionWorkflow,
    RealEvaluationAuthorization,
)

app = typer.Typer(
    name="agentskill-eval",
    help="Run reproducible Agent Skill evaluation and regression experiments.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
schema_app = typer.Typer(help="Inspect and export public data-contract schemas.")
app.add_typer(schema_app, name="schema")
dataset_app = typer.Typer(help="Validate and inspect curated evaluation datasets.")
app.add_typer(dataset_app, name="dataset")
demo_app = typer.Typer(help="Run the service-free P0 demonstration experiment.")
app.add_typer(demo_app, name="demo")
benchmark_app = typer.Typer(help="Generate, review, and publish audited benchmark candidates.")
app.add_typer(benchmark_app, name="benchmark")
optimize_app = typer.Typer(help="Search validation data for a frozen Skill candidate.")
app.add_typer(optimize_app, name="optimize")
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


@real_app.command("preflight")
def real_preflight(
    spec_path: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
) -> None:
    """Validate immutable inputs and print cost estimates without invoking an Agent."""
    try:
        spec = RealAgentEvidenceSpec.load(spec_path)
        report = RealAgentEvidenceRunner(Path(".")).preflight(spec)
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="CONFIG") from exc
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
    """Run each configured case once per arm after explicit budget authorization."""
    if not confirm_real_run:
        raise typer.BadParameter("--confirm-real-run required", param_hint="--confirm-real-run")
    import asyncio
    spec = RealAgentEvidenceSpec.load(spec_path)
    result = asyncio.run(
        RealAgentEvidenceRunner(workspace).run(
            spec, RealRunMode.SMOKE, confirm_real_run=True,
            max_cost_microusd=max_cost_microusd, max_agent_runs=max_agent_runs,
        )
    )
    typer.echo(json.dumps({
        "experiment_id": str(result.manifest.experiment_id),
        "status": result.manifest.status.value,
        "completed_runs": result.manifest.completed_runs,
        "invalid_runs": result.manifest.invalid_runs,
    }, sort_keys=True))


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
    if not confirm_real_run:
        raise typer.BadParameter("--confirm-real-run required", param_hint="--confirm-real-run")
    import asyncio
    spec = RealAgentEvidenceSpec.load(spec_path)
    result = asyncio.run(
        RealAgentEvidenceRunner(workspace).run(
            spec, RealRunMode.EVIDENCE, confirm_real_run=True,
            max_cost_microusd=max_cost_microusd, max_agent_runs=max_agent_runs,
        )
    )
    typer.echo(json.dumps({
        "experiment_id": str(result.manifest.experiment_id),
        "status": result.manifest.status.value,
        "completed_runs": result.manifest.completed_runs,
        "invalid_runs": result.manifest.invalid_runs,
    }, sort_keys=True))


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
    run = store.load_run(experiment_id)
    if run.status != RealEvidenceStatus.COMPLETED:
        typer.echo(
            json.dumps(
                {
                    "claim_limit": run.claim_limit,
                    "completed_runs": run.completed_runs,
                    "experiment_id": str(experiment_id),
                    "invalid_runs": run.invalid_runs,
                    "report_available": False,
                    "status": run.status.value,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        raise typer.Exit(code=1)
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
    if not allow_simulation:
        raise typer.BadParameter("requires --allow-simulation", param_hint="--allow-simulation")
    spec = IndependentFinalEvaluationSpec.load(spec_path)
    result = PromotionWorkflow(workspace).confirm(workflow_id, spec)
    typer.echo(
        json.dumps(
            {"result": "confirm_ok", "status": result.workflow.status.value},
            sort_keys=True,
        )
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
    if not allow_simulation:
        raise typer.BadParameter("requires --allow-simulation", param_hint="--allow-simulation")
    spec = IndependentFinalEvaluationSpec.load(spec_path)
    result = PromotionWorkflow(workspace).locked_test(workflow_id, spec)
    typer.echo(
        json.dumps(
            {"result": "locked_ok", "status": result.workflow.status.value},
            sort_keys=True,
        )
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
    typer.echo(json.dumps({
        "workflow_id": str(result.workflow.id),
        "status": result.workflow.status.value,
        "release_decision": result.release_manifest.decision if result.release_manifest else None,
    }, sort_keys=True))


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
    typer.echo(json.dumps({
        "workflow_id": str(result.workflow.id),
        "status": result.workflow.status.value,
        "release_decision": result.release_manifest.decision if result.release_manifest else None,
    }, sort_keys=True))


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