"""Thin adapters over existing scenario-specific runners and graders."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Protocol, Tuple, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import JsonValue

from agentskill_eval_benchmark_gen import DemoExperimentRunner, DemoMode, DemoRunConfig
from agentskill_eval_scenarios.contracts import (
    ArtifactReference,
    EvaluationPlan,
    ScenarioKind,
    UnifiedEvaluationResult,
    UnifiedScenarioSpec,
    VariantDescriptor,
    file_sha256,
)


class ScenarioAdapter(Protocol):
    kind: ScenarioKind

    def build_plan(self, spec: UnifiedScenarioSpec) -> EvaluationPlan: ...

    def run(
        self, spec: UnifiedScenarioSpec, plan: EvaluationPlan, workspace: Path
    ) -> UnifiedEvaluationResult: ...


def _variants(
    spec: UnifiedScenarioSpec, control: str, treatment: str
) -> Tuple[VariantDescriptor, VariantDescriptor]:
    skill_hash = spec.skill.expected_sha256 if spec.skill else None
    return (
        VariantDescriptor(name=control, role="control"),
        VariantDescriptor(name=treatment, role="treatment", skill_sha256=skill_hash),
    )


def _artifact(kind: str, path: Path) -> ArtifactReference:
    return ArtifactReference(kind=kind, path=str(path.resolve()), sha256=file_sha256(path))


class SoftwareEngineeringScenarioAdapter:
    kind = ScenarioKind.SOFTWARE_ENGINEERING

    def build_plan(self, spec: UnifiedScenarioSpec) -> EvaluationPlan:
        config = _load_software_config(spec.native_config)
        skill = spec.skill
        if skill is None:
            raise ValueError("software engineering scenarios require a frozen Skill")
        skill.verify()
        if spec.process_agent is not None:
            raise ValueError(
                "software engineering Process Agent uses the existing real runner path"
            )
        if skill.activation_mode != "native_install":
            raise ValueError("software engineering Skill activation must be native_install")
        dataset_root = _resolved_child(spec.native_config, str(config["dataset_root"]))
        from agentskill_eval_benchmark_gen import DatasetLoader

        dataset = DatasetLoader().load(dataset_root)
        return EvaluationPlan(
            name=spec.name,
            scenario=spec.scenario,
            comparison=spec.comparison,
            native_config_sha256=file_sha256(spec.native_config),
            dataset_name=dataset.manifest.name,
            dataset_sha256=dataset.dataset_sha256,
            case_count=len(dataset.cases),
            agent="deterministic-mock-agent",
            model="no-model",
            skill_name=skill.name,
            skill_version=skill.version,
            skill_activation_mode=skill.activation_mode,
            variants=_variants(spec, "without-skill", skill.name),
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            trace_capabilities=(
                "runner_events",
                "test_result",
                "file_changes",
                "failure_diagnosis",
            ),
            claim_limit=spec.claim_limit,
        )

    def run(
        self, spec: UnifiedScenarioSpec, plan: EvaluationPlan, workspace: Path
    ) -> UnifiedEvaluationResult:
        config = _load_software_config(spec.native_config)
        assert spec.skill is not None
        result = asyncio.run(
            DemoExperimentRunner().run(
                DemoRunConfig(
                    workspace=workspace,
                    dataset_root=_resolved_child(spec.native_config, str(config["dataset_root"])),
                    skill_root=spec.skill.verify(),
                    mode=DemoMode.MOCK,
                    repeats=_config_int(config, "repeats", 1),
                    random_seed=_config_int(config, "random_seed", 2026),
                    bootstrap_resamples=_config_int(config, "bootstrap_resamples", 100),
                    experiment_id=uuid5(NAMESPACE_URL, f"unified:{plan.plan_sha256}"),
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
        )

        native = json.loads(result.report_paths.json_path.read_text(encoding="utf-8"))
        stats = native["statistics"]
        primary_native = stats["primary_assignment_based"]
        primary: Dict[str, JsonValue] = {
            "control_success_rate": primary_native["control_pass_rate"],
            "treatment_success_rate": primary_native["treatment_pass_rate"],
            "absolute_gain": primary_native["absolute_gain"],
            "wins": stats["wtl"]["win"],
            "ties": stats["wtl"]["tie_positive"] + stats["wtl"]["tie_negative"],
            "losses": stats["wtl"]["loss"],
            "invalid": result.invalid_runs,
        }
        return UnifiedEvaluationResult(
            experiment_id=result.experiment_id,
            plan=plan,
            status="completed" if result.invalid_runs == 0 else "invalid",
            primary_metrics=primary,
            scenario_metrics={
                "logical_runs": result.logical_runs,
                "completed_runs": result.completed_runs,
            },
            artifacts=(
                _artifact("native_report_json", result.report_paths.json_path),
                _artifact("native_report_html", result.report_paths.html_path),
            ),
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            claim_limit=spec.claim_limit,
        )


def _load_software_config(path: Path) -> Dict[str, object]:
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("dataset_root"), str):
        raise ValueError("software native config requires dataset_root")
    return cast(Dict[str, object], payload)


def _resolved_child(config_path: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else config_path.parent / path).resolve(strict=True)


def _config_int(config: Dict[str, object], key: str, default: int) -> int:
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"software native config {key} must be an integer")
    return value


ADAPTERS: Dict[ScenarioKind, ScenarioAdapter] = {
    ScenarioKind.SOFTWARE_ENGINEERING: SoftwareEngineeringScenarioAdapter(),
}