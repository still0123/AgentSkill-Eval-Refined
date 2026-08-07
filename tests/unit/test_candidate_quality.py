from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from uuid import uuid4

import pytest

from agentskill_eval_contracts import (
    CandidateEvaluation,
    CandidateOrigin,
    FailureLabel,
    SearchCaseResult,
    SearchEvaluationStage,
    SkillCandidate,
    SkillCandidateStatus,
)
from agentskill_eval_skill_optimizer import (
    BenchmarkGuidedSkillSearch,
    CandidateQualityError,
    CandidateQualityGate,
    EvaluatorSpec,
    ImprovementHypothesis,
    MutationSpec,
    OptimizationSearchSpec,
    SearchAlgorithmSpec,
    SearchBudgetSpec,
    SearchConstraintSpec,
    SkillSearchError,
)


def _hypothesis(candidate_id: str, instruction: str) -> ImprovementHypothesis:
    return ImprovementHypothesis(
        id=candidate_id,
        failure_label=FailureLabel.TOOL_ARGUMENT,
        hypothesis="A sufficiently specific hypothesis for a deterministic quality test.",
        instruction=instruction,
        evidence_refs=("diagnosis://test/rule.tool_argument",),
    )


def test_default_mutation_keeps_append_behavior() -> None:
    mutation = MutationSpec(
        id="verify-after",
        hypothesis="Verification may improve task execution.",
        instruction="Run the targeted test after editing.",
    )

    content = BenchmarkGuidedSkillSearch._mutate(b"# Skill\n", (mutation,)).decode()

    assert mutation.operation == "append"
    assert "## Candidate guidance" in content
    assert "- Run the targeted test after editing." in content


def test_structured_mutation_replaces_named_section() -> None:
    mutation = MutationSpec(
        id="replace-workflow",
        hypothesis="A complete workflow may improve task execution.",
        instruction="Inspect, edit, run the targeted test, and verify the result.",
        operation="replace_section",
        target_section="Workflow",
    )
    base = (
        b"# Skill\n\n## Workflow\n\nOld workflow.\n\n"
        b"### Detail\n\nOld detail.\n\n## Limits\n\nKeep this.\n"
    )

    content = BenchmarkGuidedSkillSearch._mutate(base, (mutation,)).decode()

    assert "Old workflow." not in content
    assert "Old detail." not in content
    assert "## Limits\n\nKeep this." in content
    assert "<!-- mutation:replace-workflow;" in content
    assert "Inspect, edit, run the targeted test, and verify the result." in content


@pytest.mark.parametrize(
    "base, match_count",
    [
        (b"# Skill\n\n## Other\n", 0),
        (b"# Skill\n\n## Workflow\n\nOne.\n\n## Workflow\n\nTwo.\n", 2),
    ],
)
def test_structured_mutation_rejects_missing_or_duplicate_section(
    base: bytes, match_count: int
) -> None:
    mutation = MutationSpec(
        id="replace-workflow",
        hypothesis="A complete workflow may improve task execution.",
        instruction="Inspect, edit, run the targeted test, and verify the result.",
        operation="replace_section",
        target_section="Workflow",
    )

    with pytest.raises(SkillSearchError, match=rf"matched {match_count} sections"):
        BenchmarkGuidedSkillSearch._mutate(base, (mutation,))


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


def test_quality_gate_accepts_observed_test_execution_guidance(tmp_path: Path) -> None:
    gate = CandidateQualityGate(tmp_path)
    hypothesis = _hypothesis(
        "rerun-and-parse",
        "Re-execute the deterministic test, parse its output, and confirm the exit code.",
    )

    report = gate.materialize_hypotheses(
        proposal_job_id="proposal-test",
        proposal_manifest_sha256="c" * 64,
        parent_content=b"# Base Skill\n",
        hypotheses=(
            hypothesis,
            _hypothesis("inspect-path", "Inspect the changed path before editing."),
            _hypothesis("verify-result", "Verify the result after editing."),
        ),
        max_candidates=3,
    )

    candidate = next(
        item for item in report.candidates if item.candidate_id == "rerun-and-parse"
    )
    assert candidate.accepted is True


def test_quality_gate_accepts_exactly_two_hypotheses(tmp_path: Path) -> None:
    report = CandidateQualityGate(tmp_path).materialize_hypotheses(
        proposal_job_id="two-candidate-proposal",
        proposal_manifest_sha256="d" * 64,
        parent_content=b"# Base Skill\n",
        hypotheses=(
            _hypothesis("inspect-first", "Inspect the failure evidence before editing."),
            _hypothesis(
                "verify-after",
                "Run the targeted test and verify the result after editing.",
            ),
        ),
        max_candidates=2,
    )

    assert len(report.accepted_candidate_ids) == 2


def test_one_case_search_rejects_zero_gain_candidates(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Base Skill\n", encoding="utf-8")
    dataset = tmp_path / "validation-search.yaml"
    dataset.write_text(
        "schema_version: ase/optimizer-validation/v1alpha1\n"
        "name: one-case\n"
        "version: '1'\n"
        "split: validation_search\n"
        "simulated: true\n"
        "cases:\n"
        "  - id: case-one\n"
        "    required_terms: [unreachable-marker]\n",
        encoding="utf-8",
    )
    spec = OptimizationSearchSpec(
        schema_version="ase/optimization-search/v1alpha1",
        name="one-case-zero-gain",
        base_skill_path=skill,
        manual_skill_path=skill,
        validation_search_path=dataset,
        mutations=(
            MutationSpec(
                id="inspect-first",
                hypothesis="Inspecting evidence may improve task execution.",
                instruction="Inspect the available evidence before editing.",
            ),
            MutationSpec(
                id="verify-after",
                hypothesis="Verification may improve task execution.",
                instruction="Run the targeted test after editing.",
            ),
        ),
        search=SearchAlgorithmSpec(
            subset_size=1,
            promote_search_candidates=1,
            include_auxiliary_candidates=False,
        ),
        constraints=SearchConstraintSpec(min_absolute_gain=0.01),
        budget=SearchBudgetSpec(
            max_candidate_case_evaluations=3,
            timeout_seconds=10,
        ),
        evaluator=EvaluatorSpec(
            type="simulated_keyword",
            version="test",
            simulated=True,
        ),
    )

    with pytest.raises(SkillSearchError, match="no search-origin candidate"):
        BenchmarkGuidedSkillSearch(tmp_path / "workspace").run(spec)


def test_search_excludes_invalid_candidate_from_pareto_selection(tmp_path: Path) -> None:
    Outcome = Literal["pass", "fail", "invalid"]

    def evaluation(
        outcomes: tuple[Outcome, Outcome], tokens: tuple[int, int]
    ) -> CandidateEvaluation:
        results = tuple(
            SearchCaseResult(
                case_id=f"case-{index}",
                passed=outcome == "pass",
                score=1 if outcome == "pass" else 0,
                input_tokens=token_count,
                output_tokens=0,
                latency_ms=token_count,
                outcome=outcome,
            )
            for index, (outcome, token_count) in enumerate(zip(outcomes, tokens), start=1)
        )
        return CandidateEvaluation(
            stage=SearchEvaluationStage.FULL,
            dataset_sha256="d" * 64,
            evaluator_sha256="e" * 64,
            case_ids=tuple(item.case_id for item in results),
            results=results,
            pass_rate=sum(item.passed for item in results) / len(results),
            mean_score=sum(item.score for item in results) / len(results),
            total_tokens=sum(tokens),
            total_latency_ms=sum(tokens),
            total_cost_microusd=None,
            simulated=False,
            evaluated_at=datetime.now(timezone.utc),
        )

    def candidate(
        origin: CandidateOrigin,
        outcomes: tuple[Outcome, Outcome],
        tokens: tuple[int, int],
        content_bytes: int,
    ) -> SkillCandidate:
        return cast(
            SkillCandidate,
            SimpleNamespace(
                id=uuid4(),
                origin=origin,
                status=SkillCandidateStatus.FULL_VALIDATED,
                evaluations=(evaluation(outcomes, tokens),),
                content_bytes=content_bytes,
            ),
        )

    original = candidate(CandidateOrigin.ORIGINAL, ("fail", "fail"), (10, 10), 100)
    invalid = candidate(CandidateOrigin.SEARCH, ("pass", "invalid"), (5, 5), 50)
    valid = candidate(CandidateOrigin.SEARCH, ("pass", "fail"), (11, 11), 100)
    spec = cast(
        OptimizationSearchSpec,
        SimpleNamespace(
            constraints=SearchConstraintSpec(
                min_absolute_gain=0.1,
                max_loss_cases=0,
                max_token_overhead_ratio=0.25,
            )
        ),
    )

    winner, _ = BenchmarkGuidedSkillSearch(tmp_path)._select_winner(
        (original, invalid, valid),
        spec,
    )

    assert winner.id == valid.id
