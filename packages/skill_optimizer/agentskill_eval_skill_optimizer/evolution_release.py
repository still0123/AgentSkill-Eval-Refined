"""Offline Stage 5A.2 evidence release built from frozen promotion artifacts."""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Mapping, Optional, Sequence
from uuid import uuid4

import yaml
from pydantic import Field, model_validator

from agentskill_eval_contracts import (
    CandidateEvaluation,
    FinalEvaluationReport,
    FinalEvaluationStage,
    FrozenModel,
    HumanReviewRecord,
    PromotionEvidenceRef,
    PromotionReleaseManifest,
    SkillVersionManifest,
    stable_sha256,
)
from agentskill_eval_skill_optimizer.evolution import EvolutionReport
from agentskill_eval_skill_optimizer.release_evidence import (
    EvidenceReleaseError,
    EvidenceReleasePreparer,
)


class EvolutionReleaseError(EvidenceReleaseError):
    """Raised when a Stage 5A.2 release is unsafe or internally inconsistent."""


class EvolutionReleaseConfig(FrozenModel):
    schema_version: Literal["ase/evolution-evidence-release-config/v1alpha1"]
    evidence_root: Path
    promotion_release_manifest: Path
    v1_manifest: Path
    v2_manifest: Path
    confirmation_report: Path
    locked_test_report: Path
    human_review: Path
    evolution_report: Path
    search_report: Path
    skill_diff: Path
    evidence_class: Literal["simulated", "observed_agent"] = "simulated"
    simulated: bool = True
    claim_limit: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def evidence_boundary_is_consistent(self) -> "EvolutionReleaseConfig":
        expected_simulated = self.evidence_class == "simulated"
        if self.simulated != expected_simulated:
            raise ValueError("evidence_class and simulated flag disagree")
        return self

    @classmethod
    def load(cls, path: Path) -> "EvolutionReleaseConfig":
        source = path.resolve(strict=True)
        try:
            value = yaml.safe_load(source.read_text(encoding="utf-8"))
            config = cls.model_validate(value)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise EvolutionReleaseError(f"invalid evolution release config: {exc}") from exc
        root = config.evidence_root
        if not root.is_absolute():
            root = source.parent / root
        if root.is_symlink():
            raise EvolutionReleaseError("evidence_root must not be a symbolic link")
        return config.model_copy(update={"evidence_root": root.resolve(strict=True)})


@dataclass(frozen=True)
class EvolutionReleaseResult:
    release_dir: Path
    manifest_path: Path
    report_json: Path
    report_html: Path
    evidence_index: Path
    audit_bundle: Path
    manifest_sha256: str
    evidence_class: str
    simulated: bool
    idempotent_replay: bool = False


@dataclass(frozen=True)
class _Source:
    role: str
    path: Path
    source_sha256: str
    archive_path: str
    bundle_content: bytes


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class EvolutionEvidenceReleasePreparer(EvidenceReleasePreparer):
    """Create and verify one deterministic ``evolution-release`` directory."""

    schema_version = "ase/evolution-evidence-release/v1alpha1"
    index_schema_version = "ase/evolution-evidence-index/v1alpha1"
    report_schema_version = "ase/evolution-evidence-report/v1alpha1"
    generator_version = "evolution-evidence-release/v1"

    _OUTPUT_FILES = {
        "evolution-report.json",
        "evolution-report.html",
        "skill-diff.patch",
        "evidence-index.json",
        "audit-bundle.tar",
        "README.md",
    }

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.workspace = workspace.resolve()
        self.release_dir = self.workspace / "evolution-release"

    def prepare(self, config: EvolutionReleaseConfig) -> EvolutionReleaseResult:  # type: ignore[override]
        loaded = self._load_inputs(config)
        sources = loaded["sources"]
        assert isinstance(sources, tuple)
        fingerprint = self._input_fingerprint(sources, config)
        if self.release_dir.exists():
            manifest = self.verify(self.release_dir)
            if manifest.get("input_fingerprint") != fingerprint:
                raise EvolutionReleaseError(
                    "evolution-release already exists for different frozen inputs"
                )
            return self._result(manifest, idempotent=True)

        report = self._build_report(config, loaded)
        evidence_index = self._build_index(sources, fingerprint)
        report_bytes = _json_bytes(report)
        html_bytes = self._html(report).encode("utf-8")
        diff_bytes = loaded["diff_bytes"]
        assert isinstance(diff_bytes, bytes)
        index_bytes = _json_bytes(evidence_index)
        readme_bytes = self._readme(report).encode("utf-8")
        audit_bytes = self._audit_tar(sources)
        output = {
            "evolution-report.json": report_bytes,
            "evolution-report.html": html_bytes,
            "skill-diff.patch": diff_bytes,
            "evidence-index.json": index_bytes,
            "audit-bundle.tar": audit_bytes,
            "README.md": readme_bytes,
        }
        file_entries = self._entries(output)
        promotion = loaded["promotion"]
        v1 = loaded["v1"]
        v2 = loaded["v2"]
        assert isinstance(promotion, PromotionReleaseManifest)
        assert isinstance(v1, dict)
        assert isinstance(v2, SkillVersionManifest)
        manifest = {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "input_fingerprint": fingerprint,
            "workflow_id": str(promotion.workflow_id),
            "promotion_id": str(promotion.promotion_id),
            "evolution_id": str(promotion.evolution_id),
            "optimization_job_id": str(promotion.optimization_job_id),
            "skill_name": v2.skill_name,
            "parent_version": self._field(v1, "version"),
            "version": v2.version,
            "parent_content_sha256": v2.parent_content_sha256,
            "content_sha256": v2.content_sha256,
            "diff_sha256": v2.diff_sha256,
            "evidence_class": config.evidence_class,
            "simulated": config.simulated,
            "claim_limit": report["claim_limit"],
            "files": file_entries,
        }
        manifest_bytes = _json_bytes(manifest)
        digest = _sha256(manifest_bytes)
        staging = self.workspace / f".tmp-{uuid4()}-evolution-release"
        self.workspace.mkdir(parents=True, exist_ok=True)
        try:
            staging.mkdir(mode=0o700)
            for name, content in output.items():
                self._write(staging / name, content)
            self._write(staging / "release-manifest.json", manifest_bytes)
            self._write(staging / "release-manifest.sha256", f"{digest}\n".encode())
            _fsync_directory(staging)
            os.rename(staging, self.release_dir)
            _fsync_directory(self.workspace)
        except FileExistsError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            existing = self.verify(self.release_dir)
            if existing.get("input_fingerprint") != fingerprint:
                raise EvolutionReleaseError(
                    "concurrent prepare published different frozen inputs"
                ) from exc
            return self._result(existing, idempotent=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        verified = self.verify(self.release_dir)
        return self._result(verified, idempotent=False)

    def verify(self, release_dir: Path) -> Mapping[str, object]:
        release = release_dir.resolve(strict=True)
        if release.name != "evolution-release":
            raise EvolutionReleaseError("release directory must be named evolution-release")
        members = tuple(release.iterdir())
        if any(path.is_symlink() for path in members):
            raise EvolutionReleaseError("release must not contain symbolic links")
        expected_members = self._OUTPUT_FILES | {
            "release-manifest.json",
            "release-manifest.sha256",
        }
        if {path.name for path in members} != expected_members or any(
            not path.is_file() for path in members
        ):
            raise EvolutionReleaseError("release member set mismatch")
        manifest_bytes = (release / "release-manifest.json").read_bytes()
        sidecar = (release / "release-manifest.sha256").read_text(encoding="utf-8").strip()
        if len(sidecar) != 64 or _sha256(manifest_bytes) != sidecar:
            raise EvolutionReleaseError("release manifest hash mismatch")
        manifest = self._decode_json(manifest_bytes, "evolution release manifest")
        if manifest.get("schema_version") != self.schema_version:
            raise EvolutionReleaseError("unsupported evolution release schema")
        declared = manifest.get("files")
        if not isinstance(declared, list):
            raise EvolutionReleaseError("release manifest files must be a list")
        by_name: Dict[str, Mapping[str, object]] = {}
        for entry in declared:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise EvolutionReleaseError("invalid release file entry")
            name = str(entry["path"])
            if name in by_name:
                raise EvolutionReleaseError(f"duplicate release file: {name}")
            by_name[name] = entry
        if set(by_name) != self._OUTPUT_FILES:
            raise EvolutionReleaseError("declared release member set mismatch")
        for name, entry in by_name.items():
            content = (release / name).read_bytes()
            if entry.get("size_bytes") != len(content):
                raise EvolutionReleaseError(f"release member size mismatch: {name}")
            if entry.get("sha256") != _sha256(content):
                raise EvolutionReleaseError(f"release member hash mismatch: {name}")
        if by_name["skill-diff.patch"].get("sha256") != manifest.get("diff_sha256"):
            raise EvolutionReleaseError("published Skill diff hash mismatch")
        report = self._decode_json(
            (release / "evolution-report.json").read_bytes(), "evolution report"
        )
        index = self._decode_json((release / "evidence-index.json").read_bytes(), "evidence index")
        self._verify_report_lineage(manifest, report)
        self._verify_audit_bundle(release / "audit-bundle.tar", index)
        if index.get("input_fingerprint") != manifest.get("input_fingerprint"):
            raise EvolutionReleaseError("evidence index fingerprint mismatch")
        return manifest

    def inspect(self, release_dir: Path) -> Mapping[str, object]:
        manifest = self.verify(release_dir)
        report = self._decode_json(
            (release_dir / "evolution-report.json").read_bytes(), "evolution report"
        )
        review = report.get("human_review")
        if not isinstance(review, dict):
            raise EvolutionReleaseError("release report human review is missing")
        return {
            "valid": True,
            "release_dir": str(release_dir.resolve()),
            "manifest_sha256": _sha256((release_dir / "release-manifest.json").read_bytes()),
            "skill_name": manifest["skill_name"],
            "parent_version": manifest["parent_version"],
            "version": manifest["version"],
            "parent_content_sha256": manifest["parent_content_sha256"],
            "content_sha256": manifest["content_sha256"],
            "decision": review["decision"],
            "evidence_class": manifest["evidence_class"],
            "simulated": manifest["simulated"],
            "claim_limit": manifest["claim_limit"],
            "files": manifest["files"],
        }

    def _load_inputs(self, config: EvolutionReleaseConfig) -> Dict[str, object]:
        root = config.evidence_root.resolve(strict=True)
        paths = {
            "promotion_release": config.promotion_release_manifest,
            "skill_v1_manifest": config.v1_manifest,
            "skill_v2_manifest": config.v2_manifest,
            "confirmation_report": config.confirmation_report,
            "locked_test_report": config.locked_test_report,
            "human_review": config.human_review,
            "evolution_report": config.evolution_report,
            "search_report": config.search_report,
            "skill_diff": config.skill_diff,
        }
        source_paths = {
            role: self._source(root, path, role.replace("_", " ")) for role, path in paths.items()
        }
        raw = {role: path.read_bytes() for role, path in source_paths.items()}
        for role, content in raw.items():
            self._scan_bytes(content, role.replace("_", " "))
        self._require_sidecar(source_paths["promotion_release"], raw["promotion_release"])
        self._require_sidecar(source_paths["skill_v2_manifest"], raw["skill_v2_manifest"])
        decoded = {
            role: self._decode_json(content, role.replace("_", " "))
            for role, content in raw.items()
            if role != "skill_diff"
        }
        try:
            promotion = PromotionReleaseManifest.model_validate(decoded["promotion_release"])
            v2 = SkillVersionManifest.model_validate(decoded["skill_v2_manifest"])
            confirmation = FinalEvaluationReport.model_validate(decoded["confirmation_report"])
            locked = FinalEvaluationReport.model_validate(decoded["locked_test_report"])
            review = HumanReviewRecord.model_validate(decoded["human_review"])
            evolution = EvolutionReport.model_validate(decoded["evolution_report"])
        except ValueError as exc:
            raise EvolutionReleaseError(f"invalid typed release input: {exc}") from exc
        v1 = decoded["skill_v1_manifest"]
        search = decoded["search_report"]
        self._validate_inputs(
            config,
            promotion,
            v1,
            v2,
            confirmation,
            locked,
            review,
            evolution,
            search,
            raw,
        )
        archive_names = {
            "promotion_release": "evidence/promotion-release-manifest.json",
            "skill_v1_manifest": "evidence/skill-v1-manifest.json",
            "skill_v2_manifest": "evidence/skill-v2-manifest.json",
            "confirmation_report": "evidence/confirmation-report.json",
            "locked_test_report": "evidence/locked-test-report.json",
            "human_review": "evidence/human-review.json",
            "evolution_report": "evidence/evolution-report.json",
            "search_report": "evidence/search-report.json",
            "skill_diff": "evidence/skill-diff.patch",
        }
        sources: List[_Source] = []
        for role in paths:
            content = raw[role]
            if role == "skill_diff":
                bundle_content = content
            else:
                sanitized = self._sanitize(decoded[role], root, role.replace("_", " "))
                bundle_content = _json_bytes(sanitized)
            sources.append(
                _Source(
                    role=role,
                    path=source_paths[role],
                    source_sha256=_sha256(content),
                    archive_path=archive_names[role],
                    bundle_content=bundle_content,
                )
            )
        return {
            "promotion": promotion,
            "v1": v1,
            "v2": v2,
            "confirmation": confirmation,
            "locked": locked,
            "review": review,
            "evolution": evolution,
            "search": search,
            "diff_bytes": raw["skill_diff"],
            "sources": tuple(sources),
        }

    @staticmethod
    def _require_sidecar(path: Path, content: bytes) -> None:
        sidecar = path.with_suffix(".sha256")
        if sidecar.is_symlink() or not sidecar.is_file():
            raise EvolutionReleaseError(f"integrity sidecar is missing: {path.name}")
        expected = sidecar.read_text(encoding="utf-8").strip()
        if expected != _sha256(content):
            raise EvolutionReleaseError(f"integrity sidecar mismatch: {path.name}")

    def _validate_inputs(
        self,
        config: EvolutionReleaseConfig,
        promotion: PromotionReleaseManifest,
        v1: Mapping[str, object],
        v2: SkillVersionManifest,
        confirmation: FinalEvaluationReport,
        locked: FinalEvaluationReport,
        review: HumanReviewRecord,
        evolution: EvolutionReport,
        search: Mapping[str, object],
        raw: Mapping[str, bytes],
    ) -> None:
        if promotion.decision != "APPROVED" or promotion.human_review is None:
            raise EvolutionReleaseError("evidence release requires an approved promotion")
        if config.claim_limit is not None and config.claim_limit != promotion.claim_limit:
            raise EvolutionReleaseError("claim_limit cannot differ from the promotion release")
        if review != promotion.human_review or review.decision != "APPROVED":
            raise EvolutionReleaseError("human review does not match promotion release")
        flags = (
            promotion.simulated,
            v2.simulated_evidence,
            confirmation.job.simulated,
            locked.job.simulated,
            evolution.simulated,
        )
        if any(value != config.simulated for value in flags):
            raise EvolutionReleaseError("real and simulated evolution evidence must not be mixed")
        self._require_simulation_boundary(
            evolution.model_dump(mode="json"), config.simulated, "evolution report"
        )
        self._require_simulation_boundary(search, config.simulated, "search report")
        v1_name = self._field(v1, "skill_name")
        v1_version = self._field(v1, "version")
        del v1_version
        v1_hash = self._field(v1, "content_sha256")
        if v1.get("simulated_evidence") is not config.simulated:
            raise EvolutionReleaseError("Skill v1 evidence class does not match release config")
        if v1_name != v2.skill_name:
            raise EvolutionReleaseError("Skill v1/v2 names do not match")
        if v2.parent_content_sha256 != v1_hash:
            raise EvolutionReleaseError("Skill v2 parent hash does not match Skill v1")
        if (
            promotion.parent_skill_sha256 != v1_hash
            or promotion.winner_skill_sha256 != v2.content_sha256
        ):
            raise EvolutionReleaseError("promotion Skill hashes do not match v1/v2")
        if promotion.skill_version_manifest_sha256 != _sha256(raw["skill_v2_manifest"]):
            raise EvolutionReleaseError("Skill v2 manifest hash does not match promotion")
        diff_sha = _sha256(raw["skill_diff"])
        if diff_sha != v2.diff_sha256 or diff_sha != promotion.diff_sha256:
            raise EvolutionReleaseError("Skill diff hash does not match published manifests")
        self._validate_final_report(
            promotion.confirmation,
            confirmation,
            raw["confirmation_report"],
            FinalEvaluationStage.VALIDATION_CONFIRM,
        )
        if promotion.locked_test is None:
            raise EvolutionReleaseError("approved promotion is missing locked-test evidence")
        self._validate_final_report(
            promotion.locked_test,
            locked,
            raw["locked_test_report"],
            FinalEvaluationStage.LOCKED_TEST,
        )
        if v2.validation_confirm != promotion.confirmation or v2.locked_test != (
            promotion.locked_test
        ):
            raise EvolutionReleaseError("SkillVersion evidence differs from promotion evidence")
        if (
            evolution.evolution_id != promotion.evolution_id
            or evolution.optimization_job_id != promotion.optimization_job_id
            or evolution.winner_candidate_id != promotion.winner_candidate_id
            or evolution.winner_skill_sha256 != promotion.winner_skill_sha256
        ):
            raise EvolutionReleaseError("evolution report lineage does not match promotion")
        lineage = {item.role: item for item in promotion.lineage}
        if lineage["evolution_report"].sha256 != _sha256(raw["evolution_report"]):
            raise EvolutionReleaseError("evolution report hash does not match promotion lineage")
        if lineage["search_report"].sha256 != _sha256(raw["search_report"]):
            raise EvolutionReleaseError("search report hash does not match promotion lineage")
        job = search.get("job")
        if not isinstance(job, dict) or str(job.get("id")) != str(promotion.optimization_job_id):
            raise EvolutionReleaseError("search report optimization job does not match promotion")
        if str(search.get("winner_id")) != str(promotion.winner_candidate_id):
            raise EvolutionReleaseError("search report winner does not match promotion")

    @staticmethod
    def _validate_final_report(
        evidence: PromotionEvidenceRef,
        report: FinalEvaluationReport,
        content: bytes,
        expected_stage: FinalEvaluationStage,
    ) -> None:
        if report.job.id != evidence.final_evaluation_job_id:
            raise EvolutionReleaseError("final report job does not match promotion evidence")
        if report.job.stage != expected_stage:
            raise EvolutionReleaseError("final report stage does not match release role")
        if _sha256(content) != evidence.report_sha256:
            raise EvolutionReleaseError("final report hash does not match promotion evidence")
        if (
            report.job.base_skill_sha256 != evidence.base_skill_sha256
            or report.job.winner_skill_sha256 != evidence.winner_skill_sha256
            or report.decision != evidence.decision
            or report.job.simulated != evidence.simulated
        ):
            raise EvolutionReleaseError("final report content does not match promotion evidence")

    @staticmethod
    def _require_simulation_boundary(
        value: object, expected: bool, label: str
    ) -> None:
        flags: List[bool] = []

        def visit(child: object) -> None:
            if isinstance(child, dict):
                for key, nested in child.items():
                    if key in {"simulated", "simulated_evidence"}:
                        if not isinstance(nested, bool):
                            raise EvolutionReleaseError(f"invalid simulation flag in {label}")
                        flags.append(nested)
                    else:
                        visit(nested)
            elif isinstance(child, list):
                for nested in child:
                    visit(nested)

        visit(value)
        if not flags or any(flag != expected for flag in flags):
            raise EvolutionReleaseError(
                f"real and simulated evidence are mixed in {label}"
            )

    def _build_report(
        self, config: EvolutionReleaseConfig, loaded: Mapping[str, object]
    ) -> Dict[str, object]:
        promotion = loaded["promotion"]
        v1 = loaded["v1"]
        v2 = loaded["v2"]
        confirmation = loaded["confirmation"]
        locked = loaded["locked"]
        review = loaded["review"]
        evolution = loaded["evolution"]
        search = loaded["search"]
        assert isinstance(promotion, PromotionReleaseManifest)
        assert isinstance(v1, dict)
        assert isinstance(v2, SkillVersionManifest)
        assert isinstance(confirmation, FinalEvaluationReport)
        assert isinstance(locked, FinalEvaluationReport)
        assert isinstance(review, HumanReviewRecord)
        assert isinstance(evolution, EvolutionReport)
        assert isinstance(search, dict)
        claim = config.claim_limit or promotion.claim_limit
        self._scan_text(claim, "claim_limit")
        confirmation_summary = self._final_summary(confirmation)
        locked_summary = self._final_summary(locked)
        aggregate = self._aggregate_final(confirmation, locked)
        candidates = search.get("candidates")
        validation = self._validation_summary(candidates, v1, v2)
        regression = {
            "passed": evolution.regression_gate.passed,
            "loss_cases": list(evolution.regression_gate.loss_cases),
            "invalid_cases": list(evolution.regression_gate.invalid_cases),
            "token_overhead_ratio": evolution.regression_gate.token_overhead_ratio,
            "base": self._candidate_summary(evolution.regression_gate.base),
            "winner": self._candidate_summary(evolution.regression_gate.winner),
        }
        proposal_lineage = [
            {
                "id": item.id,
                "failure_label": item.failure_label.value,
                "hypothesis": item.hypothesis,
                "instruction": item.instruction,
                "evidence_refs": list(item.evidence_refs),
                "risks": list(item.risks),
            }
            for item in evolution.hypotheses
        ]
        failure_lineage = [
            {
                "failure_label": item.failure_label.value,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in evolution.hypotheses
        ]
        return {
            "schema_version": self.report_schema_version,
            "promotion": {
                "workflow_id": str(promotion.workflow_id),
                "promotion_id": str(promotion.promotion_id),
                "decision": promotion.decision,
                "lineage_sha256": promotion.lineage_sha256,
                "lineage": [item.model_dump(mode="json") for item in promotion.lineage],
            },
            "skill_versions": {
                "v1": {
                    "skill_name": self._field(v1, "skill_name"),
                    "version": self._field(v1, "version"),
                    "content_sha256": self._field(v1, "content_sha256"),
                },
                "v2": {
                    "skill_name": v2.skill_name,
                    "version": v2.version,
                    "parent_content_sha256": v2.parent_content_sha256,
                    "content_sha256": v2.content_sha256,
                    "diff_sha256": v2.diff_sha256,
                },
            },
            "proposal_lineage": proposal_lineage,
            "failure_lineage": failure_lineage,
            "stages": {
                "validation_search": validation,
                "regression_dev": regression,
                "validation_confirm": confirmation_summary,
                "locked_test": locked_summary,
            },
            "v1_v2_aggregate": aggregate,
            "human_review": review.model_dump(mode="json"),
            "evidence_class": config.evidence_class,
            "simulated": config.simulated,
            "claim_limit": claim,
        }

    @staticmethod
    def _candidate_summary(evaluation: CandidateEvaluation) -> Dict[str, object]:
        return {
            "pass_rate": evaluation.pass_rate,
            "mean_score": evaluation.mean_score,
            "tokens": evaluation.total_tokens,
            "latency_ms": evaluation.total_latency_ms,
            "cost_microusd": evaluation.total_cost_microusd,
        }

    def _validation_summary(
        self, candidates: object, v1: Mapping[str, object], v2: SkillVersionManifest
    ) -> Mapping[str, object]:
        rows = candidates if isinstance(candidates, list) else []

        def matching(content_hash: str) -> Mapping[str, object]:
            row = next(
                (
                    item
                    for item in rows
                    if isinstance(item, dict) and item.get("content_sha256") == content_hash
                ),
                None,
            )
            if not isinstance(row, dict):
                raise EvolutionReleaseError("search report is missing v1/v2 validation rows")
            return {
                "pass_rate": row.get("full_pass_rate"),
                "mean_score": row.get("full_mean_score"),
                "tokens": row.get("full_tokens"),
                "latency_ms": row.get("full_latency_ms"),
                "cost_microusd": row.get("full_cost_microusd"),
            }

        return {
            "v1": matching(self._field(v1, "content_sha256")),
            "v2": matching(v2.content_sha256),
        }

    def _final_summary(self, report: FinalEvaluationReport) -> Dict[str, object]:
        return {
            "decision": report.decision.value,
            "base_pass_rate": report.base_pass_rate,
            "winner_pass_rate": report.winner_pass_rate,
            "absolute_gain": report.absolute_gain,
            "win": report.win_count,
            "tie": report.tie_positive_count + report.tie_negative_count,
            "tie_positive": report.tie_positive_count,
            "tie_negative": report.tie_negative_count,
            "loss": report.loss_count,
            "base": self._totals(report.base_evaluations),
            "winner": self._totals(report.winner_evaluations),
            "claim_limit": report.claim_limit,
        }

    @staticmethod
    def _totals(evaluations: Sequence[CandidateEvaluation]) -> Dict[str, object]:
        typed = list(evaluations)
        costs = [item.total_cost_microusd for item in typed]
        return {
            "tokens": sum(item.total_tokens for item in typed),
            "latency_ms": sum(item.total_latency_ms for item in typed),
            "cost_microusd": (
                None
                if any(value is None for value in costs)
                else sum(value for value in costs if value is not None)
            ),
        }

    def _aggregate_final(
        self, confirmation: FinalEvaluationReport, locked: FinalEvaluationReport
    ) -> Mapping[str, object]:
        base = self._totals((*confirmation.base_evaluations, *locked.base_evaluations))
        winner = self._totals((*confirmation.winner_evaluations, *locked.winner_evaluations))
        return {
            "win": confirmation.win_count + locked.win_count,
            "tie": (
                confirmation.tie_positive_count
                + confirmation.tie_negative_count
                + locked.tie_positive_count
                + locked.tie_negative_count
            ),
            "loss": confirmation.loss_count + locked.loss_count,
            "base": base,
            "winner": winner,
        }

    def _build_index(self, sources: Sequence[_Source], fingerprint: str) -> Dict[str, object]:
        return {
            "schema_version": self.index_schema_version,
            "input_fingerprint": fingerprint,
            "artifacts": [
                {
                    "role": item.role,
                    "archive_path": item.archive_path,
                    "source_sha256": item.source_sha256,
                    "bundle_sha256": _sha256(item.bundle_content),
                    "size_bytes": len(item.bundle_content),
                }
                for item in sources
            ],
        }

    @staticmethod
    def _input_fingerprint(sources: Sequence[_Source], config: EvolutionReleaseConfig) -> str:
        return stable_sha256(
            {
                "schema_version": config.schema_version,
                "evidence_class": config.evidence_class,
                "simulated": config.simulated,
                "claim_limit": config.claim_limit,
                "sources": [{"role": item.role, "sha256": item.source_sha256} for item in sources],
            }
        )

    @staticmethod
    def _entries(files: Mapping[str, bytes]) -> List[Dict[str, object]]:
        return [
            {"path": name, "sha256": _sha256(content), "size_bytes": len(content)}
            for name, content in sorted(files.items())
        ]

    @staticmethod
    def _audit_tar(sources: Sequence[_Source]) -> bytes:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for source in sorted(sources, key=lambda item: item.archive_path):
                info = tarfile.TarInfo(source.archive_path)
                info.size = len(source.bundle_content)
                info.mode = 0o600
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                archive.addfile(info, io.BytesIO(source.bundle_content))
        return stream.getvalue()

    def _verify_audit_bundle(self, path: Path, index: Mapping[str, object]) -> None:
        artifacts = index.get("artifacts")
        if not isinstance(artifacts, list):
            raise EvolutionReleaseError("evidence index artifacts must be a list")
        declared: Dict[str, Mapping[str, object]] = {}
        roles = set()
        for item in artifacts:
            if not isinstance(item, dict) or not isinstance(item.get("archive_path"), str):
                raise EvolutionReleaseError("invalid evidence index artifact")
            archive_path = str(item["archive_path"])
            self._safe_relative(archive_path)
            if archive_path in declared:
                raise EvolutionReleaseError(f"duplicate audit member: {archive_path}")
            role = item.get("role")
            if not isinstance(role, str) or role in roles:
                raise EvolutionReleaseError("invalid or duplicate evidence role")
            roles.add(role)
            declared[archive_path] = item
        expected_roles = {
            "promotion_release",
            "skill_v1_manifest",
            "skill_v2_manifest",
            "confirmation_report",
            "locked_test_report",
            "human_review",
            "evolution_report",
            "search_report",
            "skill_diff",
        }
        if roles != expected_roles:
            raise EvolutionReleaseError("audit bundle evidence roles are incomplete")
        try:
            with tarfile.open(path, mode="r:") as archive:
                members = archive.getmembers()
                names = [member.name for member in members]
                if len(names) != len(set(names)) or set(names) != set(declared):
                    raise EvolutionReleaseError("audit bundle member set mismatch")
                for member in members:
                    self._safe_relative(member.name)
                    if not member.isfile():
                        raise EvolutionReleaseError("audit bundle contains non-file member")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise EvolutionReleaseError("audit bundle member cannot be read")
                    content = extracted.read()
                    entry = declared[member.name]
                    if entry.get("size_bytes") != len(content):
                        raise EvolutionReleaseError(
                            f"audit bundle member size mismatch: {member.name}"
                        )
                    if entry.get("bundle_sha256") != _sha256(content):
                        raise EvolutionReleaseError(
                            f"audit bundle member hash mismatch: {member.name}"
                        )
        except (tarfile.TarError, OSError) as exc:
            raise EvolutionReleaseError(f"invalid audit bundle: {exc}") from exc

    @staticmethod
    def _verify_report_lineage(
        manifest: Mapping[str, object], report: Mapping[str, object]
    ) -> None:
        versions = report.get("skill_versions")
        if not isinstance(versions, dict):
            raise EvolutionReleaseError("report SkillVersion lineage is missing")
        v1 = versions.get("v1")
        v2 = versions.get("v2")
        if not isinstance(v1, dict) or not isinstance(v2, dict):
            raise EvolutionReleaseError("report SkillVersion entries are invalid")
        if (
            v2.get("parent_content_sha256") != v1.get("content_sha256")
            or manifest.get("parent_content_sha256") != v1.get("content_sha256")
            or manifest.get("content_sha256") != v2.get("content_sha256")
            or manifest.get("diff_sha256") != v2.get("diff_sha256")
        ):
            raise EvolutionReleaseError("release SkillVersion parent lineage mismatch")
        simulated = manifest.get("simulated")
        if not isinstance(simulated, bool) or report.get("simulated") is not simulated:
            raise EvolutionReleaseError("report and manifest simulation flags differ")
        expected_class = "simulated" if simulated else "observed_agent"
        if (
            report.get("evidence_class") != expected_class
            or manifest.get("evidence_class") != expected_class
        ):
            raise EvolutionReleaseError("report and manifest evidence classes differ")

    @staticmethod
    def _html(report: Mapping[str, object]) -> str:
        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        versions = report["skill_versions"]
        stages = report["stages"]
        review = report["human_review"]
        aggregate = report["v1_v2_aggregate"]
        assert isinstance(versions, dict)
        assert isinstance(stages, dict)
        assert isinstance(review, dict)
        assert isinstance(aggregate, dict)
        v1 = versions["v1"]
        v2 = versions["v2"]
        assert isinstance(v1, dict) and isinstance(v2, dict)
        rows = []
        for name in ("validation_search", "regression_dev", "validation_confirm", "locked_test"):
            value = stages[name]
            rendered = esc(json.dumps(value, sort_keys=True))
            rows.append(f"<tr><th>{esc(name)}</th><td><pre>{rendered}</pre></td></tr>")
        simulated = report.get("simulated") is True
        badge = "SIMULATED / FIXTURE EVIDENCE" if simulated else "OBSERVED AGENT EVIDENCE"
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
            "style-src 'unsafe-inline'\"><title>Evolution Evidence Release</title>"
            "<style>body{font:14px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #bbb;padding:.5rem;"
            "text-align:left;vertical-align:top}pre{white-space:pre-wrap;word-break:break-word}"
            ".badge{padding:.2rem .5rem;background:#ffe9a8}</style></head><body>"
            f"<h1>Evolution Evidence Release</h1><p class='badge'>{esc(badge)}</p>"
            f"<p><strong>Skill:</strong> {esc(v2['skill_name'])} · {esc(v1['version'])} → "
            f"{esc(v2['version'])}</p><p><strong>v1:</strong> {esc(v1['content_sha256'])}<br>"
            f"<strong>v2:</strong> {esc(v2['content_sha256'])}<br><strong>Parent:</strong> "
            f"{esc(v2['parent_content_sha256'])}</p><h2>Evaluation stages</h2><table>"
            + "".join(rows)
            + "</table><h2>Aggregate W/T/L and cost</h2>"
            f"<pre>{esc(json.dumps(aggregate, sort_keys=True))}</pre><h2>Human review</h2>"
            f"<p>{esc(review.get('decision'))} — {esc(review.get('reviewer'))}: "
            f"{esc(review.get('reason'))}</p><h2>Claim limit</h2>"
            f"<p>{esc(report['claim_limit'])}</p></body></html>"
        )

    @staticmethod
    def _readme(report: Mapping[str, object]) -> str:
        versions = report["skill_versions"]
        assert isinstance(versions, dict)
        v1 = versions["v1"]
        v2 = versions["v2"]
        assert isinstance(v1, dict) and isinstance(v2, dict)
        claim = str(report["claim_limit"]).replace("\n", " ")
        evidence_class = str(report["evidence_class"])
        return (
            "# Evolution Evidence Release\n\n"
            "This directory is a deterministic, offline-verifiable Stage 5A.2 evidence bundle.\n\n"
            f"- Skill: `{v2['skill_name']}`\n"
            f"- Versions: `{v1['version']}` → `{v2['version']}`\n"
            f"- Evidence class: `{evidence_class}`\n"
            "- Release preparation itself does not invoke a model or Agent.\n\n"
            f"Claim limit: {claim}\n\n"
            "Run `agentskill-eval evolution release verify evolution-release` before use.\n"
        )

    def _result(
        self, manifest: Mapping[str, object], *, idempotent: bool
    ) -> EvolutionReleaseResult:
        evidence_class = manifest.get("evidence_class")
        simulated = manifest.get("simulated")
        if not isinstance(evidence_class, str) or not isinstance(simulated, bool):
            raise EvolutionReleaseError("release manifest evidence boundary is invalid")
        return EvolutionReleaseResult(
            release_dir=self.release_dir,
            manifest_path=self.release_dir / "release-manifest.json",
            report_json=self.release_dir / "evolution-report.json",
            report_html=self.release_dir / "evolution-report.html",
            evidence_index=self.release_dir / "evidence-index.json",
            audit_bundle=self.release_dir / "audit-bundle.tar",
            manifest_sha256=_sha256((self.release_dir / "release-manifest.json").read_bytes()),
            evidence_class=evidence_class,
            simulated=simulated,
            idempotent_replay=idempotent,
        )
