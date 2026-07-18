from typing import Optional
from uuid import uuid4

import pytest

from agentskill_eval_contracts import (
    AttributionRole,
    DiagnosticFinding,
    FailureDiagnosis,
    FailureLabel,
)
from agentskill_eval_skill_optimizer import (
    EvolutionError,
    FailureBundleSecretScan,
    FailureEvidenceBundle,
    ObservedFailureProvenance,
)


def _diagnosis() -> FailureDiagnosis:
    return FailureDiagnosis(
        run_id=uuid4(),
        attempt_id=uuid4(),
        status="diagnosed",
        findings=(
            DiagnosticFinding(
                label=FailureLabel.VERIFICATION,
                role=AttributionRole.ROOT_CAUSE,
                confidence=1,
                rule_id="fixture.observed",
                rationale="Observed deterministic verification failure.",
            ),
        ),
    )


def _provenance(parent_bundle_sha256: Optional[str] = None) -> ObservedFailureProvenance:
    return ObservedFailureProvenance(
        source_experiment_id=uuid4(),
        source_experiment_sha256="a" * 64,
        source_real_run_sha256="b" * 64,
        source_report_sha256="c" * 64,
        parent_bundle_sha256=parent_bundle_sha256,
        provider="deepseek",
        model="deepseek-v4-pro",
        runner_version="skill-up version 0.5.0",
        runner_sha256="d" * 64,
        agent_config_sha256="e" * 64,
        dataset_version_sha256="f" * 64,
        secret_scan=FailureBundleSecretScan(
            configured_secret_count=1,
            exact_values_available=True,
            source_attempt_scan_verified=True,
        ),
    )


def test_real_observed_bundle_requires_immutable_provenance() -> None:
    legacy = FailureEvidenceBundle(
        schema_version="ase/failure-evidence-bundle/v1alpha1",
        name="legacy",
        split="train",
        diagnoses=(_diagnosis(),),
        agent_provider="deepseek",
        agent_model="deepseek-v4-pro",
    )

    with pytest.raises(EvolutionError, match="missing immutable provenance"):
        legacy.require_observed_provenance(provider="deepseek", model="deepseek-v4-pro")


def test_provenance_requires_matching_proposal_parent_hash() -> None:
    parent = "1" * 64
    bundle = FailureEvidenceBundle(
        schema_version="ase/failure-evidence-bundle/v1alpha1",
        name="derived",
        split="train",
        diagnoses=(_diagnosis(),),
        agent_provider="deepseek",
        agent_model="deepseek-v4-pro",
        provenance=_provenance(parent),
    )

    assert (
        bundle.require_observed_provenance(
            provider="deepseek",
            model="deepseek-v4-pro",
            proposal_bundle_sha256=parent,
        ).parent_bundle_sha256
        == parent
    )
    with pytest.raises(EvolutionError, match="parent hash"):
        bundle.require_observed_provenance(
            provider="deepseek",
            model="deepseek-v4-pro",
            proposal_bundle_sha256="2" * 64,
        )


def test_provenance_rejects_simulated_source() -> None:
    payload = _provenance().model_dump(mode="python")
    payload["simulated"] = True

    with pytest.raises(ValueError, match="literal"):
        ObservedFailureProvenance.model_validate(payload)
