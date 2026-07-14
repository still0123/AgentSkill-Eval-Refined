"""Stage 4B Fake promotion to Stage 5A.2 release CLI integration tests."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tarfile
from pathlib import Path
from typing import Tuple

import yaml
from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import (
    FinalEvaluationReport,
    PromotionReleaseManifest,
    SkillVersionManifest,
)
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes
from agentskill_eval_skill_optimizer import (
    EvolutionReport,
    FailureGuidedEvolutionResult,
    FailureGuidedEvolutionSpec,
    FailureGuidedSkillEvolution,
    IndependentFinalEvaluationSpec,
    OptimizationStore,
    PromotionWorkflow,
)

ROOT = Path(__file__).resolve().parents[2]
EVOLUTION = ROOT / "examples/optimizer/failure-guided/evolution.example.yaml"
runner = CliRunner()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _refresh_release_hashes(release: Path, changed_file: str) -> None:
    target = release / changed_file
    content = target.read_bytes()
    manifest_path = release / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["path"] == changed_file)
    entry["sha256"] = _sha256(content)
    entry["size_bytes"] = len(content)
    _write_json(manifest_path, manifest)
    (release / "release-manifest.sha256").write_text(
        _sha256(manifest_path.read_bytes()) + "\n", encoding="utf-8"
    )


def _skill_texts(workspace: Path, result: FailureGuidedEvolutionResult) -> Tuple[str, str, str]:
    store = OptimizationStore(workspace)
    base = next(item for item in result.search.candidates if item.origin.value == "original")
    return (
        store.skill_path(base).read_text(encoding="utf-8"),
        store.skill_path(result.search.winner).read_text(encoding="utf-8"),
        base.content_sha256,
    )


def _terms(base: str, winner: str) -> Tuple[str, str]:
    base_words = set(re.findall(r"[a-z]{7,}", base.lower()))
    winner_words = sorted(set(re.findall(r"[a-z]{7,}", winner.lower())) - base_words)
    assert len(winner_words) >= 2
    return winner_words[0], winner_words[1]


def _final_spec(
    root: Path,
    optimization_job_id: str,
    *,
    split: str,
    terms: Tuple[str, str],
) -> Path:
    directory = root / split
    directory.mkdir(parents=True)
    dataset = directory / "final-validation.yaml"
    dataset.write_text(
        yaml.safe_dump(
            {
                "schema_version": "ase/final-evaluation-dataset/v1alpha1",
                "name": f"release-{split}",
                "version": "1",
                "split": split,
                "simulated": True,
                "cases": [
                    {
                        "id": f"{split.replace('_', '-')}-a",
                        "independence_group": f"release/{split}/a",
                        "required_terms": [terms[0]],
                    },
                    {
                        "id": f"{split.replace('_', '-')}-b",
                        "independence_group": f"release/{split}/b",
                        "required_terms": [terms[1]],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    spec = directory / "final.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "schema_version": "ase/independent-final-evaluation/v1alpha1",
                "name": f"release-{split}",
                "optimization_job_id": optimization_job_id,
                "dataset_path": str(dataset),
                "stage": split,
                "repeats": 1,
                "timeout_seconds": 30,
                "gates": {
                    "min_absolute_gain": 0.1,
                    "max_loss_cases": 0,
                    "max_token_overhead_ratio": 2.0,
                    "min_independent_groups": 2,
                    "bootstrap_resamples": 100,
                    "bootstrap_seed": 2026,
                },
                "evaluator": {
                    "type": "simulated_keyword",
                    "version": f"release-{split}-v1",
                    "simulated": True,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return spec


def _fixture(tmp_path: Path) -> Tuple[Path, Path, Path]:
    evidence_workspace = tmp_path / "evidence-workspace"
    evolution = FailureGuidedSkillEvolution(evidence_workspace).run(
        FailureGuidedEvolutionSpec.load(EVOLUTION)
    )
    base, winner, base_sha = _skill_texts(evidence_workspace, evolution)
    terms = _terms(base, winner)
    promotion = PromotionWorkflow(evidence_workspace)
    workflow = promotion.begin(
        evolution.handoff_path,
        skill_name="python-review-release",
        target_version="2.0.0-fixture",
        actor="fake-evolution",
    )
    confirm_spec = IndependentFinalEvaluationSpec.load(
        _final_spec(
            tmp_path / "final-inputs",
            str(workflow.optimization_job_id),
            split="validation_confirm",
            terms=terms,
        )
    )
    promotion.confirm(workflow.id, confirm_spec)
    locked_spec = IndependentFinalEvaluationSpec.load(
        _final_spec(
            tmp_path / "final-inputs",
            str(workflow.optimization_job_id),
            split="locked_test",
            terms=terms,
        )
    )
    promotion.locked_test(workflow.id, locked_spec)
    approved = promotion.approve(
        workflow.id,
        reviewer="fixture-reviewer",
        reason="approved <script>alert('offline')</script>",
    )
    assert approved.publication is not None
    assert approved.release_manifest is not None
    inputs = evidence_workspace / "release-inputs"
    v1 = _write_json(
        inputs / "v1-manifest.json",
        {
            "schema_version": "ase/skill-version-parent/v1alpha1",
            "skill_name": "python-review-release",
            "version": "1.0.0",
            "content_sha256": base_sha,
            "simulated_evidence": True,
        },
    )
    review = _write_json(
        inputs / "human-review.json",
        approved.release_manifest.human_review.model_dump(mode="json"),
    )
    promotion_release = promotion.store.release_path(workflow.id)
    confirmation_report = (
        evidence_workspace
        / "final-evaluations"
        / "jobs"
        / str(approved.release_manifest.confirmation.final_evaluation_job_id)
        / "reports"
        / "final-report.json"
    )
    assert approved.release_manifest.locked_test is not None
    locked_report = (
        evidence_workspace
        / "final-evaluations"
        / "jobs"
        / str(approved.release_manifest.locked_test.final_evaluation_job_id)
        / "reports"
        / "final-report.json"
    )

    def relative(path: Path) -> str:
        return path.resolve().relative_to(evidence_workspace.resolve()).as_posix()

    config = inputs / "release.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": "ase/evolution-evidence-release-config/v1alpha1",
                "evidence_root": "..",
                "promotion_release_manifest": relative(promotion_release),
                "v1_manifest": relative(v1),
                "v2_manifest": relative(approved.publication.manifest_path),
                "confirmation_report": relative(confirmation_report),
                "locked_test_report": relative(locked_report),
                "human_review": relative(review),
                "evolution_report": relative(evolution.report_json),
                "search_report": relative(evolution.search.report_json),
                "skill_diff": relative(approved.publication.diff_path),
                "evidence_class": "simulated",
                "simulated": True,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    publication_workspace = tmp_path / "publication"
    return config, publication_workspace, evidence_workspace


def _observed_contract_fixture(tmp_path: Path) -> Tuple[Path, Path, Path]:
    """Convert the Fake E2E fixture into a typed observed-evidence contract fixture."""
    config, publication, evidence = _fixture(tmp_path)
    value = yaml.safe_load(config.read_text(encoding="utf-8"))

    def source(name: str) -> Path:
        return evidence / value[name]

    def observed(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: (False if key in {"simulated", "simulated_evidence"} else observed(child))
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [observed(child) for child in value]
        return value

    final_reports = {}
    for role in ("confirmation_report", "locked_test_report"):
        path = source(role)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        report = FinalEvaluationReport.model_validate(observed(envelope["payload"]))
        content = model_bytes(report)
        path.write_bytes(content)
        final_reports[role] = (report, _sha256(content))

    evolution_path = source("evolution_report")
    evolution = EvolutionReport.model_validate(
        observed(json.loads(evolution_path.read_text(encoding="utf-8")))
    )
    _write_json(evolution_path, evolution.model_dump(mode="json"))

    search_path = source("search_report")
    search = observed(json.loads(search_path.read_text(encoding="utf-8")))
    _write_json(search_path, search)

    v1_path = source("v1_manifest")
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    v1["simulated_evidence"] = False
    _write_json(v1_path, v1)

    v2_path = source("v2_manifest")
    v2 = load_model(v2_path.read_bytes(), SkillVersionManifest)
    confirmation, confirmation_sha = final_reports["confirmation_report"]
    locked, locked_sha = final_reports["locked_test_report"]
    validation_ref = v2.validation_confirm.model_copy(
        update={
            "report_sha256": confirmation_sha,
            "simulated": False,
            "final_evaluation_job_id": confirmation.job.id,
        }
    )
    locked_ref = v2.locked_test.model_copy(
        update={
            "report_sha256": locked_sha,
            "simulated": False,
            "final_evaluation_job_id": locked.job.id,
        }
    )
    v2 = v2.model_copy(
        update={
            "validation_confirm": validation_ref,
            "locked_test": locked_ref,
            "simulated_evidence": False,
            "claim_limit": "Observed evidence applies only to the frozen experiment inputs.",
        }
    )
    v2_bytes = model_bytes(v2)
    v2_path.write_bytes(v2_bytes)
    v2_path.with_suffix(".sha256").write_text(
        _sha256(v2_bytes) + "\n", encoding="utf-8"
    )

    promotion_path = source("promotion_release_manifest")
    promotion = load_model(promotion_path.read_bytes(), PromotionReleaseManifest)
    lineage = tuple(
        item.model_copy(
            update={
                "sha256": (
                    _sha256(evolution_path.read_bytes())
                    if item.role == "evolution_report"
                    else _sha256(search_path.read_bytes())
                    if item.role == "search_report"
                    else item.sha256
                ),
                "size_bytes": (
                    len(evolution_path.read_bytes())
                    if item.role == "evolution_report"
                    else len(search_path.read_bytes())
                    if item.role == "search_report"
                    else item.size_bytes
                ),
            }
        )
        for item in promotion.lineage
    )
    promotion = promotion.model_copy(
        update={
            "confirmation": validation_ref,
            "locked_test": locked_ref,
            "lineage": lineage,
            "skill_version_manifest_sha256": _sha256(v2_bytes),
            "simulated": False,
            "claim_limit": "Observed evidence applies only to the frozen experiment inputs.",
        }
    )
    promotion_bytes = model_bytes(promotion)
    promotion_path.write_bytes(promotion_bytes)
    promotion_path.with_suffix(".sha256").write_text(
        _sha256(promotion_bytes) + "\n", encoding="utf-8"
    )

    value["evidence_class"] = "observed_agent"
    value["simulated"] = False
    value["claim_limit"] = promotion.claim_limit
    config.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return config, publication, evidence


def test_release_cli_fake_promotion_e2e_idempotency_and_tamper_detection(
    tmp_path: Path,
) -> None:
    config, publication, _ = _fixture(tmp_path)
    command = [
        "evolution",
        "release",
        "prepare",
        str(config),
        "--workspace",
        str(publication),
    ]
    prepared = runner.invoke(app, command)
    assert prepared.exit_code == 0, prepared.output
    first = json.loads(prepared.output)
    assert first["idempotent_replay"] is False
    release = Path(first["release_dir"])
    assert {path.name for path in release.iterdir()} == {
        "release-manifest.json",
        "release-manifest.sha256",
        "evolution-report.json",
        "evolution-report.html",
        "skill-diff.patch",
        "evidence-index.json",
        "audit-bundle.tar",
        "README.md",
    }

    repeated = runner.invoke(app, command)
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.output)["idempotent_replay"] is True
    assert json.loads(repeated.output)["manifest_sha256"] == first["manifest_sha256"]

    verified = runner.invoke(app, ["evolution", "release", "verify", str(release)])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["valid"] is True
    inspected = runner.invoke(app, ["evolution", "release", "inspect", str(release)])
    assert inspected.exit_code == 0, inspected.output
    summary = json.loads(inspected.output)
    assert summary["simulated"] is True
    assert summary["decision"] == "APPROVED"

    report = json.loads((release / "evolution-report.json").read_text(encoding="utf-8"))
    assert (
        report["skill_versions"]["v2"]["parent_content_sha256"]
        == (report["skill_versions"]["v1"]["content_sha256"])
    )
    assert report["proposal_lineage"]
    assert report["failure_lineage"]
    assert report["stages"]["regression_dev"]["passed"] is True
    assert report["stages"]["validation_confirm"]["decision"] == "CONFIRMED"
    assert report["stages"]["locked_test"]["decision"] == "CONFIRMED"
    assert report["v1_v2_aggregate"]["win"] >= 2
    assert "tokens" in report["v1_v2_aggregate"]["winner"]
    assert "latency_ms" in report["v1_v2_aggregate"]["winner"]
    assert "cost_microusd" in report["v1_v2_aggregate"]["winner"]

    html = (release / "evolution-report.html").read_text(encoding="utf-8")
    assert "default-src 'none'" in html
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert "http://" not in html and "https://" not in html
    with tarfile.open(release / "audit-bundle.tar", mode="r:") as archive:
        assert len(archive.getmembers()) == 9
        assert all(member.isfile() for member in archive.getmembers())

    manifest_tamper = tmp_path / "manifest-tamper" / "evolution-release"
    shutil.copytree(release, manifest_tamper)
    with (manifest_tamper / "release-manifest.json").open("ab") as stream:
        stream.write(b" ")
    invalid_manifest = runner.invoke(app, ["evolution", "release", "verify", str(manifest_tamper)])
    assert invalid_manifest.exit_code != 0
    assert "manifest hash mismatch" in invalid_manifest.output

    parent_tamper = tmp_path / "parent-tamper" / "evolution-release"
    shutil.copytree(release, parent_tamper)
    forged_report = json.loads(
        (parent_tamper / "evolution-report.json").read_text(encoding="utf-8")
    )
    forged_report["skill_versions"]["v2"]["parent_content_sha256"] = "f" * 64
    _write_json(parent_tamper / "evolution-report.json", forged_report)
    _refresh_release_hashes(parent_tamper, "evolution-report.json")
    invalid_parent = runner.invoke(app, ["evolution", "release", "verify", str(parent_tamper)])
    assert invalid_parent.exit_code != 0
    assert "parent lineage mismatch" in invalid_parent.output

    audit_tamper = tmp_path / "audit-tamper" / "evolution-release"
    shutil.copytree(release, audit_tamper)
    audit_path = audit_tamper / "audit-bundle.tar"
    members = []
    with tarfile.open(audit_path, mode="r:") as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            assert extracted is not None
            members.append((member.name, extracted.read()))
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for index, (name, content) in enumerate(members):
            payload = content + (b" " if index == 0 else b"")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    audit_path.write_bytes(stream.getvalue())
    _refresh_release_hashes(audit_tamper, "audit-bundle.tar")
    invalid_audit = runner.invoke(app, ["evolution", "release", "verify", str(audit_tamper)])
    assert invalid_audit.exit_code != 0
    assert "audit bundle member" in invalid_audit.output

    readme = release / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    tampered = runner.invoke(app, ["evolution", "release", "verify", str(release)])
    assert tampered.exit_code != 0
    assert "mismatch" in tampered.output


def test_prepare_rejects_v2_parent_mismatch(tmp_path: Path) -> None:
    config, publication, evidence = _fixture(tmp_path)
    v1 = evidence / "release-inputs" / "v1-manifest.json"
    payload = json.loads(v1.read_text(encoding="utf-8"))
    payload["content_sha256"] = _sha256(b"different-parent")
    _write_json(v1, payload)

    result = runner.invoke(
        app,
        [
            "evolution",
            "release",
            "prepare",
            str(config),
            "--workspace",
            str(publication),
        ],
    )

    assert result.exit_code != 0
    assert "parent hash" in result.output or "promotion Skill hashes" in result.output
    assert not (publication / "evolution-release").exists()


def test_prepare_rejects_promotion_integrity_sidecar_mismatch(tmp_path: Path) -> None:
    config, publication, evidence = _fixture(tmp_path)
    config_value = yaml.safe_load(config.read_text(encoding="utf-8"))
    promotion = evidence / config_value["promotion_release_manifest"]
    promotion.with_suffix(".sha256").write_text("0" * 64 + "\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "evolution",
            "release",
            "prepare",
            str(config),
            "--workspace",
            str(publication),
        ],
    )

    assert result.exit_code != 0
    assert "integrity sidecar mismatch" in result.output
    assert not (publication / "evolution-release").exists()


def test_release_cli_accepts_consistent_observed_evidence_without_running_agent(
    tmp_path: Path,
) -> None:
    config, publication, _ = _observed_contract_fixture(tmp_path)
    prepared = runner.invoke(
        app,
        [
            "evolution",
            "release",
            "prepare",
            str(config),
            "--workspace",
            str(publication),
        ],
    )
    assert prepared.exit_code == 0, prepared.output
    summary = json.loads(prepared.output)
    assert summary["simulated"] is False
    assert summary["evidence_class"] == "observed_agent"

    release = Path(summary["release_dir"])
    verified = runner.invoke(app, ["evolution", "release", "verify", str(release)])
    assert verified.exit_code == 0, verified.output
    inspected = runner.invoke(app, ["evolution", "release", "inspect", str(release)])
    assert inspected.exit_code == 0, inspected.output
    inspection = json.loads(inspected.output)
    assert inspection["simulated"] is False
    assert inspection["evidence_class"] == "observed_agent"

    report = json.loads((release / "evolution-report.json").read_text(encoding="utf-8"))
    assert report["simulated"] is False
    assert report["evidence_class"] == "observed_agent"
    html = (release / "evolution-report.html").read_text(encoding="utf-8")
    assert "OBSERVED AGENT EVIDENCE" in html
    assert "SIMULATED / FIXTURE EVIDENCE" not in html
    readme = (release / "README.md").read_text(encoding="utf-8")
    assert "Evidence class: `observed_agent`" in readme
    assert "does not invoke a model or Agent" in readme


def test_release_cli_rejects_mixed_observed_and_simulated_evidence(tmp_path: Path) -> None:
    config, publication, evidence = _observed_contract_fixture(tmp_path)
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    v1_path = evidence / value["v1_manifest"]
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    v1["simulated_evidence"] = True
    _write_json(v1_path, v1)

    result = runner.invoke(
        app,
        [
            "evolution",
            "release",
            "prepare",
            str(config),
            "--workspace",
            str(publication),
        ],
    )
    assert result.exit_code != 0
    assert "evidence class" in result.output
