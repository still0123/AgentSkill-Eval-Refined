"""JSON Schema bundle generation for persisted contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Type

from pydantic import BaseModel, JsonValue

from agentskill_eval_contracts.artifacts import ArtifactManifest
from agentskill_eval_contracts.benchmark import (
    BenchmarkCandidate,
    BenchmarkDatasetVersion,
    BenchmarkJob,
)
from agentskill_eval_contracts.evidence import (
    FrozenInputManifest,
    ReplayBundleManifest,
    SecurityScanEvidence,
    SkillActivationEvidence,
)
from agentskill_eval_contracts.experiment import ExperimentManifest, ExperimentVariant, PairBlock
from agentskill_eval_contracts.measurements import RunMeasurement
from agentskill_eval_contracts.run import Run, RunAttempt
from agentskill_eval_contracts.trace import FailureDiagnosis, PairTraceDiff, TraceManifest

SCHEMA_MODELS: Dict[str, Type[BaseModel]] = {
    model.__name__: model
    for model in (
        ArtifactManifest,
        ExperimentManifest,
        ExperimentVariant,
        PairBlock,
        Run,
        RunAttempt,
        RunMeasurement,
        FrozenInputManifest,
        SkillActivationEvidence,
        SecurityScanEvidence,
        ReplayBundleManifest,
        TraceManifest,
        FailureDiagnosis,
        PairTraceDiff,
        BenchmarkJob,
        BenchmarkCandidate,
        BenchmarkDatasetVersion,
    )
}


def build_schema_bundle() -> Dict[str, JsonValue]:
    """Build a versioned serialization-schema bundle for external tooling."""
    return {
        "schema_version": "ase/v1alpha1",
        "schemas": {
            name: model.model_json_schema(mode="serialization")
            for name, model in sorted(SCHEMA_MODELS.items())
        },
    }


def export_schema_bundle(destination: Path) -> Path:
    """Write the schema bundle to a deterministic UTF-8 JSON file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(build_schema_bundle(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination
