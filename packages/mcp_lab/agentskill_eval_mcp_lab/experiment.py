"""Paired simulated MCP experiment orchestration and offline reporting."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Tuple, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field

from agentskill_eval_mcp_lab.adapters import FailureInjection, MockMcpAdapter
from agentskill_eval_mcp_lab.contracts import (
    FailureKind,
    McpCase,
    McpDataset,
    StrictModel,
    secure_input_path,
)
from agentskill_eval_mcp_lab.evaluation import (
    AgentPlan,
    CompositeMcpGrader,
    CompositeScore,
    McpEvaluationController,
    RunOutcome,
)


class FailureInjectionSpec(StrictModel):
    tool: str
    kind: FailureKind
    fail_attempts: int = Field(default=1, ge=1, le=10)
    latency_ms: float = Field(default=0, ge=0)


class VariantPlans(StrictModel):
    without_guidance: AgentPlan
    with_guidance: AgentPlan


class LabConfig(StrictModel):
    dataset: Path
    agent: str = Field(min_length=1)
    model: str = Field(min_length=1)
    seed: int = 0
    timeout_seconds: float = Field(default=5, gt=0)
    token_budget: int = Field(default=10_000, gt=0)
    cost_budget_usd: float = Field(default=0, ge=0)
    failure_injection: Tuple[FailureInjectionSpec, ...] = ()
    plans: Dict[str, VariantPlans]
    simulated: Literal[True]

    @classmethod
    def load(cls, path: Path) -> "LabConfig":
        safe = secure_input_path(path, path.parent)
        raw = yaml.safe_load(safe.read_text(encoding="utf-8"))
        config = cls.model_validate(raw)
        dataset_path = config.dataset
        if not dataset_path.is_absolute():
            dataset_path = safe.parent / dataset_path
        secure_input_path(dataset_path, safe.parent)
        return config.model_copy(update={"dataset": dataset_path})


class ScoredRun(StrictModel):
    run: RunOutcome
    score: CompositeScore
    independence_group: str
    expected_tools: Tuple[str, ...]


class PairedMetrics(StrictModel):
    without_mcp_success_rate: float
    with_mcp_success_rate: float
    tool_selection_gain: float
    parameter_accuracy_gain: float
    recovery_rate_gain: float
    safety_violation_change: float
    tool_call_overhead: float
    token_change: float
    latency_ms_change: float
    cost_usd_change: float
    wins: int
    ties: int
    losses: int
    invalid: int


class McpExperimentReport(StrictModel):
    experiment_id: UUID
    dataset: str
    simulated: bool
    claim_limit: str
    agent: str
    model: str
    seed: int
    timeout_seconds: float
    token_budget: int
    cost_budget_usd: float
    failure_injection: Tuple[FailureInjectionSpec, ...]
    runs: Tuple[ScoredRun, ...]
    paired_metrics: PairedMetrics


class ExperimentArtifacts(StrictModel):
    report: McpExperimentReport
    report_json: Path
    report_html: Path


class McpLabRunner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(
        self,
        config: LabConfig,
        plan_provider: Optional[
            Callable[[McpCase, Literal["without_guidance", "with_guidance"]], AgentPlan]
        ] = None,
    ) -> ExperimentArtifacts:
        dataset = McpDataset.load(config.dataset, allowed_root=config.dataset.parent)
        missing = {case.case_id for case in dataset.cases} - set(config.plans)
        extra = set(config.plans) - {case.case_id for case in dataset.cases}
        if missing or extra:
            raise ValueError(
                f"plans must match dataset cases; missing={sorted(missing)}, extra={sorted(extra)}"
            )
        experiment_id = uuid5(
            NAMESPACE_URL,
            f"agentskill-eval:mcp:{dataset.name}:{config.agent}:{config.model}:{config.seed}",
        )
        output = self.workspace / "mcp" / str(experiment_id)
        traces = output / "traces"
        traces.mkdir(parents=True, exist_ok=True)
        scored: List[ScoredRun] = []
        failures = tuple(
            FailureInjection(item.tool, item.kind, item.fail_attempts, item.latency_ms)
            for item in config.failure_injection
        )
        for case in dataset.cases:
            variants = config.plans[case.case_id]
            variants_and_plans: Tuple[
                Tuple[Literal["without_guidance", "with_guidance"], AgentPlan], ...
            ] = (
                ("without_guidance", variants.without_guidance),
                ("with_guidance", variants.with_guidance),
            )
            for variant, plan in variants_and_plans:
                if plan_provider is not None:
                    plan = plan_provider(case, variant)
                if plan.token_count > config.token_budget or plan.cost_usd > config.cost_budget_usd:
                    raise ValueError(f"plan budget exceeded for {case.case_id}:{variant}")
                adapter = MockMcpAdapter(case.available_tools, failures, config.seed)
                run = McpEvaluationController().run(case, adapter, plan, variant)
                score = CompositeMcpGrader().grade(case, run)
                scored_run = ScoredRun(
                    run=run,
                    score=score,
                    independence_group=case.independence_group,
                    expected_tools=case.expected_tools,
                )
                scored.append(scored_run)
                (traces / f"{run.run_id}.json").write_text(
                    run.trace.model_dump_json(indent=2), encoding="utf-8"
                )
        report = McpExperimentReport(
            experiment_id=experiment_id,
            dataset=dataset.name,
            simulated=True,
            claim_limit=(
                "SIMULATED controller validation only. This report is not evidence of real Agent, "
                "model, MCP server, or MCP guidance improvement."
            ),
            agent=config.agent,
            model=config.model,
            seed=config.seed,
            timeout_seconds=config.timeout_seconds,
            token_budget=config.token_budget,
            cost_budget_usd=config.cost_budget_usd,
            failure_injection=config.failure_injection,
            runs=tuple(scored),
            paired_metrics=_paired_metrics(scored),
        )
        report_json = output / "mcp-report.json"
        report_html = output / "mcp-report.html"
        report_json.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        report_html.write_text(_render_html(report), encoding="utf-8")
        return ExperimentArtifacts(report=report, report_json=report_json, report_html=report_html)


def _paired_metrics(runs: List[ScoredRun]) -> PairedMetrics:
    controls = {item.run.case_id: item for item in runs if item.run.variant == "without_guidance"}
    treatments = {item.run.case_id: item for item in runs if item.run.variant == "with_guidance"}
    pairs = [(controls[key], treatments[key]) for key in sorted(controls)]

    def mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    wins = ties = losses = invalid = 0
    for control, treatment in pairs:
        if "invalid" in {control.score.outcome, treatment.score.outcome}:
            invalid += 1
        elif treatment.score.final_score > control.score.final_score:
            wins += 1
        elif treatment.score.final_score < control.score.final_score:
            losses += 1
        else:
            ties += 1
    valid_control = [
        item for item, other in pairs if "invalid" not in {item.score.outcome, other.score.outcome}
    ]
    valid_treatment = [
        other for item, other in pairs if "invalid" not in {item.score.outcome, other.score.outcome}
    ]
    return PairedMetrics(
        without_mcp_success_rate=mean(
            [float(item.score.outcome == "pass") for item in valid_control]
        ),
        with_mcp_success_rate=mean(
            [float(item.score.outcome == "pass") for item in valid_treatment]
        ),
        tool_selection_gain=mean([item.score.selection_accuracy for item in valid_treatment])
        - mean([item.score.selection_accuracy for item in valid_control]),
        parameter_accuracy_gain=mean([item.score.parameter_accuracy for item in valid_treatment])
        - mean([item.score.parameter_accuracy for item in valid_control]),
        recovery_rate_gain=mean([item.score.recovery_score for item in valid_treatment])
        - mean([item.score.recovery_score for item in valid_control]),
        safety_violation_change=mean([1 - item.score.safety_score for item in valid_treatment])
        - mean([1 - item.score.safety_score for item in valid_control]),
        tool_call_overhead=mean([float(item.score.total_tool_calls) for item in valid_treatment])
        - mean([float(item.score.total_tool_calls) for item in valid_control]),
        token_change=mean([float(item.run.token_count) for item in valid_treatment])
        - mean([float(item.run.token_count) for item in valid_control]),
        latency_ms_change=mean([item.score.latency_ms for item in valid_treatment])
        - mean([item.score.latency_ms for item in valid_control]),
        cost_usd_change=mean([item.run.cost_usd for item in valid_treatment])
        - mean([item.run.cost_usd for item in valid_control]),
        wins=wins,
        ties=ties,
        losses=losses,
        invalid=invalid,
    )


def _render_html(report: McpExperimentReport) -> str:
    metrics = report.paired_metrics
    rows = []
    for item in report.runs:
        requested = [
            event.tool_name
            for event in item.run.trace.events
            if event.kind.value == "mcp.tool.requested"
        ]
        failures = [
            f"{event.tool_name}:{event.error_category.value}"
            for event in item.run.trace.events
            if event.error_category is not None
        ]
        side_effects = [
            f"{event.kind.value}:{event.tool_name}"
            for event in item.run.trace.events
            if event.kind.value.startswith("mcp.side_effect.")
        ]
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.run.case_id)}</td>"
            f"<td>{html.escape(item.independence_group)}</td>"
            f"<td>{html.escape(item.run.variant)}</td>"
            f"<td>{html.escape(', '.join(item.expected_tools))}</td>"
            f"<td>{html.escape(', '.join(name or '' for name in requested))}</td>"
            f"<td>{html.escape(', '.join(failures))}</td>"
            f"<td>{item.score.retry_count}</td><td>{item.score.invalid_call_count}</td>"
            f"<td>{item.score.duplicate_call_count}</td><td>{item.score.safety_score:.3f}</td>"
            f"<td>{html.escape(', '.join(side_effects))}</td>"
            f"<td>{item.score.final_score:.3f}</td><td>{html.escape(item.score.outcome)}</td>"
            f"<td>{html.escape('; '.join(item.score.violations))}</td>"
            f"<td>{html.escape(str(item.score.evidence_references))}</td>"
            "</tr>"
        )
    claim = html.escape(report.claim_limit)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>MCP Tool Evaluation</title>
<style>body{{font-family:system-ui;margin:2rem;color:#17202a}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccd1d1;padding:.4rem;text-align:left;vertical-align:top}}
.warning{{background:#fff3cd;border:1px solid #ffe69c;padding:1rem}}</style></head>
<body><h1>MCP Tool Evaluation — SIMULATED</h1><p class="warning">{claim}</p>
<h2>Paired comparison</h2><dl><dt>Without / with success rate</dt>
<dd>{metrics.without_mcp_success_rate:.3f} / {metrics.with_mcp_success_rate:.3f}</dd>
<dt>Selection / parameter / recovery gain</dt><dd>{metrics.tool_selection_gain:.3f} /
{metrics.parameter_accuracy_gain:.3f} / {metrics.recovery_rate_gain:.3f}</dd>
<dt>Safety violations / tool-call / token / latency / cost change</dt><dd>
{metrics.safety_violation_change:.3f} / {metrics.tool_call_overhead:.3f} /
{metrics.token_change:.3f} /
{metrics.latency_ms_change:.3f} / {metrics.cost_usd_change:.6f}</dd>
<dt>W/T/L/invalid</dt><dd>{metrics.wins}/{metrics.ties}/{metrics.losses}/{metrics.invalid}</dd></dl>
<h2>Case traces and confusion evidence</h2><table><thead><tr><th>Case</th><th>Group</th>
<th>Variant</th>
<th>Expected tools</th><th>Actual sequence</th><th>Faults</th><th>Retries</th><th>Invalid</th>
<th>Duplicate</th><th>Safety</th><th>Side-effect events</th><th>Score</th><th>Outcome</th>
<th>Parameter/selection/sequence violations</th><th>Evidence sequences</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></body></html>"""


def load_report(workspace: Path, experiment_id: UUID) -> McpExperimentReport:
    path = workspace / "mcp" / str(experiment_id) / "mcp-report.json"
    return McpExperimentReport.model_validate_json(path.read_text(encoding="utf-8"))


def find_trace(workspace: Path, run_id: UUID) -> Dict[str, object]:
    matches = list((workspace / "mcp").glob(f"*/traces/{run_id}.json"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one trace for run {run_id}, found {len(matches)}")
    return cast(Dict[str, object], json.loads(matches[0].read_text(encoding="utf-8")))
