"""Memory/RAG dataset and trace contract tests."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentskill_eval_memory_rag_lab import MemoryRagDataset, secret_summary

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "examples/memory-rag/dataset.yaml"


def _raw() -> dict[str, object]:
    value = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_dataset_validates_four_independent_evaluation_layers() -> None:
    dataset = MemoryRagDataset.load(DATASET)
    assert len(dataset.cases) == 4
    assert {case.kind for case in dataset.cases} == {"retrieval_generation", "memory"}
    assert dataset.simulated is True


def test_duplicate_documents_and_missing_gold_are_rejected() -> None:
    duplicate = _raw()
    case = duplicate["cases"][0]  # type: ignore[index]
    case["documents"].append(case["documents"][0])  # type: ignore[index]
    with pytest.raises(ValidationError, match="unique"):
        MemoryRagDataset.model_validate(duplicate)

    missing = _raw()
    case = missing["cases"][0]  # type: ignore[index]
    case["gold_document_ids"] = ["missing"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="do not exist"):
        MemoryRagDataset.model_validate(missing)


def test_loader_rejects_symlink_and_path_escape(tmp_path: Path) -> None:
    target = tmp_path / "dataset.yaml"
    target.write_text(DATASET.read_text(encoding="utf-8"), encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic-link"):
        MemoryRagDataset.load(link, allowed_root=tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        MemoryRagDataset.load(DATASET, allowed_root=tmp_path)


def test_memory_value_summary_never_contains_original_value() -> None:
    original = "sensitive-memory-value"
    summary = secret_summary(original)
    assert summary["redacted"] is True
    assert summary["length"] == len(original)
    assert original not in str(summary)
