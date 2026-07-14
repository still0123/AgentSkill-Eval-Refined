from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Tuple
from uuid import UUID, uuid4

import yaml
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import (
    AgentSnapshot,
    ExperimentManifest,
    ExperimentStatus,
    ExperimentVariant,
    RealEvidenceClass,
    RealEvidenceRunManifest,
    RealEvidenceStatus,
    RealRunMode,
    RunnerSnapshot,
    SandboxSnapshot,
    SkillSnapshot,
    ToolSnapshot,
    VariantReference,
    VariantRole,
)
from agentskill_eval_experiment import (
    CaseExecutionSpec,
    LocalExperimentExecutor,
    LocalExperimentPlanner,
    LocalExperimentStore,
    VariantRuntimeSpec,
)
from agentskill_eval_real_evidence import RealEvidenceStore
from agentskill_eval_runner_adapters import (
    CapabilityLevel,
    ExitReason,
    RunnerCompatibility,
    RunnerEvent,
    RunnerRequest,
    RunnerResult,
    RunnerSkillEvidence,
    RunnerStatus,
    ValidationReport,
)
from agentskill_eval_runner_adapters.contracts import TraceEventSink, null_event_sink
from agentskill_eval_skill_optimizer import (
    FailureEvidenceBundle,
    ObservedFailureEvidenceBridge,
)

ROOT = Path(__file__).resolve().parents[2]
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
CASE_KINDS: Mapping[str, str | None] = {
    "planning-fail": "agent.planning",
    "tool-selection-fail": "tool.selection",
    "verification-fail": "test.verification",
    "unknown-fail": None,
    "infra-invalid": None,
}


class ObservedFixtureAdapter:
    @property
    def compatibility(self) -> RunnerCompatibility:
        return RunnerCompatibility(
            name="observed-fixture",
            version="1",
            binary_sha256="0" * 64,
            capabilities={"trace": CapabilityLevel.NATIVE},
        )

    async def validate(self, request: RunnerRequest) -> ValidationReport:
        has_skill = request.skill_path is not None
        return ValidationReport(
            valid=True,
            skill_evidence=RunnerSkillEvidence(
                skill_expected=has_skill,
                installed=True if has_skill else None,
                baseline_clean=None if has_skill else True,
                installation_method="fixture",
            ),
        )

    async def execute(
        self,
        request: RunnerRequest,
        event_sink: TraceEventSink = null_event_sink,
    ) -> RunnerResult:
        kind = CASE_KINDS[request.case_id]
        if kind is not None:
            await event_sink(
                RunnerEvent(
                    execution_id=request.execution_id,
                    kind=kind,
                    payload={"status": "failed"},
                )
            )
        if request.case_id == "infra-invalid":
            return RunnerResult(
                execution_id=request.execution_id,
                case_id=request.case_id,
                status=RunnerStatus.ERROR,
                exit_reason=ExitReason.EXECUTION_ERROR,
                process_exit_code=1,
            )
        return RunnerResult(
            execution_id=request.execution_id,
            case_id=request.case_id,
            status=RunnerStatus.FAIL,
            exit_reason=ExitReason.CASE_FAILED,
            process_exit_code=1,
        )

    async def cancel(self, execution_id: str) -> bool:
        return True


def _variants(experiment_id: UUID) -> Tuple[ExperimentVariant, ExperimentVariant]:
    common = {
        "experiment_id": experiment_id,
        "runner_snapshot": RunnerSnapshot(
            name="observed-fixture", version="1", binary_sha256="0" * 64
        ),
        "agent_snapshot": AgentSnapshot(engine="fixture", model="fixture-model"),
        "tool_snapshot": ToolSnapshot(),
        "sandbox_snapshot": SandboxSnapshot(profile="fixture"),
    }
    baseline = ExperimentVariant(
        id=uuid4(), name="without-skill", role=VariantRole.BASELINE, **common
    )
    treatment = ExperimentVariant(
        id=uuid4(),
        name="skill-v1",
        role=VariantRole.TREATMENT,
        skill_snapshot=SkillSnapshot(
            skill_id=uuid4(),
            version_id=uuid4(),
            name="bug-fix",
            version="1",
            content_sha256=DIGEST_B,
            injection_mode="native_install",
        ),
        **common,
    )
    return baseline, treatment


def _case(case_id: str) -> CaseExecutionSpec:
    case_file = ROOT / "tests/fixtures/runner_eval/cases/golden-pass.yaml"
    return CaseExecutionSpec(
        id=uuid4(),
        runner_case_id=case_id,
        independence_group=f"fixture/{case_id}",
        source_eval_dir=case_file.parents[1],
        case_file=case_file,
        case_sha256=hashlib.sha256(case_file.read_bytes()).hexdigest(),
        grader_sha256=hashlib.sha256(case_id.encode()).hexdigest(),
        platform_compiled_prompt_sha256=hashlib.sha256(f"prompt:{case_id}".encode()).hexdigest(),
    )


def _observed_workspace(tmp_path: Path) -> Tuple[Path, UUID]:
    workspace = tmp_path / "workspace"
    store = LocalExperimentStore(workspace)
    experiment_id = uuid4()
    variants = _variants(experiment_id)
    experiment = ExperimentManifest(
        id=experiment_id,
        name="observed Skill v1 train fixture",
        code_revision="fixture",
        dataset_version_id=uuid4(),
        dataset_sha256=DIGEST_A,
        protocol_snapshot={"split": "train", "repeats": 1},
        statistics_plan={"primary": "task_success"},
        budget_snapshot={"max_runs": 10},
        variants=tuple(
            VariantReference(
                variant_id=item.id,
                variant_sha256=item.variant_sha256,
                manifest_path=f"variants/{item.id}.json",
            )
            for item in variants
        ),
        status=ExperimentStatus.FROZEN,
    )
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Bug Fix v1\n", encoding="utf-8")
    runtimes = (
        VariantRuntimeSpec(variant_id=variants[0].id, engine={}, environment={}),
        VariantRuntimeSpec(
            variant_id=variants[1].id, engine={}, environment={}, skill_path=skill
        ),
    )
    plan = LocalExperimentPlanner(store).build(
        experiment,
        variants,
        runtimes,
        [_case(case_id) for case_id in CASE_KINDS],
        repeats=1,
        random_seed=7,
    )
    LocalExperimentPlanner(store).persist(plan)
    summary = asyncio.run(LocalExperimentExecutor(store, ObservedFixtureAdapter()).execute(plan))
    now = datetime.now(timezone.utc)
    RealEvidenceStore(workspace).save_run(
        RealEvidenceRunManifest(
            experiment_id=experiment_id,
            mode=RealRunMode.EVIDENCE,
            status=RealEvidenceStatus.COMPLETED,
            config_sha256="c" * 64,
            preflight_sha256="d" * 64,
            simulated=False,
            evidence_class=RealEvidenceClass.OBSERVED_AGENT,
            provider="fixture",
            model="fixture-model",
            real_run_confirmed=True,
            max_cost_microusd=1,
            max_agent_runs=10,
            planned_runs=10,
            completed_runs=summary.completed_runs,
            invalid_runs=summary.invalid_runs,
            observed_or_reserved_cost_microusd=0,
            started_at=now,
            completed_at=now,
            claim_limit="local observed fixture for bridge integration only",
        )
    )
    return workspace, experiment_id


def test_observed_failures_become_trace_linked_train_bundle(tmp_path: Path) -> None:
    workspace, experiment_id = _observed_workspace(tmp_path)
    output = tmp_path / "train-failures.yaml"
    result = ObservedFailureEvidenceBridge(workspace).prepare(experiment_id, output)

    assert result.report.status == "READY"
    assert result.report.treatment_run_count == 5
    assert result.report.task_failed_run_count == 4
    assert result.report.invalid_run_count == 1
    assert len(result.report.eligible) == 3
    assert len(result.report.excluded) == 2
    assert {item.label.value for item in result.report.eligible} == {
        "PLANNING",
        "TOOL_SELECTION",
        "VERIFICATION",
    }
    assert all(item.trace_event_refs for item in result.report.eligible)
    assert len(result.report.clusters) == 3
    assert FailureEvidenceBundle.load(output) == result.bundle


def test_review_can_exclude_and_override_only_task_failures(tmp_path: Path) -> None:
    workspace, experiment_id = _observed_workspace(tmp_path)
    initial = ObservedFailureEvidenceBridge(workspace).prepare(
        experiment_id, tmp_path / "initial.yaml"
    )
    planning = next(item for item in initial.report.eligible if item.label.value == "PLANNING")
    unknown = next(item for item in initial.report.excluded if item.label.value == "UNKNOWN")
    invalid = next(item for item in initial.report.excluded if item.label.value == "ENVIRONMENT")
    review = tmp_path / "review.yaml"
    review.write_text(
        yaml.safe_dump(
            {
                "schema_version": "ase/failure-evidence-review/v1alpha1",
                "decisions": [
                    {
                        "run_id": str(planning.run_id),
                        "rule_id": planning.rule_id,
                        "action": "exclude",
                        "reason": "planning signal was incidental",
                    },
                    {
                        "run_id": str(unknown.run_id),
                        "rule_id": unknown.rule_id,
                        "action": "include",
                        "override_label": "MEMORY",
                        "reason": "human review confirmed stale memory use",
                    },
                    {
                        "run_id": str(invalid.run_id),
                        "rule_id": invalid.rule_id,
                        "action": "include",
                        "override_label": "MEMORY",
                        "reason": "invalid runs must remain excluded despite this request",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    reviewed = ObservedFailureEvidenceBridge(workspace).prepare(
        experiment_id, tmp_path / "reviewed.yaml", review_path=review
    )
    assert {item.label.value for item in reviewed.report.eligible} == {
        "MEMORY",
        "TOOL_SELECTION",
        "VERIFICATION",
    }
    assert all(
        item.review_applied
        for item in reviewed.report.eligible
        if item.label.value == "MEMORY"
    )
    infra = next(item for item in reviewed.report.excluded if item.label.value == "ENVIRONMENT")
    assert infra.eligible is False


def test_prepare_failures_cli_reports_paths_and_counts(tmp_path: Path) -> None:
    workspace, experiment_id = _observed_workspace(tmp_path)
    output = tmp_path / "cli-train-failures.yaml"
    result = CliRunner().invoke(
        app,
        [
            "optimize",
            "prepare-failures",
            str(workspace),
            str(experiment_id),
            "--output",
            str(output),
        ],
        terminal_width=240,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY"
    assert payload["eligible_findings"] == 3
    assert Path(payload["bundle"]).is_file()
    assert Path(payload["audit_report"]).is_file()
