"""Fault-injection tests for P0 local persistence and recovery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Tuple
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import (
    AgentSnapshot,
    ArtifactEntry,
    ArtifactManifest,
    AttemptStatus,
    EvaluationOutcome,
    ExecutionStatus,
    ExperimentManifest,
    ExperimentVariant,
    PairBlock,
    Run,
    RunAttempt,
    RunnerSnapshot,
    RunPlanFingerprint,
    SandboxSnapshot,
    SkillSnapshot,
    ToolSnapshot,
    VariantReference,
    VariantRole,
)
from agentskill_eval_experiment import (
    AtomicFileWriter,
    ExperimentLayout,
    ImmutableManifestError,
    IntegrityError,
    LocalExperimentStore,
    LocalSqliteIndex,
    LockUnavailableError,
)
from agentskill_eval_experiment.storage.manifests import model_bytes, parse_envelope

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
cli_runner = CliRunner()


class FailRunFinalizeWriter(AtomicFileWriter):
    """Fault injector that crashes only while publishing a terminal Run pointer."""

    fail_terminal_run = False

    def write(self, target: Path, content: bytes) -> None:
        if self.fail_terminal_run and target.name == "run.json":
            envelope = parse_envelope(content)
            if envelope.payload.get("execution_status") == "COMPLETED":
                raise OSError("simulated crash before Run pointer replacement")
        super().write(target, content)


def make_variant(
    experiment_id: UUID,
    *,
    role: VariantRole,
    variant_id: UUID | None = None,
    model: str = "mock-v1",
) -> ExperimentVariant:
    skill = None
    name = "without-skill"
    if role == VariantRole.TREATMENT:
        name = "with-skill"
        skill = SkillSnapshot(
            skill_id=uuid4(),
            version_id=uuid4(),
            name="python-review",
            version="1.0.0",
            content_sha256=DIGEST_B,
            injection_mode="native_install",
        )
    return ExperimentVariant(
        id=variant_id or uuid4(),
        experiment_id=experiment_id,
        name=name,
        role=role,
        runner_snapshot=RunnerSnapshot(
            name="skill-up",
            version="0.5.0",
            binary_sha256=DIGEST_A,
        ),
        agent_snapshot=AgentSnapshot(engine="mock", model=model),
        skill_snapshot=skill,
        tool_snapshot=ToolSnapshot(),
        sandbox_snapshot=SandboxSnapshot(profile="runner_default", image="python:3.11"),
    )


def make_experiment(
    experiment_id: UUID,
    variants: Tuple[ExperimentVariant, ExperimentVariant],
) -> ExperimentManifest:
    references = tuple(
        VariantReference(
            variant_id=variant.id,
            variant_sha256=variant.variant_sha256,
            manifest_path=f"variants/{variant.id}.json",
        )
        for variant in variants
    )
    return ExperimentManifest(
        id=experiment_id,
        name="local-storage-test",
        code_revision="fac812e",
        dataset_version_id=uuid4(),
        dataset_sha256=DIGEST_A,
        protocol_snapshot={"repeats": 1},
        statistics_plan={"primary": "itt"},
        budget_snapshot={"max_runs": 2},
        variants=references,
    )


def make_run(experiment_id: UUID, block_id: UUID, variant_id: UUID) -> Run:
    return Run(
        id=uuid4(),
        experiment_id=experiment_id,
        pair_block_id=block_id,
        variant_id=variant_id,
        run_plan_fingerprint=RunPlanFingerprint(
            case_sha256=DIGEST_A,
            grader_sha256=DIGEST_B,
            platform_compiled_prompt_sha256="c" * 64,
            upstream_config_sha256="d" * 64,
            image_digest="sha256:" + "e" * 64,
        ),
    )


def update_run(run: Run, **updates: Any) -> Run:
    payload: Dict[str, Any] = run.model_dump(mode="python", round_trip=True)
    payload.update(updates)
    return Run.model_validate(payload)


def prepare_experiment(
    store: LocalExperimentStore,
) -> tuple[ExperimentManifest, ExperimentVariant, ExperimentVariant, PairBlock, Run]:
    experiment_id = uuid4()
    baseline = make_variant(experiment_id, role=VariantRole.BASELINE)
    treatment = make_variant(experiment_id, role=VariantRole.TREATMENT)
    experiment = make_experiment(experiment_id, (baseline, treatment))
    block = PairBlock(
        id=uuid4(),
        experiment_id=experiment_id,
        case_id=uuid4(),
        independence_group="repo/example",
        repeat_index=0,
        seed=42,
        execution_order=(baseline.id, treatment.id),
    )
    run = make_run(experiment_id, block.id, treatment.id)
    store.save_experiment(experiment)
    store.save_variant(baseline)
    store.save_variant(treatment)
    store.save_pair_block(block)
    store.save_run(run)
    return experiment, baseline, treatment, block, run


def advance_to_persisting(store: LocalExperimentStore, run: Run) -> Run:
    for status in (
        ExecutionStatus.QUEUED,
        ExecutionStatus.LEASED,
        ExecutionStatus.PREPARING,
        ExecutionStatus.RUNNING,
        ExecutionStatus.GRADING,
        ExecutionStatus.PERSISTING,
    ):
        updates: Dict[str, Any] = {"execution_status": status}
        if status == ExecutionStatus.LEASED:
            updates["lease_generation"] = 1
        run = update_run(run, **updates)
        store.save_run(run)
    return run


def test_manifest_envelope_detects_payload_tampering() -> None:
    variant = make_variant(uuid4(), role=VariantRole.BASELINE)
    document = json.loads(model_bytes(variant).decode("utf-8"))
    document["payload"]["name"] = "tampered"

    with pytest.raises(IntegrityError, match="digest mismatch"):
        parse_envelope(json.dumps(document).encode("utf-8"))


def test_immutable_manifest_is_idempotent_but_rejects_replacement(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment_id = uuid4()
    variant_id = uuid4()
    first = make_variant(
        experiment_id, role=VariantRole.BASELINE, variant_id=variant_id, model="mock-v1"
    )
    changed = make_variant(
        experiment_id, role=VariantRole.BASELINE, variant_id=variant_id, model="mock-v2"
    )

    store.save_variant(first)
    store.save_variant(first)

    with pytest.raises(ImmutableManifestError):
        store.save_variant(changed)


def test_content_addressed_blob_store_deduplicates_and_verifies(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    content = b"raw runner output"

    first = store.blobs.put_bytes(content)
    second = store.blobs.put_bytes(content)

    assert first == second
    assert store.blobs.read_bytes(first) == content
    blob_path = store.blobs.root / first.relative_path
    blob_path.write_bytes(b"corrupted")
    with pytest.raises(IntegrityError, match="integrity check failed"):
        store.blobs.verify(first)


def test_commit_attempt_persists_evidence_before_run_pointer_and_indexes_it(
    tmp_path: Path,
) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment, _, _, _, run = prepare_experiment(store)
    run = advance_to_persisting(store, run)
    now = datetime.now(timezone.utc)
    attempt = RunAttempt(
        id=uuid4(),
        run_id=run.id,
        attempt_no=1,
        lease_generation=1,
        fencing_token=uuid4(),
        status=AttemptStatus.COMPLETED,
        worker_id="local-worker",
        claimed_at=now,
        finished_at=now,
    )
    artifact_content = b'{"passed": true}'
    artifacts = ArtifactManifest(
        artifacts=(
            ArtifactEntry(
                path="raw-runner/result.json",
                sha256=sha256(artifact_content).hexdigest(),
                size_bytes=len(artifact_content),
                media_type="application/json",
            ),
        )
    )
    completed = update_run(
        run,
        execution_status=ExecutionStatus.COMPLETED,
        evaluation_outcome=EvaluationOutcome.PASS,
        final_score=1.0,
        finished_at=now,
    )

    selected = store.commit_attempt(completed, attempt, artifacts)

    layout = ExperimentLayout(tmp_path, experiment.id)
    assert layout.attempt(run.id, 1).exists()
    assert layout.artifact_manifest(run.id, 1).exists()
    assert selected.active_attempt_id == attempt.id
    assert selected.selected_attempt_sha256 is not None
    assert store.load_run(experiment.id, run.id) == selected
    records = LocalSqliteIndex(layout.index).records()
    assert len(records) == 7
    run_record = next(record for record in records if record.kind == "Run")
    assert run_record.status == "COMPLETED"
    assert run_record.evaluation_outcome == "pass"


def test_attempt_survives_crash_before_run_pointer_and_recovery_reports_run(
    tmp_path: Path,
) -> None:
    writer = FailRunFinalizeWriter()
    store = LocalExperimentStore(tmp_path, writer)
    experiment, _, _, _, run = prepare_experiment(store)
    run = advance_to_persisting(store, run)
    now = datetime.now(timezone.utc)
    attempt = RunAttempt(
        id=uuid4(),
        run_id=run.id,
        attempt_no=1,
        lease_generation=1,
        fencing_token=uuid4(),
        status=AttemptStatus.COMPLETED,
        claimed_at=now,
        finished_at=now,
    )
    completed = update_run(
        run,
        execution_status=ExecutionStatus.COMPLETED,
        evaluation_outcome=EvaluationOutcome.PASS,
        final_score=1.0,
        finished_at=now,
    )
    writer.fail_terminal_run = True

    with pytest.raises(OSError, match="simulated crash"):
        store.commit_attempt(completed, attempt)

    layout = ExperimentLayout(tmp_path, experiment.id)
    assert layout.attempt(run.id, 1).exists()
    assert store.load_run(experiment.id, run.id).execution_status == ExecutionStatus.PERSISTING
    report = store.recover()
    assert report.unfinished_run_ids == (run.id,)


def test_commit_attempt_rejects_stale_lease_generation(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    _, _, _, _, run = prepare_experiment(store)
    run = advance_to_persisting(store, run)
    now = datetime.now(timezone.utc)
    stale_attempt = RunAttempt(
        id=uuid4(),
        run_id=run.id,
        attempt_no=1,
        lease_generation=2,
        fencing_token=uuid4(),
        status=AttemptStatus.COMPLETED,
        claimed_at=now,
        finished_at=now,
    )
    completed = update_run(
        run,
        execution_status=ExecutionStatus.COMPLETED,
        evaluation_outcome=EvaluationOutcome.PASS,
        finished_at=now,
    )

    with pytest.raises(ValueError, match="lease_generation"):
        store.commit_attempt(completed, stale_attempt)


def test_sqlite_index_can_be_deleted_and_rebuilt_from_manifests(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment, _, _, _, _ = prepare_experiment(store)
    layout = ExperimentLayout(tmp_path, experiment.id)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{layout.index}{suffix}").unlink(missing_ok=True)

    records = store.rebuild_index(experiment.id)

    assert len(records) == 5
    assert len(LocalSqliteIndex(layout.index).records()) == 5


def test_recovery_promotes_a_valid_fsynced_temporary_manifest(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment_id = uuid4()
    variant = make_variant(experiment_id, role=VariantRole.BASELINE)
    target = ExperimentLayout(tmp_path, experiment_id).variant(variant.id)
    staged = AtomicFileWriter().stage(target, model_bytes(variant))

    report = store.recover()

    assert not staged.temporary.exists()
    assert str(target.relative_to(tmp_path)) in report.promoted_temporary_files
    assert store.load_variant(experiment_id, variant.id) == variant


def test_recovery_removes_duplicate_temp_and_quarantines_conflict(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment_id = uuid4()
    variant = make_variant(experiment_id, role=VariantRole.BASELINE)
    layout = ExperimentLayout(tmp_path, experiment_id)
    store.save_variant(variant)
    duplicate = AtomicFileWriter().stage(layout.variant(variant.id), model_bytes(variant))
    conflicting = make_variant(
        experiment_id,
        role=VariantRole.BASELINE,
        variant_id=variant.id,
        model="different-model",
    )
    conflict = AtomicFileWriter().stage(layout.variant(variant.id), model_bytes(conflicting))

    report = store.recover()

    assert not duplicate.temporary.exists()
    assert not conflict.temporary.exists()
    assert len(report.removed_duplicate_temporary_files) == 1
    assert len(report.quarantined_files) == 1
    assert store.load_variant(experiment_id, variant.id) == variant


def test_recovery_quarantines_corrupt_manifest_and_reports_unfinished_runs(
    tmp_path: Path,
) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment, _, treatment, _, run = prepare_experiment(store)
    layout = ExperimentLayout(tmp_path, experiment.id)
    layout.variant(treatment.id).write_text("not-json", encoding="utf-8")

    report = store.recover()

    assert run.id in report.unfinished_run_ids
    assert len(report.quarantined_files) == 1
    assert not layout.variant(treatment.id).exists()
    assert (tmp_path / report.quarantined_files[0]).exists()


def test_local_run_lock_rejects_a_second_owner(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment_id, run_id = uuid4(), uuid4()

    with store.run_lock(experiment_id, run_id):  # noqa: SIM117 - preserve Python 3.9 syntax
        with pytest.raises(LockUnavailableError), store.run_lock(experiment_id, run_id):
            pytest.fail("second owner unexpectedly acquired the run lock")

    with store.run_lock(experiment_id, run_id):
        pass


def test_store_rejects_run_state_regression(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    _, _, _, _, run = prepare_experiment(store)
    queued = update_run(run, execution_status=ExecutionStatus.QUEUED)
    store.save_run(queued)

    with pytest.raises(ValueError, match="QUEUED -> CREATED"):
        store.save_run(run)


def test_storage_cli_recovers_and_rebuilds_index(tmp_path: Path) -> None:
    store = LocalExperimentStore(tmp_path)
    experiment, _, _, _, _ = prepare_experiment(store)
    layout = ExperimentLayout(tmp_path, experiment.id)
    layout.index.unlink()

    recover_result = cli_runner.invoke(app, ["storage", "recover", str(tmp_path)])
    rebuild_result = cli_runner.invoke(
        app,
        ["storage", "rebuild-index", str(tmp_path), str(experiment.id)],
    )

    assert recover_result.exit_code == 0
    assert json.loads(recover_result.stdout)["unfinished_run_ids"]
    assert rebuild_result.exit_code == 0
    assert json.loads(rebuild_result.stdout) == {"indexed_manifests": 5}
