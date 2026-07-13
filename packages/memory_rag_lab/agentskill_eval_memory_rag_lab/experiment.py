"""Paired deterministic Memory/RAG experiments and offline reports."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Dict, List, Literal, Tuple, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field

from agentskill_eval_memory_rag_lab.adapters import (
    FailureInjection,
    MockMemoryAdapter,
    MockRetrieverAdapter,
)
from agentskill_eval_memory_rag_lab.contracts import (
    FailureKind,
    MemoryRagDataset,
    StrictModel,
    secure_input_path,
)
from agentskill_eval_memory_rag_lab.evaluation import (
    AgentPlan,
    CompositeMemoryRagGrader,
    CompositeScore,
    MemoryRagController,
    RunOutcome,
)

PairType = Literal[
    "no_rag_vs_with_rag",
    "no_memory_vs_with_memory",
    "clean_context_vs_noisy_context",
    "clean_memory_vs_poisoned_memory",
]


class FailureInjectionSpec(StrictModel):
    target: str
    kind: FailureKind
    fail_attempts: int = Field(default=1, ge=1, le=10)
    latency_ms: float = Field(default=0, ge=0)


class PairPlans(StrictModel):
    pair_type: PairType
    control: AgentPlan
    treatment: AgentPlan


class LabConfig(StrictModel):
    dataset: Path
    agent: str = Field(min_length=1)
    model: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    seed: int = 0
    timeout_seconds: float = Field(default=5, gt=0)
    token_budget: int = Field(default=10_000, gt=0)
    cost_budget_usd: float = Field(default=0, ge=0)
    failure_injection: Tuple[FailureInjectionSpec, ...] = ()
    plans: Dict[str, PairPlans]
    simulated: Literal[True]

    @classmethod
    def load(cls, path: Path) -> "LabConfig":
        safe = secure_input_path(path, path.parent)
        config = cls.model_validate(yaml.safe_load(safe.read_text(encoding="utf-8")))
        dataset = config.dataset if config.dataset.is_absolute() else safe.parent / config.dataset
        secure_input_path(dataset, safe.parent)
        return config.model_copy(update={"dataset": dataset})


class ScoredRun(StrictModel):
    pair_type: PairType
    independence_group: str
    run: RunOutcome
    score: CompositeScore


class PairTypeMetrics(StrictModel):
    pair_type: PairType
    pair_count: int
    control_success_rate: float
    treatment_success_rate: float
    retrieval_recall_gain: float
    answer_correctness_gain: float
    faithfulness_gain: float
    memory_lifecycle_gain: float
    safety_violation_change: float
    latency_ms_change: float
    cost_usd_change: float
    token_change: float
    wins: int
    ties: int
    losses: int
    invalid: int


class MemoryRagReport(StrictModel):
    experiment_id: UUID
    dataset: str
    simulated: bool
    claim_limit: str
    agent: str
    model: str
    environment: str
    seed: int
    timeout_seconds: float
    token_budget: int
    cost_budget_usd: float
    failure_injection: Tuple[FailureInjectionSpec, ...]
    runs: Tuple[ScoredRun, ...]
    paired_metrics: Tuple[PairTypeMetrics, ...]


class ExperimentArtifacts(StrictModel):
    report: MemoryRagReport
    report_json: Path
    report_html: Path


class MemoryRagLabRunner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, config: LabConfig) -> ExperimentArtifacts:
        dataset = MemoryRagDataset.load(config.dataset, allowed_root=config.dataset.parent)
        case_ids = {case.case_id for case in dataset.cases}
        if set(config.plans) != case_ids:
            raise ValueError(
                f"plans must exactly match cases; missing={sorted(case_ids - set(config.plans))}, "
                f"extra={sorted(set(config.plans) - case_ids)}"
            )
        experiment_id = uuid5(
            NAMESPACE_URL,
            f"memory-rag:{dataset.name}:{config.agent}:{config.model}:{config.seed}",
        )
        output = self.workspace / "memory-rag" / str(experiment_id)
        trace_dir = output / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        failures = tuple(
            FailureInjection(item.target, item.kind, item.fail_attempts, item.latency_ms)
            for item in config.failure_injection
        )
        scored: List[ScoredRun] = []
        for case in dataset.cases:
            pair = config.plans[case.case_id]
            variants: Tuple[Tuple[Literal["control", "treatment"], AgentPlan], ...] = (
                ("control", pair.control),
                ("treatment", pair.treatment),
            )
            for variant, plan in variants:
                if plan.token_count > config.token_budget or plan.cost_usd > config.cost_budget_usd:
                    raise ValueError(f"plan budget exceeded for {case.case_id}:{variant}")
                retriever = MockRetrieverAdapter(case.documents, failures)
                memory = MockMemoryAdapter(
                    forbidden_keys=case.forbidden_memory_keys,
                    sensitive_keys=case.sensitive_memory_keys,
                    failures=failures,
                )
                run = MemoryRagController().run(case, retriever, memory, plan, variant)
                score = CompositeMemoryRagGrader().grade(case, run)
                if score.cost_usd > config.cost_budget_usd:
                    raise ValueError(f"run cost budget exceeded for {case.case_id}:{variant}")
                scored.append(
                    ScoredRun(
                        pair_type=pair.pair_type,
                        independence_group=case.independence_group,
                        run=run,
                        score=score,
                    )
                )
                (trace_dir / f"{run.run_id}.json").write_text(
                    run.trace.model_dump_json(indent=2), encoding="utf-8"
                )
        report = MemoryRagReport(
            experiment_id=experiment_id,
            dataset=dataset.name,
            simulated=True,
            claim_limit=(
                "SIMULATED controller validation only. This report is not evidence of real Agent, "
                "retrieval, RAG, Memory, model, or guidance improvement."
            ),
            agent=config.agent,
            model=config.model,
            environment=config.environment,
            seed=config.seed,
            timeout_seconds=config.timeout_seconds,
            token_budget=config.token_budget,
            cost_budget_usd=config.cost_budget_usd,
            failure_injection=config.failure_injection,
            runs=tuple(scored),
            paired_metrics=_paired_metrics(scored),
        )
        report_json = output / "memory-rag-report.json"
        report_html = output / "memory-rag-report.html"
        report_json.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        report_html.write_text(_render_html(report), encoding="utf-8")
        return ExperimentArtifacts(report=report, report_json=report_json, report_html=report_html)


def _paired_metrics(runs: List[ScoredRun]) -> Tuple[PairTypeMetrics, ...]:
    metrics: List[PairTypeMetrics] = []
    pair_types = sorted({item.pair_type for item in runs})

    def mean(items: List[float]) -> float:
        return sum(items) / len(items) if items else 0.0

    for pair_type in pair_types:
        selected = [item for item in runs if item.pair_type == pair_type]
        controls = {item.run.case_id: item for item in selected if item.run.variant == "control"}
        treatments = {
            item.run.case_id: item for item in selected if item.run.variant == "treatment"
        }
        pairs = [(controls[key], treatments[key]) for key in sorted(controls)]
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
        valid = [
            pair
            for pair in pairs
            if "invalid" not in {pair[0].score.outcome, pair[1].score.outcome}
        ]
        control_valid = [item[0] for item in valid]
        treatment_valid = [item[1] for item in valid]
        metrics.append(
            PairTypeMetrics(
                pair_type=pair_type,
                pair_count=len(pairs),
                control_success_rate=mean(
                    [float(item.score.outcome == "pass") for item in control_valid]
                ),
                treatment_success_rate=mean(
                    [float(item.score.outcome == "pass") for item in treatment_valid]
                ),
                retrieval_recall_gain=mean(
                    [item.score.retrieval.recall_at_k for item in treatment_valid]
                )
                - mean([item.score.retrieval.recall_at_k for item in control_valid]),
                answer_correctness_gain=mean(
                    [item.score.grounding.answer_correctness for item in treatment_valid]
                )
                - mean([item.score.grounding.answer_correctness for item in control_valid]),
                faithfulness_gain=mean(
                    [item.score.grounding.faithfulness for item in treatment_valid]
                )
                - mean([item.score.grounding.faithfulness for item in control_valid]),
                memory_lifecycle_gain=mean(
                    [item.score.memory_lifecycle.score for item in treatment_valid]
                )
                - mean([item.score.memory_lifecycle.score for item in control_valid]),
                safety_violation_change=mean(
                    [float(item.score.memory_safety.violation_count) for item in treatment_valid]
                )
                - mean([float(item.score.memory_safety.violation_count) for item in control_valid]),
                latency_ms_change=mean([item.score.latency_ms for item in treatment_valid])
                - mean([item.score.latency_ms for item in control_valid]),
                cost_usd_change=mean([item.score.cost_usd for item in treatment_valid])
                - mean([item.score.cost_usd for item in control_valid]),
                token_change=mean([float(item.run.token_count) for item in treatment_valid])
                - mean([float(item.run.token_count) for item in control_valid]),
                wins=wins,
                ties=ties,
                losses=losses,
                invalid=invalid,
            )
        )
    return tuple(metrics)


def _render_html(report: MemoryRagReport) -> str:
    metric_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.pair_type)}</td><td>{item.control_success_rate:.3f}</td>"
        f"<td>{item.treatment_success_rate:.3f}</td><td>{item.retrieval_recall_gain:.3f}</td>"
        f"<td>{item.answer_correctness_gain:.3f}</td><td>{item.memory_lifecycle_gain:.3f}</td>"
        f"<td>{item.safety_violation_change:.3f}</td>"
        f"<td>{item.latency_ms_change:.3f}</td><td>{item.cost_usd_change:.6f}</td>"
        f"<td>{item.token_change:.3f}</td>"
        f"<td>{item.wins}/{item.ties}/{item.losses}/{item.invalid}</td></tr>"
        for item in report.paired_metrics
    )
    run_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.run.case_id)}</td><td>{html.escape(item.independence_group)}</td>"
        f"<td>{html.escape(item.pair_type)}</td><td>{html.escape(item.run.variant)}</td>"
        f"<td>{item.score.retrieval.recall_at_k:.3f}</td>"
        f"<td>{item.score.retrieval.precision_at_k:.3f}</td>"
        f"<td>{item.score.retrieval.mrr:.3f}</td><td>{item.score.retrieval.ndcg:.3f}</td>"
        f"<td>{item.score.retrieval.gold_evidence_coverage:.3f}</td>"
        f"<td>{item.score.retrieval.irrelevant_context_ratio:.3f}</td>"
        f"<td>{item.score.retrieval.duplicate_retrieval_count}/"
        f"{item.score.retrieval.stale_document_count}/"
        f"{item.score.retrieval.conflicting_document_count}</td>"
        f"<td>{item.score.grounding.answer_correctness:.3f}</td>"
        f"<td>{item.score.citations.citation_precision:.3f}/"
        f"{item.score.citations.citation_recall:.3f}</td>"
        f"<td>{item.score.grounding.unsupported_claim_count}</td>"
        f"<td>{item.score.grounding.faithfulness:.3f}/"
        f"{item.score.grounding.context_utilization:.3f}</td>"
        f"<td>{int(item.score.grounding.found_evidence_not_used)}/"
        f"{int(item.score.grounding.guessed_correct_without_evidence)}</td>"
        f"<td>{item.score.context_quality.poisoned_document_count}/"
        f"{item.score.context_quality.sensitive_document_count}</td>"
        f"<td>{item.score.memory_lifecycle.score:.3f}</td>"
        f"<td>{item.score.memory_lifecycle.stale_memory_count}/"
        f"{item.score.memory_lifecycle.conflicting_memory_count}</td>"
        f"<td>{item.score.memory_safety.violation_count}</td>"
        f"<td>{item.score.memory_safety.poisoning_detected_count}/"
        f"{item.score.memory_safety.poisoning_violation_count}</td>"
        f"<td>{item.score.latency_ms:.3f}/{item.score.cost_usd:.6f}</td>"
        f"<td>{item.score.final_score:.3f} {html.escape(item.score.outcome)}</td>"
        f"<td>{html.escape(str(item.score.evidence_references))}</td></tr>"
        for item in report.runs
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Memory/RAG Evaluation</title><style>body{{font-family:system-ui;margin:2rem;color:#17202a}}
table{{border-collapse:collapse;width:100%;margin-bottom:2rem}}th,td{{border:1px solid #ccd1d1;
padding:.35rem;text-align:left;vertical-align:top}}.warning{{background:#fff3cd;border:1px solid
#ffe69c;padding:1rem}}</style></head><body><h1>Memory/RAG Evaluation — SIMULATED</h1>
<p class="warning">{html.escape(report.claim_limit)}</p><h2>Paired metrics</h2><table><thead><tr>
<th>Comparison</th><th>Control success</th><th>Treatment success</th><th>Recall gain</th>
<th>Answer gain</th><th>Memory gain</th><th>Safety violation change</th><th>Latency Δ</th>
<th>Cost Δ</th><th>Token Δ</th><th>W/T/L/invalid</th>
</tr></thead><tbody>{metric_rows}</tbody></table><h2>Retrieval, grounding, and Memory evidence</h2>
<table><thead><tr><th>Case</th><th>Group</th><th>Comparison</th><th>Variant</th><th>Recall@K</th>
<th>Precision@K</th><th>MRR</th><th>nDCG</th><th>Gold coverage</th><th>Irrelevant ratio</th>
<th>Duplicate/stale/conflict</th><th>Answer</th><th>Citation P/R</th><th>Unsupported</th>
<th>Faithfulness/utilization</th><th>Found-unused/guessed</th><th>Poison/sensitive context</th>
<th>Memory lifecycle</th><th>Stale/conflicting Memory</th><th>Safety violations</th>
<th>Poison detected/accepted</th>
<th>Latency/cost</th><th>Final</th><th>Trace evidence</th></tr></thead><tbody>{run_rows}</tbody>
</table></body></html>"""


def load_report(workspace: Path, experiment_id: UUID) -> MemoryRagReport:
    path = workspace / "memory-rag" / str(experiment_id) / "memory-rag-report.json"
    return MemoryRagReport.model_validate_json(path.read_text(encoding="utf-8"))


def find_trace(workspace: Path, run_id: UUID) -> Dict[str, object]:
    matches = list((workspace / "memory-rag").glob(f"*/traces/{run_id}.json"))
    if len(matches) != 1:
        raise ValueError(f"expected one trace for {run_id}, found {len(matches)}")
    return cast(Dict[str, object], json.loads(matches[0].read_text(encoding="utf-8")))
