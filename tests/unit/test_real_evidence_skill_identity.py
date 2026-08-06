import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agentskill_eval_contracts import RealEvidenceClass, RealEvidenceStatus
from agentskill_eval_experiment import ReplayBundleWriter
from agentskill_eval_real_evidence.execution import (
    RealAgentEvidenceRunner,
    RealEvidenceError,
    RealEvidenceStore,
)
from agentskill_eval_real_evidence.reporting import RealEvidenceReportWriter


def test_real_evidence_uses_frozen_skill_metadata_identity(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "metadata.yaml").write_text(
        "name: python-test-generation-v1\nversion: 1.0.0\n",
        encoding="utf-8",
    )

    assert RealAgentEvidenceRunner._skill_identity(skill) == (
        "python-test-generation-v1",
        "1.0.0",
    )


def test_real_evidence_rejects_missing_skill_identity(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "metadata.yaml").write_text("license: Apache-2.0\n", encoding="utf-8")

    with pytest.raises(RealEvidenceError, match="requires a name"):
        RealAgentEvidenceRunner._skill_identity(skill)


def test_real_evidence_builds_hash_bound_process_agent_skill_context(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    content = "# Test Generation\nWrite a regression test.\n"
    (skill / "SKILL.md").write_text(content, encoding="utf-8")

    home_files = RealAgentEvidenceRunner._process_agent_skill_home_files(skill)

    assert set(home_files) == {".agentskill-eval/selected-skill.json"}
    context = home_files[".agentskill-eval/selected-skill.json"]
    assert context == {
        "schema_version": "ase/process-agent-skill/v1alpha1",
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def test_real_evidence_uses_explicit_process_context_injection_mode() -> None:
    assert (
        RealAgentEvidenceRunner._skill_injection_mode("qwen_openai_process")
        == "skill-up-native-plus-process-context"
    )
    assert (
        RealAgentEvidenceRunner._skill_injection_mode("qwen_code")
        == "skill-up-native-install"
    )


def test_real_evidence_validates_process_agent_skill_context() -> None:
    digest = "a" * 64

    RealAgentEvidenceRunner._validate_process_agent_skill_context(
        {
            "skill_context_loaded": True,
            "skill_context_sha256": digest,
        },
        expected_sha256=digest,
    )
    RealAgentEvidenceRunner._validate_process_agent_skill_context(
        {
            "skill_context_loaded": False,
            "skill_context_sha256": None,
        },
        expected_sha256=None,
    )

    with pytest.raises(RealEvidenceError, match="Skill context handoff mismatch"):
        RealAgentEvidenceRunner._validate_process_agent_skill_context(
            {
                "skill_context_loaded": False,
                "skill_context_sha256": None,
            },
            expected_sha256=digest,
        )


def test_real_evidence_report_requires_integrity_sidecar(tmp_path: Path) -> None:
    store = RealEvidenceStore(tmp_path)
    experiment_id = uuid4()
    path = store.report_json(experiment_id)
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(RealEvidenceError, match="report integrity mismatch"):
        store.load_report(experiment_id)


def test_completed_evidence_verifier_binds_report_and_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RealEvidenceStore(tmp_path)
    experiment_id = uuid4()
    bundle = tmp_path / "real-evidence-bundles" / f"{experiment_id}.tar"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"frozen bundle")
    run = SimpleNamespace(status=RealEvidenceStatus.COMPLETED)
    report = SimpleNamespace(
        run=run,
        replay_bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(store, "load_run", lambda _experiment_id: run)
    monkeypatch.setattr(store, "load_report", lambda _experiment_id: report)
    monkeypatch.setattr(
        ReplayBundleWriter,
        "verify",
        lambda _bundle: SimpleNamespace(experiment_id=experiment_id),
    )

    assert store.verify_completed(experiment_id) == (run, report, bundle)

    report.replay_bundle_sha256 = "0" * 64
    with pytest.raises(RealEvidenceError, match="bundle hash"):
        store.verify_completed(experiment_id)


def test_real_evidence_report_rejects_mixed_evidence_classes() -> None:
    run = SimpleNamespace(
        simulated=False,
        evidence_class=RealEvidenceClass.OBSERVED_AGENT,
        provider="local",
        model="model",
    )
    variant = SimpleNamespace(
        runner_snapshot=SimpleNamespace(config={"simulated": False})
    )
    simulated_attempt = SimpleNamespace(
        simulated=True,
        evidence_class=RealEvidenceClass.PROCESS_INTEGRATION,
        provider="local",
        model="model",
    )
    with pytest.raises(ValueError, match="real and simulated"):
        RealEvidenceReportWriter._assert_uniform_evidence(
            run, variant, variant, (simulated_attempt,)
        )

    wrong_class = SimpleNamespace(
        simulated=False,
        evidence_class=RealEvidenceClass.PROCESS_INTEGRATION,
        provider="local",
        model="model",
    )
    with pytest.raises(ValueError, match="mixed evidence classes"):
        RealEvidenceReportWriter._assert_uniform_evidence(
            run, variant, variant, (wrong_class,)
        )
