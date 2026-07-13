from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentskill_eval_contracts import (
    BenchmarkCandidate,
    BenchmarkCandidateStatus,
    CandidateTransition,
    ReviewDecision,
    stable_sha256,
)


def transitions(*statuses: BenchmarkCandidateStatus) -> tuple[CandidateTransition, ...]:
    previous = None
    result = []
    for sequence, status in enumerate(statuses, start=1):
        result.append(
            CandidateTransition(
                sequence=sequence,
                from_status=previous,
                to_status=status,
                occurred_at=datetime.now(timezone.utc),
                actor="test",
                input_sha256=stable_sha256((sequence, "input")),
                output_sha256=stable_sha256((sequence, "output")),
            )
        )
        previous = status
    return tuple(result)


def test_candidate_reject_requires_reason() -> None:
    with pytest.raises(ValidationError, match="rejection reasons"):
        BenchmarkCandidate(
            id=uuid4(),
            job_id=uuid4(),
            key="candidate-one",
            task="A sufficiently concrete task",
            target_split="validation_search",
            status=BenchmarkCandidateStatus.REJECTED,
            transitions=transitions(
                BenchmarkCandidateStatus.INGESTED,
                BenchmarkCandidateStatus.REJECTED,
            ),
        )


def test_published_candidate_requires_human_approval() -> None:
    with pytest.raises(ValidationError, match="requires approval"):
        BenchmarkCandidate(
            id=uuid4(),
            job_id=uuid4(),
            key="candidate-one",
            task="A sufficiently concrete task",
            target_split="validation_search",
            status=BenchmarkCandidateStatus.PUBLISHED,
            review_decision=ReviewDecision.REJECTED,
            rejection_reasons=("not approved",),
            transitions=transitions(
                BenchmarkCandidateStatus.INGESTED,
                BenchmarkCandidateStatus.RECONSTRUCTED,
                BenchmarkCandidateStatus.VERIFIED,
                BenchmarkCandidateStatus.DEDUPED,
                BenchmarkCandidateStatus.REVIEWED,
                BenchmarkCandidateStatus.PUBLISHED,
            ),
        )
