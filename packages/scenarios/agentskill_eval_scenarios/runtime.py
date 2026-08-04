"""Unified scenario validation, execution, persistence, and reporting."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

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
        experiment_id = uuid5(NAMESPACE_URL, f"unified:{plan.plan_sha256}")
        persisted = self.output_dir(experiment_id) / "unified-report.json"
        if persisted.exists():
            existing = self.load(experiment_id)
            if existing.plan != plan:
                raise ValueError("persisted unified result belongs to another frozen plan")
            return existing
        result = ADAPTERS[spec.scenario].run(spec, plan, self.workspace)
        if result.experiment_id != experiment_id:
            raise ValueError("ScenarioAdapter returned an experiment ID outside the frozen plan")
        output = self.output_dir(result.experiment_id)
        output.mkdir(parents=True, exist_ok=True)
        json_path = output / "unified-report.json"
        html_path = output / "unified-report.html"
        sha256_path = output / "unified-report.json.sha256"
        payload = result.model_dump_json(indent=2).encode("utf-8")
        self._write_immutable(json_path, payload)
        self._write_immutable(sha256_path, hashlib.sha256(payload).hexdigest().encode("utf-8"))
        self._write_immutable(html_path, self._html(result).encode("utf-8"))
        return result

    def load(self, experiment_id: UUID) -> UnifiedEvaluationResult:
        target = self.output_dir(experiment_id) / "unified-report.json"
        return UnifiedEvaluationResult.model_validate_json(target.read_bytes())

    def output_dir(self, experiment_id: UUID) -> Path:
        return self.workspace / "unified-scenarios" / str(experiment_id)

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    @staticmethod
    def _html(result: UnifiedEvaluationResult) -> str:
        payload = result.model_dump_json(indent=2)
        return html.escape(payload)  # simplified; see full version in ds_store