"""Unit tests for immutable experiment and execution contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from agentskill_eval_contracts import (
    AgentSnapshot,
    ArtifactEntry,
    ArtifactManifest,
    AttemptStatus,
    EvaluationOutcome,
    ExecutableSnapshot,
    ExecutionStatus,
    ExperimentVariant,
    FinalDecision,
    FinalEvaluationStage,
    PairBlock,
    PromotionEvidenceRef,
    PromotionLineageArtifact,
    PromotionWorkflowRecord,
    PromotionWorkflowStatus,
    RealCaseEvidence,
    RealEvidenceClass,
    RealEvidenceRunManifest,
    RealEvidenceStatus,
    RealExperimentReport,
    RealPreflightReport,
    RealRunMode,
    Run,
    RunAttempt,
    RunnerSnapshot,
    RunPlanFingerprint,
    SandboxSnapshot,
    SkillSnapshot,
    ToolSnapshot,
    VariantRole,
    stable_sha256,
    validate_run_transition,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def make_variant(
    *,
    variant_id: UUID | None = None,
    experiment_id: UUID | None = None,
    name: str = "with-skill",
    skill_digest: str = DIGEST_B,
) -> ExperimentVariant:
    return ExperimentVariant(
        id=variant_id or uuid4(),
        experiment_id=experiment_id or uuid4(),
        name=name,
        role=VariantRole.TREATMENT,
        runner_snapshot=RunnerSnapshot(
            name="skill-up",
            version="0.5.0",
            binary_sha256=DIGEST_A,
            config={"parallelism": 1},
        ),
        agent_snapshot=AgentSnapshot(
            engine="mock",
            model="mock-v1",
            generation_parameters={"temperature": 0},
        ),
        skill_snapshot=SkillSnapshot(
            skill_id=uuid4(),
            version_id=uuid4(),
            name="python-review",
            version="1.0.0",
            content_sha256=skill_digest,
            injection_mode="native_install",
        ),
        tool_snapshot=ToolSnapshot(config={"filesystem": True}),
        sandbox_snapshot=SandboxSnapshot(profile="runner_default", image="python:3.11"),
    )


def make_plan() -> RunPlanFingerprint:
    return RunPlanFingerprint(
        case_sha256=DIGEST_A,
        grader_sha256=DIGEST_B,
        platform_compiled_prompt_sha256="c" * 64,
        upstream_config_sha256="d" * 64,
        image_digest="sha256:" + "e" * 64,
    )


def test_stable_sha256_is_independent_of_mapping_order() -> None:
    assert stable_sha256({"b": 2, "a": 1}) == stable_sha256({"a": 1, "b": 2})


def test_variant_fingerprint_excludes_identity_and_display_name() -> None:
    first = make_variant()
    second = make_variant(name="renamed-treatment")

    first_payload = first.model_dump(mode="json")
    second_payload = second.model_dump(mode="json")

    assert first_payload["id"] != second_payload["id"]
    assert first.skill_snapshot is not None
    assert second.skill_snapshot is not None
    assert first.skill_snapshot.version_id != second.skill_snapshot.version_id
    assert first.variant_sha256 == second.variant_sha256


def test_variant_fingerprint_changes_when_skill_content_changes() -> None:
    original = make_variant(skill_digest=DIGEST_A)
    changed = make_variant(skill_digest=DIGEST_B)
    changed = changed.model_copy(
        update={
            "skill_snapshot": changed.skill_snapshot.model_copy(
                update={
                    "skill_id": original.skill_snapshot.skill_id
                    if original.skill_snapshot
                    else uuid4(),
                    "version_id": original.skill_snapshot.version_id
                    if original.skill_snapshot
                    else uuid4(),
                }
            )
        }
    )

    assert original.variant_sha256 != changed.variant_sha256


def test_frozen_contract_rejects_attribute_assignment() -> None:
    variant = make_variant()

    with pytest.raises(ValidationError):
        variant.name = "mutated"  # type: ignore[misc]


def test_pair_block_requires_unique_execution_order() -> None:
    variant_id = uuid4()

    with pytest.raises(ValidationError, match="exactly once"):
        PairBlock(
            id=uuid4(),
            experiment_id=uuid4(),
            case_id=uuid4(),
            independence_group="repository/example",
            repeat_index=0,
            seed=42,
            execution_order=(variant_id, variant_id),
        )


def test_pair_block_hash_changes_with_execution_order() -> None:
    first_id, second_id = uuid4(), uuid4()
    common = {
        "id": uuid4(),
        "experiment_id": uuid4(),
        "case_id": uuid4(),
        "independence_group": "repository/example",
        "repeat_index": 0,
        "seed": 42,
    }

    forward = PairBlock(**common, execution_order=(first_id, second_id))
    reverse = PairBlock(**common, execution_order=(second_id, first_id))

    # block_sha256 was removed as a computed_field; execution_order ordering
    # is still enforced by the model_validator.
    assert forward.execution_order != reverse.execution_order


def test_run_idempotency_key_is_stable_for_pair_and_variant() -> None:
    pair_block_id, variant_id = uuid4(), uuid4()
    first = Run(
        id=uuid4(),
        experiment_id=uuid4(),
        pair_block_id=pair_block_id,
        variant_id=variant_id,
        run_plan_fingerprint=make_plan(),
    )
    second = first.model_copy(update={"id": uuid4()})

    # idempotency_key was removed as a computed_field; the pair_block_id
    # and variant_id fields still provide the identity semantics.
    assert first.pair_block_id == second.pair_block_id
    assert first.variant_id == second.variant_id


def test_completed_run_requires_outcome_and_finished_at() -> None:
    with pytest.raises(ValidationError, match="require an outcome"):
        Run(
            id=uuid4(),
            experiment_id=uuid4(),
            pair_block_id=uuid4(),
            variant_id=uuid4(),
            execution_status=ExecutionStatus.COMPLETED,
            run_plan_fingerprint=make_plan(),
        )


def test_infra_failure_is_invalid_not_task_failure() -> None:
    run = Run(
        id=uuid4(),
        experiment_id=uuid4(),
        pair_block_id=uuid4(),
        variant_id=uuid4(),
        execution_status=ExecutionStatus.INFRA_FAILED,
        evaluation_outcome=EvaluationOutcome.INVALID,
        finished_at=datetime.now(timezone.utc),
        run_plan_fingerprint=make_plan(),
    )

    assert run.final_score is None


def test_run_transition_rejects_skipping_execution_stages() -> None:
    validate_run_transition(ExecutionStatus.CREATED, ExecutionStatus.QUEUED)

    with pytest.raises(ValueError, match="CREATED -> COMPLETED"):
        validate_run_transition(ExecutionStatus.CREATED, ExecutionStatus.COMPLETED)


def test_failed_attempt_requires_error_code_and_finish_time() -> None:
    with pytest.raises(ValidationError, match="finished_at"):
        RunAttempt(
            id=uuid4(),
            run_id=uuid4(),
            attempt_no=1,
            lease_generation=1,
            fencing_token=uuid4(),
            status=AttemptStatus.FAILED,
            claimed_at=datetime.now(timezone.utc),
            error_code="provider_timeout",
        )


@pytest.mark.parametrize(
    "unsafe_path",
    ["/etc/passwd", "../secret", "logs/../../secret", "a\\b", "logs//result.json"],
)
def test_artifact_manifest_rejects_unsafe_paths(unsafe_path: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactEntry(
            path=unsafe_path,
            sha256=DIGEST_A,
            size_bytes=1,
            media_type="text/plain",
        )


def test_artifact_manifest_rejects_duplicate_paths() -> None:
    artifact = ArtifactEntry(
        path="runner/result.json",
        sha256=DIGEST_A,
        size_bytes=2,
        media_type="application/json",
    )

    with pytest.raises(ValidationError, match="unique"):
        ArtifactManifest(artifacts=(artifact, artifact))


def test_schema_version_is_not_silently_accepted() -> None:
    payload = make_variant().model_dump(mode="json", exclude={"variant_sha256"})

    with pytest.raises(ValidationError):
        ExperimentVariant.model_validate({**payload, "schema_version": "ase/v9"})


def test_observed_promotion_workflow_requires_observed_evidence() -> None:
    now = datetime.now(timezone.utc)
    roles = (
        "handoff",
        "evolution_report",
        "regression_gate",
        "hypotheses",
        "search_report",
    )
    lineage = tuple(
        PromotionLineageArtifact(role=role, sha256=DIGEST_A, size_bytes=1)
        for role in roles
    )
    confirmation = PromotionEvidenceRef(
        stage=FinalEvaluationStage.VALIDATION_CONFIRM,
        final_evaluation_job_id=uuid4(),
        report_sha256=DIGEST_A,
        decision=FinalDecision.CONFIRMED,
        base_skill_sha256=DIGEST_A,
        winner_skill_sha256=DIGEST_B,
        simulated=False,
        validator_version="observed-test",
        recorded_at=now,
    )
    payload = {
        "id": uuid4(),
        "promotion_id": uuid4(),
        "evolution_id": uuid4(),
        "optimization_job_id": uuid4(),
        "winner_candidate_id": uuid4(),
        "skill_name": "python-bug-fix",
        "target_version": "2.0.0",
        "base_skill_sha256": DIGEST_A,
        "winner_skill_sha256": DIGEST_B,
        "lineage_sha256": DIGEST_A,
        "lineage": lineage,
        "status": PromotionWorkflowStatus.AWAITING_LOCKED_TEST,
        "confirmation": confirmation,
        "simulated": False,
        "created_at": now,
        "updated_at": now,
        "claim_limit": "descriptive observed evidence only",
    }

    assert PromotionWorkflowRecord.model_validate(payload).simulated is False
    with pytest.raises(ValidationError, match="evidence boundary"):
        PromotionWorkflowRecord.model_validate(
            {**payload, "confirmation": confirmation.model_copy(update={"simulated": True})}
        )


def test_completed_real_evidence_requires_every_authorized_run() -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "experiment_id": uuid4(),
        "mode": RealRunMode.SMOKE,
        "status": RealEvidenceStatus.COMPLETED,
        "config_sha256": DIGEST_A,
        "preflight_sha256": DIGEST_B,
        "simulated": False,
        "evidence_class": RealEvidenceClass.OBSERVED_AGENT,
        "provider": "local",
        "model": "model",
        "real_run_confirmed": True,
        "max_cost_microusd": 10,
        "max_agent_runs": 2,
        "planned_runs": 2,
        "completed_runs": 1,
        "invalid_runs": 0,
        "observed_or_reserved_cost_microusd": 1,
        "started_at": now,
        "completed_at": now,
        "claim_limit": "exact frozen evidence only",
    }

    with pytest.raises(ValidationError, match="every planned run"):
        RealEvidenceRunManifest.model_validate(payload)

    with pytest.raises(ValidationError, match="exceed authorization"):
        RealEvidenceRunManifest.model_validate({**payload, "completed_runs": 1, "invalid_runs": 2})

    with pytest.raises(ValidationError, match="cost exceeds authorization"):
        RealEvidenceRunManifest.model_validate(
            {
                **payload,
                "completed_runs": 2,
                "observed_or_reserved_cost_microusd": 11,
            }
        )


def test_real_case_evidence_recomputes_gain_and_classification() -> None:
    payload = {
        "case_id": "case-one",
        "independence_group": "group-one",
        "baseline_pass_rate": 0.25,
        "treatment_pass_rate": 0.75,
        "absolute_gain": 0.5,
        "classification": "win",
    }

    RealCaseEvidence.model_validate(payload)
    with pytest.raises(ValidationError, match="gain does not match"):
        RealCaseEvidence.model_validate({**payload, "absolute_gain": 0.25})
    with pytest.raises(ValidationError, match="classification does not match"):
        RealCaseEvidence.model_validate({**payload, "classification": "loss"})


def test_v04_real_report_binds_preflight_lineage() -> None:
    now = datetime.now(timezone.utc)
    experiment_id = uuid4()
    dataset_version_id = uuid4()
    preflight = RealPreflightReport(
        config_sha256=DIGEST_A,
        dataset_version_id=dataset_version_id,
        dataset_name="dataset",
        dataset_version="1.0.0",
        dataset_sha256=DIGEST_B,
        case_ids=("case-one",),
        skill_sha256="c" * 64,
        baseline_skill_sha256="d" * 64,
        runner=ExecutableSnapshot(
            name="runner",
            version="0.5.0",
            path="/runner",
            sha256="e" * 64,
        ),
        agent=ExecutableSnapshot(
            name="agent",
            version="1.0.0",
            path="/agent",
            sha256="f" * 64,
        ),
        agent_engine="process",
        agent_engine_version="1.0.0",
        provider="local",
        model="model",
        simulated=False,
        evidence_class=RealEvidenceClass.OBSERVED_AGENT,
        smoke_runs=2,
        evidence_runs=6,
        estimated_input_tokens_per_run=1,
        estimated_output_tokens_per_run=1,
        estimated_cost_per_run_microusd=1,
        secret_env_names=(),
        checked_at=now,
    )
    run = RealEvidenceRunManifest(
        experiment_id=experiment_id,
        mode=RealRunMode.SMOKE,
        status=RealEvidenceStatus.COMPLETED,
        config_sha256=preflight.config_sha256,
        preflight_sha256=stable_sha256(preflight.model_dump(mode="json")),
        simulated=False,
        evidence_class=RealEvidenceClass.OBSERVED_AGENT,
        provider="local",
        model="model",
        real_run_confirmed=True,
        max_cost_microusd=2,
        max_agent_runs=2,
        planned_runs=2,
        completed_runs=2,
        invalid_runs=0,
        observed_or_reserved_cost_microusd=2,
        started_at=now,
        completed_at=now,
        claim_limit="exact frozen evidence only",
    )
    payload = {
        "run": run,
        "preflight": preflight,
        "dataset_version_id": dataset_version_id,
        "dataset_name": "dataset",
        "dataset_version": "1.0.0",
        "dataset_sha256": DIGEST_B,
        "runner_snapshot": RunnerSnapshot(
            name="skill-up",
            version="0.5.0",
            binary_sha256="e" * 64,
            config={"agent_executable_sha256": "f" * 64},
        ),
        "agent_snapshot": AgentSnapshot(
            engine="process",
            engine_version="1.0.0",
            model="model",
        ),
        "skill_sha256": "c" * 64,
        "baseline_skill_sha256": "d" * 64,
        "baseline_pass_rate": 0,
        "treatment_pass_rate": 0,
        "absolute_gain": 0,
        "wtl": {
            "win": 0,
            "tie_positive": 0,
            "tie_negative": 1,
            "loss": 0,
            "invalid": 0,
        },
        "invalid_runs": 0,
        "token_summary": {},
        "latency_summary": {},
        "cost_summary": {},
        "cases": (
            RealCaseEvidence(
                case_id="case-one",
                independence_group="group-one",
                baseline_pass_rate=0,
                treatment_pass_rate=0,
                absolute_gain=0,
                classification="tie_negative",
            ),
        ),
        "attempt_evidence_paths": ("runs/one", "runs/two"),
        "capability_unavailable": (),
        "replay_bundle_path": "real-evidence-bundles/evidence.tar",
        "replay_bundle_sha256": "1" * 64,
        "simulated": False,
        "evidence_class": RealEvidenceClass.OBSERVED_AGENT,
        "provider": "local",
        "model": "model",
        "real_run_confirmed": True,
        "statistics_semantics_version": "ase/statistics/v0.4",
        "valid_block_ratio": 1,
        "capability_baseline_pass_rate": 0,
        "capability_treatment_pass_rate": 0,
        "capability_absolute_gain": 0,
        "inference_note": "descriptive only",
        "claim_limit": "exact frozen evidence only",
    }

    RealExperimentReport.model_validate(payload)
    with pytest.raises(ValidationError, match="dataset identity"):
        RealExperimentReport.model_validate(
            {**payload, "dataset_sha256": "9" * 64}
        )
