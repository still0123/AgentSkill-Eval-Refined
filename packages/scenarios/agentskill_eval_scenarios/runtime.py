"""Unified scenario validation, execution, persistence, and reporting."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from uuid import UUID

from agentskill_eval_scenarios.adapters import ADAPTERS
from agentskill_eval_scenarios.contracts import (
    EvaluationPlan,
    UnifiedEvaluationResult,
    UnifiedScenarioSpec,
)


class UnifiedScenarioRunner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def validate(self, spec: UnifiedScenarioSpec) -> EvaluationPlan:
        if spec.skill is not None:
            spec.skill.verify()
        return ADAPTERS[spec.scenario].build_plan(spec)

    def run(self, spec: UnifiedScenarioSpec, *, allow_simulation: bool) -> UnifiedEvaluationResult:
        if spec.simulated and not allow_simulation:
            raise ValueError("simulated scenarios require explicit allow_simulation")
        plan = self.validate(spec)
        result = ADAPTERS[spec.scenario].run(spec, plan, self.workspace)
        output = self.output_dir(result.experiment_id)
        output.mkdir(parents=True, exist_ok=True)
        json_path = output / "unified-report.json"
        html_path = output / "unified-report.html"
        payload = result.model_dump_json(indent=2).encode("utf-8")
        self._write_immutable(json_path, payload)
        self._write_immutable(html_path, self._html(result).encode("utf-8"))
        return result

    def output_dir(self, experiment_id: UUID) -> Path:
        return self.workspace / "unified" / str(experiment_id)

    def load(self, experiment_id: UUID) -> UnifiedEvaluationResult:
        path = self.output_dir(experiment_id) / "unified-report.json"
        return UnifiedEvaluationResult.model_validate_json(path.read_bytes())

    @staticmethod
    def _write_immutable(path: Path, payload: bytes) -> None:
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError(f"immutable unified artifact changed: {path}")
            return
        path.write_bytes(payload)
        path.with_suffix(path.suffix + ".sha256").write_text(
            hashlib.sha256(payload).hexdigest() + "\n", encoding="utf-8"
        )

    @staticmethod
    def _html(result: UnifiedEvaluationResult) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        metric_rows = "".join(
            f"<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>"
            for key, value in sorted(result.primary_metrics.items())
        )
        artifact_rows = "".join(
            f"<li>{esc(item.kind)}: <code>{esc(item.path)}</code> · "
            f"<code>{esc(item.sha256)}</code></li>"
            for item in result.artifacts
        )
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(result.plan.name)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:auto;padding:28px}}
table{{border-collapse:collapse}}th,td{{border:1px solid #aaa;padding:7px;text-align:left}}
.warning{{border-left:4px solid #c80;padding:10px;background:#c801}}
code{{overflow-wrap:anywhere}}</style>
</head><body><h1>Unified Evaluation Result</h1>
<p class="warning"><strong>Claim limit:</strong> {esc(result.claim_limit)}</p>
<dl><dt>Scenario</dt><dd>{esc(result.plan.scenario.value)}</dd><dt>Comparison</dt>
<dd>{esc(result.plan.comparison.value)}</dd><dt>Evidence</dt><dd>{esc(result.evidence_class.value)}</dd>
<dt>Simulated</dt><dd>{str(result.simulated).lower()}</dd><dt>Plan SHA-256</dt>
<dd><code>{esc(result.plan.plan_sha256)}</code></dd></dl><h2>Primary metrics</h2>
<table>{metric_rows}</table><h2>Native artifacts</h2><ul>{artifact_rows}</ul>
<footer>No external resources or scripts. All dynamic content is escaped.</footer></body></html>"""
