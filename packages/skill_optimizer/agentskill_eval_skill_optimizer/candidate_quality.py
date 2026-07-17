"""Deterministic materialization and quality gates for generated Skill candidates."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Tuple

from pydantic import Field

from agentskill_eval_contracts import FrozenModel, canonical_json
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_skill_optimizer.evolution import ImprovementHypothesis
from agentskill_eval_skill_optimizer.proposal import (
    RealLLMProposalError,
    RealLLMProposalService,
)


class CandidateQualityError(RuntimeError):
    """Raised when immutable candidate materialization cannot be verified."""


class CandidateQualityDecision(FrozenModel):
    candidate_id: str = Field(min_length=1)
    accepted: bool
    skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    novelty_ratio: float = Field(ge=0, le=1)
    reasons: Tuple[str, ...] = ()
    evidence_refs: Tuple[str, ...] = ()


class MaterializedCandidate(FrozenModel):
    candidate_id: str = Field(min_length=1)
    skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_path: str = Field(min_length=1)
    hypothesis: str = Field(min_length=10)
    instruction: str = Field(min_length=10)
    evidence_refs: Tuple[str, ...] = ()
    accepted: bool
    rejection_reasons: Tuple[str, ...] = ()


class CandidateQualityReport(FrozenModel):
    schema_version: str = "ase/candidate-quality-report/v1alpha1"
    proposal_job_id: str = Field(min_length=1)
    proposal_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: Tuple[MaterializedCandidate, ...] = Field(min_length=1)
    accepted_candidate_ids: Tuple[str, ...] = ()
    rejected_candidate_ids: Tuple[str, ...] = ()
    claim_limit: str = (
        "Offline candidate materialization and quality screening only; "
        "no Agent or Skill gain claim."
    )


class CandidateQualityGate:
    """Materialize proposal hypotheses into immutable, reviewable Skill trees."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.writer = AtomicFileWriter()

    def prepare_from_proposal(
        self,
        proposal_directory: Path,
        *,
        base_skill_path: Path,
        case_tokens: Iterable[str] = (),
        max_candidates: int = 3,
        max_skill_bytes: int = 20_000,
    ) -> CandidateQualityReport:
        if max_candidates < 1:
            raise CandidateQualityError("max_candidates must be positive")
        if max_skill_bytes < 100:
            raise CandidateQualityError("max_skill_bytes is too small")
        try:
            proposal = RealLLMProposalService(proposal_directory.parent.parent).verify(
                proposal_directory
            )
        except RealLLMProposalError as exc:
            raise CandidateQualityError(str(exc)) from exc
        base_file = self._skill_file(base_skill_path)
        parent_content = base_file.read_bytes()
        parent_sha = hashlib.sha256(parent_content).hexdigest()
        if parent_sha != proposal.manifest.base_skill_sha256:
            raise CandidateQualityError("base Skill hash does not match Proposal manifest")
        hypotheses = self._load_hypotheses(proposal.proposals_path)
        return self._materialize(
            proposal_job_id=str(proposal.manifest.proposal_job_id),
            proposal_manifest_sha256=self._sha(
                (proposal.directory / "proposal-manifest.json").read_bytes()
            ),
            parent_content=parent_content,
            parent_sha=parent_sha,
            hypotheses=hypotheses,
            case_tokens=tuple(case_tokens),
            max_candidates=max_candidates,
            max_skill_bytes=max_skill_bytes,
        )

    def materialize_hypotheses(
        self,
        *,
        proposal_job_id: str,
        proposal_manifest_sha256: str,
        parent_content: bytes,
        hypotheses: Tuple[ImprovementHypothesis, ...],
        case_tokens: Tuple[str, ...] = (),
        max_candidates: int = 3,
        max_skill_bytes: int = 20_000,
    ) -> CandidateQualityReport:
        """Pure-input entry point used by unit tests and offline tooling."""

        parent_sha = self._sha(parent_content)
        return self._materialize(
            proposal_job_id=proposal_job_id,
            proposal_manifest_sha256=proposal_manifest_sha256,
            parent_content=parent_content,
            parent_sha=parent_sha,
            hypotheses=hypotheses,
            case_tokens=case_tokens,
            max_candidates=max_candidates,
            max_skill_bytes=max_skill_bytes,
        )

    def verify(self, report_path: Path) -> CandidateQualityReport:
        root = report_path.resolve(strict=True)
        report = CandidateQualityReport.model_validate_json(root.read_bytes())
        for candidate in report.candidates:
            if not candidate.accepted:
                continue
            path = (self.workspace / candidate.skill_path).resolve()
            if self.workspace not in path.parents:
                raise CandidateQualityError("candidate path escapes quality workspace")
            if not path.is_file() or self._sha(path.read_bytes()) != candidate.skill_sha256:
                raise CandidateQualityError(f"candidate hash mismatch: {candidate.candidate_id}")
        return report

    def _materialize(
        self,
        *,
        proposal_job_id: str,
        proposal_manifest_sha256: str,
        parent_content: bytes,
        parent_sha: str,
        hypotheses: Tuple[ImprovementHypothesis, ...],
        case_tokens: Tuple[str, ...],
        max_candidates: int,
        max_skill_bytes: int,
    ) -> CandidateQualityReport:
        if len(hypotheses) < 3:
            raise CandidateQualityError("at least three hypotheses are required")
        root = self.workspace / "candidate-skills"
        root.mkdir(parents=True, exist_ok=True)
        base_text = parent_content.decode("utf-8")
        base_tokens = self._tokens(base_text)
        seen_instructions: list[set[str]] = []
        records = []
        for hypothesis in hypotheses:
            instruction_tokens = self._tokens(hypothesis.instruction)
            overlap = self._overlap(base_tokens, instruction_tokens)
            reasons = list(self._static_reasons(hypothesis, case_tokens, overlap))
            if any(
                self._overlap(previous, instruction_tokens) >= 0.8
                for previous in seen_instructions
            ):
                reasons.append("duplicate_or_near_duplicate_candidate")
            seen_instructions.append(instruction_tokens)
            content = self._candidate_content(base_text, hypothesis)
            if len(content.encode("utf-8")) > max_skill_bytes:
                reasons.append("skill_size_limit_exceeded")
            digest = self._sha(content.encode("utf-8"))
            candidate_root = root / hypothesis.id
            skill_file = candidate_root / "SKILL.md"
            metadata_file = candidate_root / "metadata.yaml"
            if not reasons:
                candidate_root.mkdir(parents=True, exist_ok=True)
                self.writer.write(skill_file, content.encode("utf-8"))
                metadata = (
                    "name: optimizer-candidate-"
                    + hypothesis.id
                    + "\nversion: candidate\n"
                    + f"skill_md_sha256: {digest}\n"
                    + "license: evaluation-only\n"
                ).encode()
                self.writer.write(metadata_file, metadata)
            records.append(
                MaterializedCandidate(
                    candidate_id=hypothesis.id,
                    skill_sha256=digest,
                    parent_skill_sha256=parent_sha,
                    skill_path=str(skill_file.relative_to(self.workspace)),
                    hypothesis=hypothesis.hypothesis,
                    instruction=hypothesis.instruction,
                    evidence_refs=hypothesis.evidence_refs,
                    accepted=not reasons,
                    rejection_reasons=tuple(reasons),
                )
            )
        accepted = tuple(item.candidate_id for item in records if item.accepted)[:max_candidates]
        accepted_set = set(accepted)
        record_tuple = tuple(
            item
            if item.accepted and item.candidate_id in accepted_set
            else item.model_copy(
                update={
                    "accepted": False,
                    "rejection_reasons": item.rejection_reasons
                    if item.rejection_reasons
                    else ("candidate_limit_exceeded",),
                }
            )
            for item in records
        )
        report = CandidateQualityReport(
            proposal_job_id=proposal_job_id,
            proposal_manifest_sha256=proposal_manifest_sha256,
            parent_skill_sha256=parent_sha,
            candidates=record_tuple,
            accepted_candidate_ids=tuple(
                item.candidate_id for item in record_tuple if item.accepted
            ),
            rejected_candidate_ids=tuple(
                item.candidate_id for item in record_tuple if not item.accepted
            ),
        )
        report_path = self.workspace / "candidate-quality-report.json"
        if report_path.exists():
            existing = CandidateQualityReport.model_validate_json(report_path.read_bytes())
            if existing != report:
                raise CandidateQualityError("quality report already exists with different content")
        else:
            self.writer.write(report_path, canonical_json(report.model_dump(mode="json")) + b"\n")
        return report

    @staticmethod
    def _candidate_content(base_text: str, hypothesis: ImprovementHypothesis) -> str:
        return (
            base_text.rstrip()
            + "\n\n## Candidate guidance\n\n"
            + f"<!-- proposal-hypothesis:{hypothesis.id} -->\n"
            + f"- {hypothesis.instruction.strip()}\n"
        )

    @staticmethod
    def _static_reasons(
        hypothesis: ImprovementHypothesis,
        case_tokens: Tuple[str, ...],
        overlap: float,
    ) -> Tuple[str, ...]:
        reasons = []
        lowered = hypothesis.instruction.lower()
        if overlap >= 0.8:
            reasons.append("near_duplicate_of_parent_skill")
        if any(token and token.lower() in lowered for token in case_tokens):
            reasons.append("benchmark_or_case_leakage")
        if any(
            marker in lowered
            for marker in (
                "tool wrapper",
                "wrapper that",
                "implement a helper",
                "wrap edit",
                "a helper",
            )
        ):
            reasons.append("requires_unprovided_tooling")
        if not any(
            word in lowered
            for word in (
                "inspect",
                "verify",
                "check",
                "read",
                "run",
                "execute",
                "test",
                "parse",
                "validate",
                "clear",
                "confirm",
                "retry",
                "use",
                "identify",
                "map",
                "translate",
                "normalize",
            )
        ):
            reasons.append("not_actionable_guidance")
        return tuple(reasons)

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]{3,}", value.lower()))

    @staticmethod
    def _overlap(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, min(len(left), len(right)))

    @staticmethod
    def _load_hypotheses(path: Path) -> Tuple[ImprovementHypothesis, ...]:
        try:
            from agentskill_eval_skill_optimizer.evolution import HypothesisArtifact

            artifact = HypothesisArtifact.model_validate_json(path.read_bytes())
            return artifact.hypotheses
        except (OSError, ValueError) as exc:
            raise CandidateQualityError(f"invalid proposal hypotheses: {path}") from exc

    @staticmethod
    def _skill_file(path: Path) -> Path:
        resolved = path.resolve(strict=True)
        candidate = resolved / "SKILL.md" if resolved.is_dir() else resolved
        if candidate.name != "SKILL.md" or not candidate.is_file() or candidate.is_symlink():
            raise CandidateQualityError("base Skill must be a regular SKILL.md")
        return candidate

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()
