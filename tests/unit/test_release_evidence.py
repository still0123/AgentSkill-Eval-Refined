from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agentskill_eval_skill_optimizer.release_evidence import (
    EvidenceReleaseError,
    EvidenceReleasePreparer,
    EvidenceReleaseSpec,
    ReleaseArtifact,
)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _fixture(root: Path) -> EvidenceReleaseSpec:
    v1_hash = _sha(b"skill-v1")
    v2_hash = _sha(b"skill-v2")
    _write_json(
        root / "v1.json",
        {
            "skill_name": "python-review",
            "version": "1.0.0",
            "content_sha256": v1_hash,
            "simulated_evidence": True,
        },
    )
    _write_json(
        root / "v2.json",
        {
            "skill_name": "python-review",
            "version": "2.0.0-fixture",
            "content_sha256": v2_hash,
            "parent_content_sha256": v1_hash,
            "simulated_evidence": True,
        },
    )
    _write_json(
        root / "report.json",
        {
            "simulated": True,
            "evidence_class": "fixture",
            "baseline_pass_rate": 0.5,
            "treatment_pass_rate": 0.75,
            "workspace": str(root / "private" / "workspace"),
        },
    )
    artifact = root / "audit" / "trace.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"event":"fake-evaluation"}\n', encoding="utf-8")
    return EvidenceReleaseSpec(
        release_id="fixture-v2-release",
        evidence_root=root,
        report_path=Path("report.json"),
        v1_manifest_path=Path("v1.json"),
        v2_manifest_path=Path("v2.json"),
        artifacts=(
            ReleaseArtifact(
                source=Path("audit/trace.json"),
                bundle_path="traces/trace.json",
                sha256=_sha(artifact.read_bytes()),
            ),
        ),
    )


def test_prepare_creates_redacted_immutable_release_and_verifies_bundle(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    preparer = EvidenceReleasePreparer(tmp_path / "publication")

    result = preparer.prepare(spec)
    manifest = preparer.verify(result.release_dir)

    assert manifest["evidence_class"] == "simulated"
    assert manifest["simulated"] is True
    assert result.release_sha256 == _sha(result.manifest_path.read_bytes())
    report = result.report_path.read_text(encoding="utf-8")
    assert str(evidence) not in report
    assert "<EVIDENCE_ROOT>" in report
    assert "python-review" in result.comparison_path.read_text(encoding="utf-8")
    assert result.artifact_manifest_path.is_file()
    with pytest.raises(EvidenceReleaseError, match="immutable release already exists"):
        preparer.prepare(spec)


@pytest.mark.parametrize("bundle_path", ["../trace.json", "/tmp/trace.json", "a\\b.json"])
def test_path_traversal_is_rejected(tmp_path: Path, bundle_path: str) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    artifact = spec.artifacts[0]
    unsafe = EvidenceReleaseSpec(
        **{
            **spec.__dict__,
            "artifacts": (ReleaseArtifact(artifact.source, bundle_path, artifact.sha256),),
        }
    )
    with pytest.raises(EvidenceReleaseError, match="unsafe release path"):
        EvidenceReleasePreparer(tmp_path / "publication").prepare(unsafe)


def test_hash_mismatch_and_tampered_release_are_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    artifact = spec.artifacts[0]
    wrong = EvidenceReleaseSpec(
        **{
            **spec.__dict__,
            "artifacts": (ReleaseArtifact(artifact.source, artifact.bundle_path, "0" * 64),),
        }
    )
    preparer = EvidenceReleasePreparer(tmp_path / "publication")
    with pytest.raises(EvidenceReleaseError, match="artifact hash mismatch"):
        preparer.prepare(wrong)

    result = preparer.prepare(spec)
    result.report_path.write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceReleaseError, match="size mismatch|hash mismatch"):
        preparer.verify(result.release_dir)


@pytest.mark.parametrize(
    "payload",
    [
        {"simulated": True, "api_key": "not-even-needed"},
        {"simulated": True, "message": "Authorization: Bearer abcdefghijklmnop"},
        {"simulated": True, "message": "sk-abcdefghijklmnop"},
    ],
)
def test_secret_material_is_rejected_before_publication(tmp_path: Path, payload: object) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    _write_json(evidence / "report.json", payload)
    with pytest.raises(EvidenceReleaseError, match="Secret"):
        EvidenceReleasePreparer(tmp_path / "publication").prepare(spec)
    assert not (tmp_path / "publication" / "releases" / spec.release_id).exists()


def test_mixed_real_and_simulated_evidence_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    _write_json(
        evidence / "report.json",
        {
            "simulated": True,
            "attempts": [{"simulated": False, "provider": "fake-process"}],
        },
    )
    with pytest.raises(EvidenceReleaseError, match="must not be mixed"):
        EvidenceReleasePreparer(tmp_path / "publication").prepare(spec)


def test_lineage_mismatch_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    v2 = json.loads((evidence / "v2.json").read_text(encoding="utf-8"))
    v2["parent_content_sha256"] = "f" * 64
    _write_json(evidence / "v2.json", v2)
    with pytest.raises(EvidenceReleaseError, match="parent hash"):
        EvidenceReleasePreparer(tmp_path / "publication").prepare(spec)


def test_storage_envelope_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    payload = json.loads((evidence / "v2.json").read_text(encoding="utf-8"))
    _write_json(
        evidence / "v2.json",
        {
            "storage_schema_version": "ase/storage/v1",
            "payload_sha256": "0" * 64,
            "payload": payload,
        },
    )
    with pytest.raises(EvidenceReleaseError, match="envelope hash mismatch"):
        EvidenceReleasePreparer(tmp_path / "publication").prepare(spec)


def test_secret_in_audit_artifact_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    spec = _fixture(evidence)
    artifact = evidence / spec.artifacts[0].source
    artifact.write_text("provider=sk-abcdefghijklmnop", encoding="utf-8")
    leaked = EvidenceReleaseSpec(
        **{
            **spec.__dict__,
            "artifacts": (
                ReleaseArtifact(
                    spec.artifacts[0].source,
                    spec.artifacts[0].bundle_path,
                    _sha(artifact.read_bytes()),
                ),
            ),
        }
    )
    with pytest.raises(EvidenceReleaseError, match="Secret"):
        EvidenceReleasePreparer(tmp_path / "publication").prepare(leaked)
