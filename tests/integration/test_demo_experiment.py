from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentskill_eval_benchmark_gen import DemoExperimentRunner, DemoRunConfig
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

    first_bundle = ReplayBundleWriter(store).write(result.experiment_id, tmp_path / "first.tar")
    second_bundle = ReplayBundleWriter(store).write(result.experiment_id, tmp_path / "second.tar")
    assert first_bundle.path.read_bytes() == second_bundle.path.read_bytes()
    verified = ReplayBundleWriter.verify(first_bundle.path)
    assert verified == first_bundle.manifest
    paths = {entry.path for entry in verified.files}
    assert any("/inputs/case_source/" in path for path in paths)
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
