"""Offline, escaped real-Agent evidence reports."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import TYPE_CHECKING, Sequence, Tuple, cast
from uuid import uuid5

from pydantic import JsonValue

from agentskill_eval_contracts import (
    ExperimentVariant,
    RealAttemptEvidence,
    RealCaseEvidence,
    RealEvidenceRunManifest,
    RealExperimentReport,
    RealPreflightReport,
)
from agentskill_eval_experiment import ExperimentStatistics, LocalExperimentStore
from agentskill_eval_experiment.storage import load_model
from agentskill_eval_experiment.storage.manifests import model_bytes

if TYPE_CHECKING:
    from agentskill_eval_real_evidence.execution import RealEvidenceStore


class RealEvidenceReportWriter:
    def __init__(
        self, experiment_store: LocalExperimentStore, real_store: "RealEvidenceStore"
    ) -> None:
        self.experiment_store = experiment_store
        self.real_store = real_store

    def write(
        self,
        run: RealEvidenceRunManifest,
        preflight: RealPreflightReport,
        baseline: ExperimentVariant,
        treatment: ExperimentVariant,
        statistics: ExperimentStatistics,
        attempt_paths: Tuple[str, ...],
        bundle_path: Path,
    ) -> Tuple[RealExperimentReport, Path, Path]:
        attempts = tuple(
            load_model(
                (self.real_store.experiment_dir(run.experiment_id) / path).read_bytes(),
                RealAttemptEvidence,
            )
            for path in attempt_paths
        )
        self._assert_uniform_evidence(run, baseline, treatment, attempts)
        case_names = {
            uuid5(preflight.dataset_version_id, f"case:{case_id}"): case_id
            for case_id in preflight.case_ids
        }
        cases = tuple(
            RealCaseEvidence(
                case_id=case_names[item.case_id],
                independence_group=item.independence_group,
                baseline_pass_rate=item.control_pass_rate,
                treatment_pass_rate=item.treatment_pass_rate,
                absolute_gain=item.absolute_gain,
                classification=item.classification,
            )
            for item in statistics.cases
        )
        capability_unavailable = tuple(
            sorted({name for attempt in attempts for name in attempt.capability_unavailable})
        )
        by_variant = {
            str(item.variant_id): {
                "assigned_runs": item.assigned_runs,
                "pass_runs": item.pass_runs,
                "invalid_runs": item.invalid_runs,
                "total_cost_microusd": item.total_cost_microusd,
                "cost_per_success_microusd": item.cost_per_success_microusd,
            }
            for item in statistics.variants
        }
        report = RealExperimentReport(
            run=run,
            dataset_version_id=preflight.dataset_version_id,
            dataset_name=preflight.dataset_name,
            dataset_version=preflight.dataset_version,
            dataset_sha256=preflight.dataset_sha256,
            runner_snapshot=baseline.runner_snapshot,
            agent_snapshot=baseline.agent_snapshot,
            skill_sha256=(
                treatment.skill_snapshot.content_sha256 if treatment.skill_snapshot else "0" * 64
            ),
            baseline_skill_sha256=(
                baseline.skill_snapshot.content_sha256
                if baseline.skill_snapshot is not None
                else None
            ),
            baseline_pass_rate=statistics.primary_assignment_based.control_pass_rate,
            treatment_pass_rate=statistics.primary_assignment_based.treatment_pass_rate,
            absolute_gain=statistics.primary_assignment_based.absolute_gain,
            wtl=statistics.wtl.model_dump(mode="json"),
            invalid_runs=sum(item.invalid_runs for item in statistics.variants),
            token_summary=statistics.tokens.model_dump(mode="json"),
            latency_summary=statistics.latency_ms.model_dump(mode="json"),
            cost_summary={
                "comparison": statistics.cost_microusd.model_dump(mode="json"),
                "by_variant": cast(JsonValue, by_variant),
                "authorized_max_microusd": run.max_cost_microusd,
                "estimated_per_run_microusd": preflight.estimated_cost_per_run_microusd,
                "estimated_total_microusd": (
                    run.planned_runs * preflight.estimated_cost_per_run_microusd
                ),
                "observed_or_reserved_microusd": run.observed_or_reserved_cost_microusd,
            },
            cases=cases,
            attempt_evidence_paths=attempt_paths,
            capability_unavailable=capability_unavailable,
            replay_bundle_path=str(bundle_path),
            replay_bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            simulated=run.simulated,
            evidence_class=run.evidence_class,
            provider=run.provider,
            model=run.model,
            real_run_confirmed=run.real_run_confirmed,
            inference_note=statistics.inference_note,
            claim_limit=run.claim_limit,
        )
        json_path = self.real_store.report_json(run.experiment_id)
        html_path = self.real_store.report_html(run.experiment_id)
        report_bytes = model_bytes(report)
        self.real_store.writer.write(json_path, report_bytes)
        self.real_store.writer.write(
            json_path.with_suffix(".sha256"),
            (hashlib.sha256(report_bytes).hexdigest() + "\n").encode(),
        )
        self.real_store.writer.write(html_path, self._html(report).encode("utf-8"))
        return report, json_path, html_path

    @staticmethod
    def _assert_uniform_evidence(
        run: RealEvidenceRunManifest,
        baseline: ExperimentVariant,
        treatment: ExperimentVariant,
        attempts: Sequence[RealAttemptEvidence],
    ) -> None:
        values = {
            bool(variant.runner_snapshot.config.get("simulated"))
            for variant in (baseline, treatment)
        }
        values.update(item.simulated for item in attempts)
        if values != {run.simulated}:
            raise ValueError("real and simulated evidence cannot be aggregated")
        if any(item.evidence_class != run.evidence_class for item in attempts):
            raise ValueError("mixed evidence classes cannot be aggregated")
        if any(item.provider != run.provider or item.model != run.model for item in attempts):
            raise ValueError("mixed provider/model Runs cannot be aggregated")

    @staticmethod
    def _html(report: RealExperimentReport) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        case_rows = "".join(
            "<tr>"
            f"<td>{esc(item.case_id)}</td><td>{esc(item.independence_group)}</td>"
            f"<td>{item.baseline_pass_rate:.3f}</td>"
            f"<td>{item.treatment_pass_rate:.3f}</td>"
            f"<td>{item.absolute_gain:+.3f}</td><td>{esc(item.classification)}</td>"
            "</tr>"
            for item in report.cases
        )
        evidence_rows = "".join(
            "<li>"
            f'<a href="../{esc(path)}">{esc(path)}</a> · '
            f'<a href="../{esc(path.rsplit("/", 1)[0])}/trace.json">trace</a> · '
            f'<a href="../{esc(path.rsplit("/", 1)[0])}/failure-diagnosis.json">diagnosis</a>'
            "</li>"
            for path in report.attempt_evidence_paths
        )
        unavailable = ", ".join(report.capability_unavailable) or "none"
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Real Agent Evidence — {esc(report.run.experiment_id)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:auto;padding:28px}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #aaa;padding:7px}}
.warning{{border-left:4px solid #c80;padding:10px;background:#c801}}</style></head><body>
<h1>Real Agent Evaluation Evidence</h1>
<p class="warning"><strong>Claim limit:</strong> {esc(report.claim_limit)}</p>
<dl><dt>simulated</dt><dd>{str(report.simulated).lower()}</dd>
<dt>evidence class</dt><dd>{esc(report.evidence_class.value)}</dd>
<dt>provider/model</dt><dd>{esc(report.provider)} / {esc(report.model)}</dd>
<dt>real run confirmed</dt><dd>{str(report.real_run_confirmed).lower()}</dd>
<dt>dataset</dt><dd>{esc(report.dataset_name)}@{esc(report.dataset_version)} ·
<code>{esc(report.dataset_sha256)}</code></dd>
<dt>runner</dt><dd>{esc(report.runner_snapshot.name)} {esc(report.runner_snapshot.version)} ·
<code>{esc(report.runner_snapshot.binary_sha256)}</code></dd>
<dt>agent</dt><dd>{esc(report.agent_snapshot.engine)} {esc(report.agent_snapshot.engine_version)} ·
{esc(report.agent_snapshot.model)} · executable
<code>{esc(report.runner_snapshot.config.get("agent_executable_sha256", "unavailable"))}</code></dd>
<dt>Skill hash</dt><dd><code>{esc(report.skill_sha256)}</code></dd>
<dt>Baseline Skill hash</dt><dd><code>{esc(report.baseline_skill_sha256 or "none")}</code></dd></dl>
<h2>Outcome</h2><p>Baseline {esc(report.baseline_pass_rate)} · Treatment
{esc(report.treatment_pass_rate)} · Absolute gain {esc(report.absolute_gain)} · Invalid
{report.invalid_runs}</p><p>{esc(report.inference_note)}</p>
<h2>Cases</h2><table><thead><tr><th>Case</th><th>Group</th><th>Baseline</th>
<th>Treatment</th><th>Gain</th><th>Class</th></tr></thead><tbody>{case_rows}</tbody></table>
<h2>Efficiency</h2><pre>{esc(report.token_summary)}\n{esc(report.latency_summary)}\n
{esc(report.cost_summary)}</pre>
<h2>Trace capability unavailable</h2><p>{esc(unavailable)}</p>
<h2>Attempt evidence</h2><ul>{evidence_rows}</ul>
<p>Replay bundle: <code>{esc(report.replay_bundle_path)}</code> ·
<code>{esc(report.replay_bundle_sha256)}</code></p>
<footer>No external resources or scripts. All dynamic text is HTML escaped.</footer>
</body></html>"""
