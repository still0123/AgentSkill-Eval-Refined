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
from agentskill_eval_mcp_lab import LabConfig as McpLabConfig
from agentskill_eval_mcp_lab import McpDataset, McpLabRunner
from agentskill_eval_memory_rag_lab import LabConfig as MemoryRagLabConfig
from agentskill_eval_memory_rag_lab import MemoryRagDataset, MemoryRagLabRunner
from agentskill_eval_scenarios.contracts import (
    ArtifactReference,
    EvaluationPlan,
    ScenarioKind,
    UnifiedEvaluationResult,
    UnifiedScenarioSpec,
    VariantDescriptor,
    file_sha256,
)
from agentskill_eval_scenarios.interactive import InteractiveRunEvidence
from agentskill_eval_scenarios.interactive_runtime import (
    InteractiveMcpController,
    InteractiveMemoryRagController,
)
from agentskill_eval_scenarios.process_agent import ProcessScenarioAgentClient


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


class McpScenarioAdapter:
    kind = ScenarioKind.MCP_TOOL

    def build_plan(self, spec: UnifiedScenarioSpec) -> EvaluationPlan:
        config = McpLabConfig.load(spec.native_config)
        _assert_simulation_boundary(spec, config.simulated)
        dataset = McpDataset.load(config.dataset, allowed_root=config.dataset.parent)
        if spec.skill:
            spec.skill.verify()
            expected_mode = "process_prompt" if spec.process_agent else "precompiled_plan"
            if spec.skill.activation_mode != expected_mode:
                raise ValueError(f"MCP Lab Skill activation must be {expected_mode}")
        agent = spec.process_agent.name if spec.process_agent else config.agent
        model = "process-json" if spec.process_agent else config.model
        return EvaluationPlan(
            name=spec.name,
            scenario=spec.scenario,
            comparison=spec.comparison,
            native_config_sha256=file_sha256(spec.native_config),
            dataset_name=dataset.name,
            dataset_sha256=file_sha256(config.dataset),
            case_count=len(dataset.cases),
            agent=agent,
            model=model,
            agent_version=spec.process_agent.version if spec.process_agent else None,
            agent_executable_sha256=(
                spec.process_agent.expected_sha256 if spec.process_agent else None
            ),
            interaction_mode=(
                spec.process_agent.interaction_mode if spec.process_agent else "plan_once"
            ),
            max_interaction_steps=(
                spec.process_agent.max_steps
                if spec.process_agent and spec.process_agent.interaction_mode == "step_loop"
                else None
            ),
            skill_name=spec.skill.name if spec.skill else None,
            skill_version=spec.skill.version if spec.skill else None,
            skill_activation_mode=spec.skill.activation_mode if spec.skill else None,
            variants=_variants(
                spec, "without-skill", spec.skill.name if spec.skill else "treatment"
            ),
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            trace_capabilities=(
                "tool_selection",
                "tool_parameters",
                "recovery",
                "side_effects",
                *(("agent_decision",) if spec.process_agent else ()),
                *(
                    ("agent_observation_loop",)
                    if spec.process_agent and spec.process_agent.interaction_mode == "step_loop"
                    else ()
                ),
            ),
            claim_limit=spec.claim_limit,
        )

    def run(
        self, spec: UnifiedScenarioSpec, plan: EvaluationPlan, workspace: Path
    ) -> UnifiedEvaluationResult:
        native_workspace = workspace / "native" / self.kind.value / plan.plan_sha256
        client = ProcessScenarioAgentClient(spec.process_agent) if spec.process_agent else None
        interactive_evidence: list[InteractiveRunEvidence] = []

        def provide_plan(case, variant):  # type: ignore[no-untyped-def]
            if client is None:
                raise AssertionError("plan provider requires Process Scenario Agent")
            skill = spec.skill if variant == "with_guidance" else None
            return client.decide_mcp(case, variant, skill)

        def provide_outcome(case, adapter, variant):  # type: ignore[no-untyped-def]
            if client is None:
                raise AssertionError("outcome provider requires Process Scenario Agent")
            skill = spec.skill if variant == "with_guidance" else None
            outcome, evidence = InteractiveMcpController().run(
                case, adapter, client, variant, skill
            )
            interactive_evidence.append(evidence)
            return outcome

        step_loop = bool(client and client.spec.interaction_mode == "step_loop")

        artifacts = McpLabRunner(native_workspace).run(
            McpLabConfig.load(spec.native_config),
            plan_provider=provide_plan if client and not step_loop else None,
            outcome_provider=provide_outcome if step_loop else None,
        )
        metrics = artifacts.report.paired_metrics
        primary: Dict[str, JsonValue] = {
            "control_success_rate": metrics.without_mcp_success_rate,
            "treatment_success_rate": metrics.with_mcp_success_rate,
            "absolute_gain": metrics.with_mcp_success_rate - metrics.without_mcp_success_rate,
            "wins": metrics.wins,
            "ties": metrics.ties,
            "losses": metrics.losses,
            "invalid": metrics.invalid,
        }
        artifact_refs = [
            _artifact("native_report_json", artifacts.report_json),
            _artifact("native_report_html", artifacts.report_html),
        ]
        if client is not None:
            artifact_refs.append(_write_decision_evidence(native_workspace, client))
        if interactive_evidence:
            artifact_refs.append(
                _write_interactive_evidence(native_workspace, interactive_evidence)
            )
        return UnifiedEvaluationResult(
            experiment_id=uuid5(NAMESPACE_URL, f"unified:{plan.plan_sha256}"),
            plan=plan,
            status="completed" if metrics.invalid == 0 else "invalid",
            primary_metrics=primary,
            scenario_metrics=cast(Dict[str, JsonValue], metrics.model_dump(mode="json")),
            artifacts=tuple(artifact_refs),
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            claim_limit=spec.claim_limit,
        )


class MemoryRagScenarioAdapter:
    kind = ScenarioKind.MEMORY_RAG

    def build_plan(self, spec: UnifiedScenarioSpec) -> EvaluationPlan:
        config = MemoryRagLabConfig.load(spec.native_config)
        _assert_simulation_boundary(spec, config.simulated)
        dataset = MemoryRagDataset.load(config.dataset, allowed_root=config.dataset.parent)
        if spec.skill:
            spec.skill.verify()
            expected_mode = "process_prompt" if spec.process_agent else "precompiled_plan"
            if spec.skill.activation_mode != expected_mode:
                raise ValueError(f"Memory/RAG Lab Skill activation must be {expected_mode}")
        agent = spec.process_agent.name if spec.process_agent else config.agent
        model = "process-json" if spec.process_agent else config.model
        return EvaluationPlan(
            name=spec.name,
            scenario=spec.scenario,
            comparison=spec.comparison,
            native_config_sha256=file_sha256(spec.native_config),
            dataset_name=dataset.name,
            dataset_sha256=file_sha256(config.dataset),
            case_count=(
                len(config.selected_case_ids) if config.selected_case_ids else len(dataset.cases)
            ),
            agent=agent,
            model=model,
            agent_version=spec.process_agent.version if spec.process_agent else None,
            agent_executable_sha256=(
                spec.process_agent.expected_sha256 if spec.process_agent else None
            ),
            interaction_mode=(
                spec.process_agent.interaction_mode if spec.process_agent else "plan_once"
            ),
            max_interaction_steps=(
                spec.process_agent.max_steps
                if spec.process_agent and spec.process_agent.interaction_mode == "step_loop"
                else None
            ),
            skill_name=spec.skill.name if spec.skill else None,
            skill_version=spec.skill.version if spec.skill else None,
            skill_activation_mode=spec.skill.activation_mode if spec.skill else None,
            variants=_variants(spec, "control", spec.skill.name if spec.skill else "treatment"),
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            trace_capabilities=(
                "retrieval",
                "citations",
                "memory_lifecycle",
                "memory_safety",
                *(("agent_decision",) if spec.process_agent else ()),
                *(
                    ("agent_observation_loop",)
                    if spec.process_agent and spec.process_agent.interaction_mode == "step_loop"
                    else ()
                ),
            ),
            claim_limit=spec.claim_limit,
        )

    def run(
        self, spec: UnifiedScenarioSpec, plan: EvaluationPlan, workspace: Path
    ) -> UnifiedEvaluationResult:
        native_workspace = workspace / "native" / self.kind.value / plan.plan_sha256
        client = ProcessScenarioAgentClient(spec.process_agent) if spec.process_agent else None
        interactive_evidence: list[InteractiveRunEvidence] = []

        def provide_plan(case, pair_type, variant):  # type: ignore[no-untyped-def]
            if client is None:
                raise AssertionError("plan provider requires Process Scenario Agent")
            skill = spec.skill if variant == "treatment" else None
            return client.decide_memory_rag(case, pair_type, variant, skill)

        def provide_outcome(case, retriever, memory, pair_type, variant):  # type: ignore[no-untyped-def]
            if client is None:
                raise AssertionError("outcome provider requires Process Scenario Agent")
            skill = spec.skill if variant == "treatment" else None
            outcome, evidence = InteractiveMemoryRagController().run(
                case, retriever, memory, client, pair_type, variant, skill
            )
            interactive_evidence.append(evidence)
            return outcome

        step_loop = bool(client and client.spec.interaction_mode == "step_loop")

        artifacts = MemoryRagLabRunner(native_workspace).run(
            MemoryRagLabConfig.load(spec.native_config),
            plan_provider=provide_plan if client and not step_loop else None,
            outcome_provider=provide_outcome if step_loop else None,
        )
        controls = [item for item in artifacts.report.runs if item.run.variant == "control"]
        treatments = [item for item in artifacts.report.runs if item.run.variant == "treatment"]
        valid_pairs = [
            (control, next(item for item in treatments if item.run.case_id == control.run.case_id))
            for control in controls
        ]
        valid = [
            pair
            for pair in valid_pairs
            if "invalid" not in {pair[0].score.outcome, pair[1].score.outcome}
        ]

        def mean(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        control_rate = mean([float(item[0].score.outcome == "pass") for item in valid])
        treatment_rate = mean([float(item[1].score.outcome == "pass") for item in valid])
        primary: Dict[str, JsonValue] = {
            "control_success_rate": control_rate,
            "treatment_success_rate": treatment_rate,
            "absolute_gain": treatment_rate - control_rate,
            "wins": sum(item[1].score.final_score > item[0].score.final_score for item in valid),
            "ties": sum(item[1].score.final_score == item[0].score.final_score for item in valid),
            "losses": sum(item[1].score.final_score < item[0].score.final_score for item in valid),
            "invalid": max(
                len(valid_pairs) - len(valid),
                int(any(item.termination != "final" for item in interactive_evidence)),
            ),
        }
        scenario_metrics: Dict[str, JsonValue] = {
            "pair_types": cast(
                JsonValue,
                [item.model_dump(mode="json") for item in artifacts.report.paired_metrics],
            ),
            "interactive_nonfinal_runs": sum(
                item.termination != "final" for item in interactive_evidence
            ),
        }
        artifact_refs = [
            _artifact("native_report_json", artifacts.report_json),
            _artifact("native_report_html", artifacts.report_html),
        ]
        if client is not None:
            artifact_refs.append(_write_decision_evidence(native_workspace, client))
        if interactive_evidence:
            artifact_refs.append(
                _write_interactive_evidence(native_workspace, interactive_evidence)
            )
        return UnifiedEvaluationResult(
            experiment_id=uuid5(NAMESPACE_URL, f"unified:{plan.plan_sha256}"),
            plan=plan,
            status=(
                "completed"
                if len(valid_pairs) == len(valid)
                and all(item.termination == "final" for item in interactive_evidence)
                else "invalid"
            ),
            primary_metrics=primary,
            scenario_metrics=scenario_metrics,
            artifacts=tuple(artifact_refs),
            simulated=spec.simulated,
            evidence_class=spec.evidence_class,
            claim_limit=spec.claim_limit,
        )


class SoftwareEngineeringScenarioAdapter:
    kind = ScenarioKind.SOFTWARE_ENGINEERING

    def build_plan(self, spec: UnifiedScenarioSpec) -> EvaluationPlan:
        config = _load_software_config(spec.native_config)
        skill = spec.skill
        if skill is None:
            raise ValueError("software engineering scenarios require a frozen Skill")
        skill.verify()
        _assert_simulation_boundary(spec, native_simulated=True)
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
        import json

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


def _assert_simulation_boundary(spec: UnifiedScenarioSpec, native_simulated: bool) -> None:
    if spec.simulated != native_simulated:
        raise ValueError("scenario evidence boundary does not match native runner")


def _write_decision_evidence(
    native_workspace: Path, client: ProcessScenarioAgentClient
) -> ArtifactReference:
    path = native_workspace / "process-agent-decisions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "ase/process-agent-decisions/v1alpha1",
        "decisions": [item.model_dump(mode="json") for item in client.evidence],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _artifact("process_agent_decisions", path)


def _write_interactive_evidence(
    native_workspace: Path, evidence: list[InteractiveRunEvidence]
) -> ArtifactReference:
    path = native_workspace / "interactive-agent-traces.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "ase/interactive-agent-traces/v1alpha1",
        "runs": [item.model_dump(mode="json") for item in evidence],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _artifact("interactive_agent_traces", path)


ADAPTERS: Dict[ScenarioKind, ScenarioAdapter] = {
    ScenarioKind.SOFTWARE_ENGINEERING: SoftwareEngineeringScenarioAdapter(),
    ScenarioKind.MCP_TOOL: McpScenarioAdapter(),
    ScenarioKind.MEMORY_RAG: MemoryRagScenarioAdapter(),
}
