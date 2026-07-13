"""Script-free, escaped JSON and HTML reports for P0 experiments."""

from __future__ import annotations

import html
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence
from uuid import UUID

from agentskill_eval_contracts import (
    ExperimentManifest,
    ExperimentVariant,
    FailureDiagnosis,
    PairTraceDiff,
    Run,
    TraceManifest,
)
from agentskill_eval_experiment.statistics import (
    ConfidenceInterval,
    EfficiencyComparison,
    EstimandSummary,
    ExperimentStatistics,
)
from agentskill_eval_experiment.storage import ExperimentLayout, LocalExperimentStore
from agentskill_eval_trace_intelligence import compare_traces


@dataclass(frozen=True)
class StaticReportPaths:
    json_path: Path
    html_path: Path


@dataclass(frozen=True)
class TraceReportData:
    traces: tuple[TraceManifest, ...]
    diagnoses: tuple[FailureDiagnosis, ...]
    pair_diffs: tuple[PairTraceDiff, ...]


class StaticReportWriter:
    def __init__(self, store: LocalExperimentStore) -> None:
        self.store = store

    def write(
        self,
        experiment_id: UUID,
        statistics: ExperimentStatistics,
    ) -> StaticReportPaths:
        experiment = self.store.load_experiment(experiment_id)
        variants = self.store.list_variants(experiment_id)
        if statistics.experiment_id != experiment_id:
            raise ValueError("statistics belong to another experiment")
        layout = ExperimentLayout(self.store.workspace, experiment_id)
        trace_data = self._trace_report_data(experiment_id, statistics)
        json_path = layout.reports / "report.json"
        html_path = layout.reports / "report.html"
        bundle = {
            "report_schema_version": "ase/report/v1alpha1",
            "experiment": experiment.model_dump(mode="json", round_trip=True),
            "variants": [
                variant.model_dump(mode="json", round_trip=True)
                for variant in sorted(variants, key=lambda item: str(item.id))
            ],
            "statistics": statistics.model_dump(mode="json", round_trip=True),
            "trace_intelligence": {
                "traces": [
                    item.model_dump(mode="json", round_trip=True) for item in trace_data.traces
                ],
                "diagnoses": [
                    item.model_dump(mode="json", round_trip=True) for item in trace_data.diagnoses
                ],
                "pair_diffs": [
                    item.model_dump(mode="json", round_trip=True) for item in trace_data.pair_diffs
                ],
            },
        }
        self.store.writer.write(
            json_path,
            (json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        self.store.writer.write(
            html_path,
            self._html(experiment, variants, statistics, trace_data).encode("utf-8"),
        )
        return StaticReportPaths(json_path=json_path, html_path=html_path)

    def _html(
        self,
        experiment: ExperimentManifest,
        variants: Sequence[ExperimentVariant],
        result: ExperimentStatistics,
        trace_data: TraceReportData,
    ) -> str:
        names = {variant.id: variant.name for variant in variants}
        control_name = names.get(result.control_variant_id, str(result.control_variant_id))
        treatment_name = names.get(result.treatment_variant_id, str(result.treatment_variant_id))
        primary = result.primary_assignment_based
        capability = result.sensitivity_capability
        case_rows = "".join(
            "<tr>"
            f"<td><code>{self._escape(str(case.case_id))}</code></td>"
            f"<td>{self._escape(case.independence_group)}</td>"
            f"<td>{self._percent(case.control_pass_rate)}</td>"
            f"<td>{self._percent(case.treatment_pass_rate)}</td>"
            f"<td>{self._signed_percent(case.absolute_gain)}</td>"
            f'<td><span class="tag {self._class_name(case.classification)}">'
            f"{self._escape(case.classification)}</span></td>"
            "</tr>"
            for case in result.cases
        )
        variant_rows = "".join(
            "<tr>"
            f"<td>{self._escape(names.get(item.variant_id, str(item.variant_id)))}</td>"
            f"<td>{item.assigned_runs}</td><td>{item.pass_runs}</td>"
            f"<td>{item.fail_runs}</td><td>{item.invalid_runs}</td>"
            f"<td>{self._number(item.cost_per_success_microusd)}</td>"
            "</tr>"
            for item in result.variants
        )
        efficiency_rows = "".join(
            self._efficiency_row(label, metric)
            for label, metric in (
                ("Token", result.tokens),
                ("Latency", result.latency_ms),
                ("Cost", result.cost_microusd),
            )
        )
        evidence_rows = self._evidence_rows(experiment.id, names)
        scope_banner = self._scope_banner(experiment)
        trace_section = self._trace_section(trace_data)
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{self._escape(experiment.name)} — AgentSkill-Eval</title>
<style>
:root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }}
body {{ margin: 0 auto; max-width: 1180px; padding: 32px 20px 80px; line-height: 1.45; }}
h1, h2 {{ line-height: 1.2; }} .muted {{ opacity: .72; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 12px; }}
.card {{ border: 1px solid #8886; border-radius: 10px; padding: 14px; }}
.value {{ font-size: 1.55rem; font-weight: 700; margin-top: 5px; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0 28px; }}
th, td {{ border-bottom: 1px solid #8885; padding: 8px; text-align: left; }}
th {{ font-size: .86rem; }} code {{ overflow-wrap: anywhere; }}
.tag {{ border-radius: 999px; padding: 2px 8px; font-size: .82rem; }}
.win {{ background: #169c4933; }} .loss {{ background: #d33b3433; }}
.tie-positive, .tie-negative {{ background: #8884; }}
.warning {{ border-left: 4px solid #d99b22; padding: 10px 14px; background: #d99b2218; }}
</style>
</head>
<body>
<header><p class="muted">AgentSkill-Eval · offline report</p>
<h1>{self._escape(experiment.name)}</h1>
<p><code>{self._escape(str(experiment.id))}</code></p></header>
{scope_banner}
<section><h2>Primary assignment-based estimate</h2>
<p class="muted">Invalid terminal runs count as failures. Groups receive equal weight.</p>
<div class="grid">
{self._estimate_cards(control_name, treatment_name, primary)}
<div class="card"><div>Complete blocks</div>
<div class="value">{self._percent(result.complete_block_ratio)}</div></div>
<div class="card"><div>Valid paired blocks</div>
<div class="value">{self._percent(result.valid_block_ratio)}</div></div>
</div>
<p class="warning">{self._escape(result.inference_note)} ·
{result.independence_group_count} independent groups ·
{result.bootstrap_resamples} bootstrap resamples.</p>
<p class="muted">Bootstrap seed {result.bootstrap_seed} · W/T/L threshold
{result.majority_threshold:.3f} · weighting {self._escape(result.weighting)}.</p>
</section>
<section><h2>Capability sensitivity estimate</h2>
<p class="muted">Only PairBlocks where both arms produced valid pass/fail outcomes.</p>
<div class="grid">{self._estimate_cards(control_name, treatment_name, capability)}</div>
</section>
<section><h2>Win / Tie / Loss</h2>
<div class="grid">
<div class="card"><div>Win</div><div class="value">{result.wtl.win}</div></div>
<div class="card"><div>Tie+</div><div class="value">{result.wtl.tie_positive}</div></div>
<div class="card"><div>Tie−</div><div class="value">{result.wtl.tie_negative}</div></div>
<div class="card"><div>Loss</div><div class="value">{result.wtl.loss}</div></div>
</div></section>
<section><h2>Run validity</h2><table><thead><tr>
<th>Variant</th><th>Assigned</th><th>Pass</th><th>Fail</th><th>Invalid</th>
<th>Cost / success (micro-USD)</th>
</tr></thead><tbody>{variant_rows}</tbody></table></section>
<section><h2>Efficiency</h2><table><thead><tr>
<th>Metric</th><th>Control mean</th><th>Treatment mean</th>
<th>Relative overhead</th><th>Paired median Δ</th><th>Observed pairs</th>
</tr></thead><tbody>{efficiency_rows}</tbody></table></section>
<section><h2>Case comparisons</h2><table><thead><tr>
<th>Case</th><th>Independence group</th><th>Control</th>
<th>Treatment</th><th>Gain</th><th>Class</th>
</tr></thead><tbody>{case_rows}</tbody></table></section>
{trace_section}
<section><h2>Evidence</h2><p class="muted">Links are relative to this offline report.</p>
<table><thead><tr><th>Run</th><th>Variant</th><th>Outcome</th>
<th>Attempt</th><th>Skill installed</th><th>Baseline clean</th><th>Secret scan</th>
<th>Trace</th><th>Diagnosis</th>
<th>Artifacts</th><th>Raw result</th></tr></thead>
<tbody>{evidence_rows}</tbody></table></section>
<footer class="muted"><p>No external resources or scripts.
Machine-readable evidence: <code>report.json</code>.</p></footer>
</body></html>
"""

    def _scope_banner(self, experiment: ExperimentManifest) -> str:
        mode = experiment.protocol_snapshot.get("evidence_mode")
        demo_only = experiment.protocol_snapshot.get("demo_only") is True
        if mode == "simulated_fixture":
            message = (
                "SIMULATED DEMO: outcomes are deterministic fixtures for exercising the platform "
                "and are not Agent or Skill performance evidence."
            )
        elif demo_only:
            message = (
                "PUBLIC SYNTHETIC DEMO: outcomes are observed Agent runs on public smoke graders "
                "and do not support generalization claims."
            )
        else:
            return ""
        return f'<p class="warning">{self._escape(message)}</p>'

    def _estimate_cards(
        self, control_name: str, treatment_name: str, summary: EstimandSummary
    ) -> str:
        return (
            self._card(control_name, summary.control_pass_rate, summary.control_ci)
            + self._card(treatment_name, summary.treatment_pass_rate, summary.treatment_ci)
            + self._card("Absolute gain", summary.absolute_gain, summary.gain_ci, signed=True)
            + self._card("Relative gain", summary.relative_gain, None, signed=True)
        )

    def _card(
        self,
        label: str,
        value: Optional[float],
        interval: Optional[ConfidenceInterval],
        *,
        signed: bool = False,
    ) -> str:
        formatted = self._signed_percent(value) if signed else self._percent(value)
        ci = ""
        if interval is not None:
            ci = (
                f'<div class="muted">95% CI {self._percent(interval.low)}–'
                f"{self._percent(interval.high)}</div>"
            )
        return (
            f'<div class="card"><div>{self._escape(label)}</div>'
            f'<div class="value">{formatted}</div>{ci}</div>'
        )

    def _efficiency_row(self, label: str, metric: EfficiencyComparison) -> str:
        overhead = self._signed_percent(metric.relative_overhead)
        if metric.relative_overhead_ci is not None:
            overhead += (
                f'<div class="muted">95% CI '
                f"{self._signed_percent(metric.relative_overhead_ci.low)}–"
                f"{self._signed_percent(metric.relative_overhead_ci.high)}</div>"
            )
        paired_delta = self._signed_number(metric.paired_median_delta)
        if metric.paired_median_delta_ci is not None:
            paired_delta += (
                f'<div class="muted">95% CI '
                f"{self._signed_number(metric.paired_median_delta_ci.low)}–"
                f"{self._signed_number(metric.paired_median_delta_ci.high)}</div>"
            )
        return (
            "<tr>"
            f'<td>{self._escape(label)} <span class="muted">'
            f"({self._escape(metric.unit)})</span></td>"
            f"<td>{self._number(metric.control_mean)}</td>"
            f"<td>{self._number(metric.treatment_mean)}</td>"
            f"<td>{overhead}</td>"
            f"<td>{paired_delta}</td>"
            f"<td>{metric.observed_pairs}</td></tr>"
        )

    def _evidence_rows(self, experiment_id: UUID, names: dict[UUID, str]) -> str:
        layout = ExperimentLayout(self.store.workspace, experiment_id)
        rows = []
        for run in self.store.list_runs(experiment_id):
            attempt = self.store.load_selected_attempt(experiment_id, run)
            if attempt is None:
                continue
            prefix = f"../runs/{run.id}/attempts/{attempt.attempt_no}"
            artifact_path = layout.artifact_manifest(run.id, attempt.attempt_no)
            activation_path = layout.activation_evidence(run.id, attempt.attempt_no)
            security_path = layout.security_scan(run.id, attempt.attempt_no)
            trace_path = layout.trace_manifest(run.id, attempt.attempt_no)
            diagnosis_path = layout.failure_diagnosis(run.id, attempt.attempt_no)
            raw_result_path = (
                layout.attempt_root(run.id, attempt.attempt_no) / "raw-runner" / "result.json"
            )
            artifact_link = (
                f'<a href="{prefix}/artifacts/manifest.json">manifest</a>'
                if artifact_path.is_file()
                else "N/A"
            )
            raw_link = (
                f'<a href="{prefix}/raw-runner/result.json">result.json</a>'
                if raw_result_path.is_file()
                else "N/A"
            )
            outcome = run.evaluation_outcome.value if run.evaluation_outcome else "cancelled"
            installed = "N/A"
            baseline_clean = "N/A"
            if activation_path.is_file():
                activation = self.store.load_activation_evidence(
                    experiment_id, run.id, attempt.attempt_no
                )
                installed = (
                    f'<a href="{prefix}/skill-activation.json">'
                    f"{self._optional_bool(activation.installed)}</a>"
                )
                baseline_clean = (
                    f'<a href="{prefix}/skill-activation.json">'
                    f"{self._optional_bool(activation.baseline_clean)}</a>"
                )
            security = "N/A"
            if security_path.is_file():
                scan_status = self._escape(
                    self.store.load_security_scan(experiment_id, run.id, attempt.attempt_no).status
                )
                security = f'<a href="{prefix}/security-scan.json">{scan_status}</a>'
            trace_link = (
                f'<a href="{prefix}/trace.json">trace</a>' if trace_path.is_file() else "N/A"
            )
            diagnosis_link = (
                f'<a href="{prefix}/failure-diagnosis.json">diagnosis</a>'
                if diagnosis_path.is_file()
                else "N/A"
            )
            rows.append(
                "<tr>"
                f"<td><code>{self._escape(str(run.id))}</code></td>"
                f"<td>{self._escape(names.get(run.variant_id, str(run.variant_id)))}</td>"
                f"<td>{self._escape(outcome)}</td><td>{attempt.attempt_no}</td>"
                f"<td>{installed}</td><td>{baseline_clean}</td><td>{security}</td>"
                f"<td>{trace_link}</td><td>{diagnosis_link}</td>"
                f"<td>{artifact_link}</td><td>{raw_link}</td></tr>"
            )
        return "".join(rows)

    def _trace_report_data(
        self, experiment_id: UUID, statistics: ExperimentStatistics
    ) -> TraceReportData:
        traces = []
        diagnoses = []
        trace_by_run = {}
        runs = self.store.list_runs(experiment_id)
        for run in runs:
            attempt = self.store.load_selected_attempt(experiment_id, run)
            if attempt is None:
                continue
            layout = ExperimentLayout(self.store.workspace, experiment_id)
            if layout.trace_manifest(run.id, attempt.attempt_no).is_file():
                trace = self.store.load_trace_manifest(experiment_id, run.id, attempt.attempt_no)
                traces.append(trace)
                trace_by_run[run.id] = trace
            if layout.failure_diagnosis(run.id, attempt.attempt_no).is_file():
                diagnoses.append(
                    self.store.load_failure_diagnosis(experiment_id, run.id, attempt.attempt_no)
                )
        runs_by_block: Dict[UUID, Dict[UUID, Run]] = {}
        for run in runs:
            runs_by_block.setdefault(run.pair_block_id, {})[run.variant_id] = run
        pair_diffs = []
        for block_id, variants in sorted(runs_by_block.items(), key=lambda item: str(item[0])):
            control = variants.get(statistics.control_variant_id)
            treatment = variants.get(statistics.treatment_variant_id)
            if control is None or treatment is None:
                continue
            control_trace = trace_by_run.get(control.id)
            treatment_trace = trace_by_run.get(treatment.id)
            if control_trace is not None and treatment_trace is not None:
                pair_diffs.append(compare_traces(block_id, control_trace, treatment_trace))
        return TraceReportData(tuple(traces), tuple(diagnoses), tuple(pair_diffs))

    def _trace_section(self, data: TraceReportData) -> str:
        labels = Counter(
            finding.label.value for diagnosis in data.diagnoses for finding in diagnosis.findings
        )
        label_text = (
            ", ".join(f"{self._escape(label)}={count}" for label, count in sorted(labels.items()))
            or "none"
        )
        mean_distance = (
            sum(item.sequence_edit_distance for item in data.pair_diffs) / len(data.pair_diffs)
            if data.pair_diffs
            else None
        )
        return (
            '<section><h2>Trace intelligence</h2><div class="grid">'
            f'<div class="card"><div>Observed traces</div>'
            f'<div class="value">{len(data.traces)}</div></div>'
            f'<div class="card"><div>Paired trace diffs</div>'
            f'<div class="value">{len(data.pair_diffs)}</div></div>'
            f'<div class="card"><div>Mean sequence edit distance</div>'
            f'<div class="value">{self._number(mean_distance)}</div></div></div>'
            f'<p class="muted">Rule diagnoses: {label_text}. UNKNOWN means the '
            "system abstained because observable evidence was insufficient.</p></section>"
        )

    @staticmethod
    def _optional_bool(value: Optional[bool]) -> str:
        if value is None:
            return "unsupported"
        return "yes" if value else "no"

    @staticmethod
    def _escape(value: str) -> str:
        return html.escape(value, quote=True)

    @staticmethod
    def _class_name(value: str) -> str:
        return html.escape(value.replace("_", "-"), quote=True)

    @staticmethod
    def _percent(value: Optional[float]) -> str:
        return "N/A" if value is None else f"{value * 100:.1f}%"

    @staticmethod
    def _signed_percent(value: Optional[float]) -> str:
        return "N/A" if value is None else f"{value * 100:+.1f}%"

    @staticmethod
    def _number(value: Optional[float]) -> str:
        return "N/A" if value is None else f"{value:,.2f}"

    @staticmethod
    def _signed_number(value: Optional[float]) -> str:
        return "N/A" if value is None else f"{value:+,.2f}"
