from pathlib import Path

import pytest
from pydantic import ValidationError

from agentskill_eval_benchmark_gen import BenchmarkGenerationSpec

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_single_source_spec_is_preserved() -> None:
    spec = BenchmarkGenerationSpec.load(
        ROOT / "examples/benchmark-sources/more-itertools-generation.example.yaml"
    )

    sources = spec.repository_sources()

    assert spec.schema_version == "ase/benchmark-generation/v1alpha1"
    assert len(sources) == 1
    assert sources[0].key == "primary"
    assert all(candidate.source_key == "primary" for candidate in spec.candidates)


def test_multi_source_semantic_payload_excludes_machine_paths() -> None:
    spec = BenchmarkGenerationSpec.load(
        ROOT / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    relocated = spec.model_copy(
        update={
            "sources": tuple(
                source.model_copy(
                    update={"repository_path": Path("/different-machine") / source.key}
                )
                for source in spec.sources
            )
        }
    )

    assert spec.semantic_payload() == relocated.semantic_payload()
    assert len(spec.repository_sources()) == 2


def test_multi_source_spec_rejects_unknown_candidate_source() -> None:
    spec = BenchmarkGenerationSpec.load(
        ROOT / "examples/benchmark-sources/cross-repository-generation.example.yaml"
    )
    payload = spec.model_dump(mode="json")
    payload["candidates"][0]["source_key"] = "missing-source"

    with pytest.raises(ValidationError, match="unknown candidate source_key"):
        BenchmarkGenerationSpec.model_validate(payload)
