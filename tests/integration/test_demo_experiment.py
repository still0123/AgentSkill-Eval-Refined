from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from agentskill_eval_benchmark_gen import DemoEvidencePack, DemoExperimentRunner, DemoRunConfig
from agentskill_eval_experiment import (
    AnalysisConfig,
    BundleError,
    ExperimentAnalyzer,
    LocalExperimentStore,
    ReplayBundleWriter,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "examples/datasets/python-review-demo"
SKILL = ROOT / "examples/skills/python-review-v1"


def test_mock_demo_runs_72_logical_runs_and_writes_labeled_reports(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    result = asyncio.run(
        DemoExperimentRunner().run(
            DemoRunConfig(
                workspace=workspace,
                dataset_root=DATASET,
                skill_root=SKILL,
                bootstrap_resamples=50,
            )
        )
    )

    assert result.simulated is True
    assert result.logical_runs == 72
    assert result.completed_runs == 72
    assert result.invalid_runs == 0
    assert result.report_paths.json_path.is_file()
    assert result.report_paths.html_path.is_file()
    verification = DemoEvidencePack.verify(workspace)
    assert verification["valid"] is True
    assert verification["total_runs"] == 72
    assert verification["audit_bundle_verified"] is True
    evidence_index = json.loads((workspace / "evidence-index.json").read_text(encoding="utf-8"))
    assert evidence_index["schema_version"] == "ase/demo-evidence-index/v1alpha1"
    assert set(evidence_index["hashes"]) == {
        "dataset_sha256",
        "skill_sha256",
        "runner_sha256",
        "environment_sha256",
    }
    first_bundle_bytes = (workspace / "audit-bundle.tar").read_bytes()
    repeated = asyncio.run(
        DemoExperimentRunner().run(
            DemoRunConfig(
                workspace=workspace,
                dataset_root=DATASET,
                skill_root=SKILL,
                bootstrap_resamples=50,
            )
        )
    )
    assert repeated.experiment_id == result.experiment_id
    assert len(list((workspace / "experiments").iterdir())) == 1
    assert (workspace / "audit-bundle.tar").read_bytes() == first_bundle_bytes

    store = LocalExperimentStore(workspace)
    experiment = store.load_experiment(result.experiment_id)
    assert experiment.protocol_snapshot["evidence_mode"] == "simulated_fixture"
    assert experiment.statistics_plan["inference"] == "descriptive_only"
    statistics = ExperimentAnalyzer(store).analyze(
        result.experiment_id,
        AnalysisConfig(
            result.control_variant_id,
            result.treatment_variant_id,
            bootstrap_resamples=20,
            min_independent_groups=50,
        ),
    )
    assert statistics.primary_assignment_based.control_pass_rate == 0.5
    assert statistics.primary_assignment_based.treatment_pass_rate == 10 / 12
    assert statistics.wtl.win == 5
    assert statistics.wtl.loss == 1
    assert statistics.wtl.tie_positive == 5
    assert statistics.wtl.tie_negative == 1
    assert statistics.inference_ready is False
    assert statistics.tokens.control_mean == 150
    assert statistics.tokens.treatment_mean == 260

    html = result.report_paths.html_path.read_text(encoding="utf-8")
    assert "SIMULATED DEMO" in html
    assert "not Agent or Skill performance evidence" in html
    bundle = json.loads(result.report_paths.json_path.read_text(encoding="utf-8"))
    assert bundle["experiment"]["protocol_snapshot"]["demo_only"] is True
    trace_data = bundle["trace_intelligence"]
    assert len(trace_data["traces"]) == 72
    assert len(trace_data["diagnoses"]) == 72
    assert len(trace_data["pair_diffs"]) == 36
    assert {item["status"] for item in trace_data["diagnoses"]} == {
        "abstained",
        "no_failure",
    }

    first_bundle = ReplayBundleWriter(store).write(result.experiment_id, tmp_path / "first.tar")
    second_bundle = ReplayBundleWriter(store).write(result.experiment_id, tmp_path / "second.tar")
    assert first_bundle.path.read_bytes() == second_bundle.path.read_bytes()
    verified = ReplayBundleWriter.verify(first_bundle.path)
    assert verified == first_bundle.manifest
    paths = {entry.path for entry in verified.files}
    assert any("/inputs/case_source/" in path for path in paths)
    assert any(path.endswith("/trace.json") for path in paths)
    assert any(path.endswith("/failure-diagnosis.json") for path in paths)
    assert any(path.endswith("/reports/report.json") for path in paths)
    assert not any("index.sqlite" in path or path.endswith("run.lock") for path in paths)

    corrupted = bytearray(first_bundle.path.read_bytes())
    marker = b"SIMULATED DEMO"
    offset = corrupted.find(marker)
    assert offset >= 0
    corrupted[offset] ^= 1
    tampered = tmp_path / "tampered.tar"
    tampered.write_bytes(corrupted)
    with pytest.raises(BundleError, match="digest mismatch"):
        ReplayBundleWriter.verify(tampered)

    index_path = workspace / "evidence-index.json"
    original_index = index_path.read_bytes()
    evidence_index["files"] = []
    index_path.write_text(json.dumps(evidence_index), encoding="utf-8")
    with pytest.raises(ValueError, match="file index"):
        DemoEvidencePack.verify(workspace)

    index_path.write_bytes(original_index)
    paired_results = workspace / "paired-results.json"
    paired = json.loads(paired_results.read_text(encoding="utf-8"))
    paired["win"] = 99
    paired["logical_runs"] = 999
    paired_results.write_text(json.dumps(paired), encoding="utf-8")
    evidence_index = json.loads(original_index)
    paired_entry = next(
        entry for entry in evidence_index["files"] if entry["path"] == "paired-results.json"
    )
    paired_entry["size_bytes"] = paired_results.stat().st_size
    paired_entry["sha256"] = hashlib.sha256(paired_results.read_bytes()).hexdigest()
    index_path.write_text(json.dumps(evidence_index), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the audited experiment report"):
        DemoEvidencePack.verify(workspace)
