"""Tests for public JSON Schema generation and CLI export."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentskill_eval_cli.main import app
from agentskill_eval_contracts import build_schema_bundle

runner = CliRunner()


def test_schema_bundle_contains_core_persisted_models() -> None:
    bundle = build_schema_bundle()
    schemas = bundle["schemas"]

    assert bundle["schema_version"] == "ase/v1alpha1"
    assert isinstance(schemas, dict)
    assert {"ExperimentVariant", "PairBlock", "Run", "RunAttempt", "RunMeasurement"} <= set(schemas)
    assert {"TraceManifest", "FailureDiagnosis", "PairTraceDiff"} <= set(schemas)
    assert {"OptimizationJob", "SkillCandidate"} <= set(schemas)
    assert {"FinalEvaluationJob", "FinalEvaluationReport"} <= set(schemas)
    assert {
        "RealPreflightReport",
        "RealAttemptEvidence",
        "RealEvidenceRunManifest",
        "RealExperimentReport",
    } <= set(schemas)
    assert len(schemas) == len(set(schemas))


def test_cli_exports_valid_json_schema_bundle(tmp_path: Path) -> None:
    destination = tmp_path / "schemas" / "ase-v1alpha1.json"

    result = runner.invoke(app, ["schema", "export", str(destination)])

    assert result.exit_code == 0
    assert result.stdout.strip() == str(destination)
    exported = json.loads(destination.read_text(encoding="utf-8"))
    assert exported["schema_version"] == "ase/v1alpha1"
    assert "Run" in exported["schemas"]
