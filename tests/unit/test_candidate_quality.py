from __future__ import annotations

from pathlib import Path

import pytest

from agentskill_eval_contracts import FailureLabel
from agentskill_eval_skill_optimizer import (
    CandidateQualityError,
    CandidateQualityGate,
    ImprovementHypothesis,
)


def _hypothesis(candidate_id: str, instruction: str) -> ImprovementHypothesis:
    return ImprovementHypothesis(
        id=candidate_id,
        failure_label=FailureLabel.TOOL_ARGUMENT,
        hypothesis="A sufficiently specific hypothesis for a deterministic quality test.",
        instruction=instruction,
        evidence_refs=("diagnosis://test/rule.tool_argument",),
    )


def test_quality_gate_materializes_candidates_and_records_rejections(tmp_path: Path) -> None:
    gate = CandidateQualityGate(tmp_path)
    base = (
        b"# Skill\n\n"
        b"Read the relevant code, run the targeted test, and verify the result after editing.\n"
    )
    hypotheses = (
        _hypothesis(
            "inspect-schema-before-edit",
            "Inspect the exposed edit schema before calling the tool and use its exact arguments.",
        ),
        _hypothesis(
            "duplicate-inspect-schema",
            "Inspect the exposed edit schema before calling the tool and use its exact arguments.",
        ),
        _hypothesis(
            "unprovided-wrapper",
            "Use a standard tool wrapper that translates canonical arguments before invocation.",
        ),
        _hypothesis(
            "leaking-case",
            "Read and fix the case-secret-001 defect, then run the targeted test.",
        ),
    )

    report = gate.materialize_hypotheses(
        proposal_job_id="proposal-test",
        proposal_manifest_sha256="a" * 64,
        parent_content=base,
        hypotheses=hypotheses,
        case_tokens=("case-secret-001",),
        max_candidates=3,
    )

    assert report.accepted_candidate_ids == ("inspect-schema-before-edit",)
    rejected = {item.candidate_id: item.rejection_reasons for item in report.candidates}
    assert "duplicate_or_near_duplicate_candidate" in rejected["duplicate-inspect-schema"]
    assert "requires_unprovided_tooling" in rejected["unprovided-wrapper"]
    assert "benchmark_or_case_leakage" in rejected["leaking-case"]
    accepted_path = tmp_path / "candidate-skills" / "inspect-schema-before-edit" / "SKILL.md"
    assert accepted_path.is_file()
    assert "proposal-hypothesis:inspect-schema-before-edit" in accepted_path.read_text()
    assert gate.verify(tmp_path / "candidate-quality-report.json") == report


def test_quality_gate_is_idempotent_and_detects_report_drift(tmp_path: Path) -> None:
    gate = CandidateQualityGate(tmp_path)
    hypotheses = tuple(
        _hypothesis(f"candidate-{index}", f"Inspect and verify edit result number {index}.")
        for index in range(1, 4)
    )
    kwargs = dict(
        proposal_job_id="proposal-test",
        proposal_manifest_sha256="b" * 64,
        parent_content=b"# Base Skill\n",
        hypotheses=hypotheses,
    )
    first = gate.materialize_hypotheses(**kwargs)
    assert gate.materialize_hypotheses(**kwargs) == first

    report = tmp_path / "candidate-quality-report.json"
    report.write_text(report.read_text().replace('"proposal-test"', '"tampered"'))
    with pytest.raises(CandidateQualityError, match="different content"):
        gate.materialize_hypotheses(**kwargs)
