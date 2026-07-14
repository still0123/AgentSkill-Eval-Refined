"""Prepare immutable, redacted evidence releases from frozen fixture inputs.

This module deliberately does not run an Agent or an evaluator.  It is a publication
boundary: all inputs must already exist, carry expected hashes, and belong to one
uniform evidence class before any release bytes are written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple, Union
from uuid import uuid4

JsonScalar = Union[None, bool, int, float, str]
JsonValue = Union[JsonScalar, Sequence["JsonValue"], Mapping[str, "JsonValue"]]

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEYS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "password",
    "authorization",
    "clientsecret",
    "privatekey",
    "credential",
}
_SECRET_VALUES = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class EvidenceReleaseError(RuntimeError):
    """Raised when evidence cannot be safely or immutably released."""


@dataclass(frozen=True)
class ReleaseArtifact:
    """One already-produced audit artifact to include in the release."""

    source: Path
    bundle_path: str
    sha256: str


@dataclass(frozen=True)
class EvidenceReleaseSpec:
    """Frozen inputs for one evidence release."""

    release_id: str
    evidence_root: Path
    report_path: Path
    v1_manifest_path: Path
    v2_manifest_path: Path
    artifacts: Tuple[ReleaseArtifact, ...] = ()
    expected_simulated: bool = True
    claim_limit: str = (
        "Fixture/simulated evidence validates the release workflow only; it is not real "
        "Agent performance evidence."
    )


@dataclass(frozen=True)
class EvidenceReleaseResult:
    release_dir: Path
    manifest_path: Path
    report_path: Path
    comparison_path: Path
    artifact_manifest_path: Path
    release_sha256: str


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256(encoded)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


class EvidenceReleasePreparer:
    """Validate and publish an offline evidence directory exactly once."""

    schema_version = "ase/evidence-release/v1alpha1"
    sanitizer_version = "evidence-release-sanitizer/v1"

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()

    def prepare(self, spec: EvidenceReleaseSpec) -> EvidenceReleaseResult:
        """Validate every input, then atomically expose an immutable release directory."""
        self._safe_name(spec.release_id, "release_id")
        if not spec.claim_limit.strip():
            raise EvidenceReleaseError("claim_limit must not be empty")
        self._scan_text(spec.claim_limit, "claim_limit")

        evidence_root = spec.evidence_root.resolve(strict=True)
        report_source = self._source(evidence_root, spec.report_path, "report")
        v1_source = self._source(evidence_root, spec.v1_manifest_path, "v1 manifest")
        v2_source = self._source(evidence_root, spec.v2_manifest_path, "v2 manifest")
        v1 = self._load_json(v1_source, "v1 manifest")
        v2 = self._load_json(v2_source, "v2 manifest")
        report = self._load_json(report_source, "experiment report")
        self._validate_lineage(v1, v2)

        classes = self._evidence_classes((v1, v2, report))
        expected_class = "simulated" if spec.expected_simulated else "real"
        if not classes:
            raise EvidenceReleaseError("evidence class is unavailable")
        if len(classes) != 1:
            raise EvidenceReleaseError("real and simulated evidence must not be mixed")
        actual_class = next(iter(classes))
        if actual_class != expected_class:
            raise EvidenceReleaseError(f"expected {expected_class} evidence, found {actual_class}")

        sanitized_report = self._sanitize(report, evidence_root, "experiment report")
        sanitized_v1 = self._sanitize(v1, evidence_root, "v1 manifest")
        sanitized_v2 = self._sanitize(v2, evidence_root, "v2 manifest")
        checked_artifacts = self._validate_artifacts(evidence_root, spec.artifacts)

        releases = self.output_root / "releases"
        target = releases / spec.release_id
        if target.exists():
            raise EvidenceReleaseError(f"immutable release already exists: {spec.release_id}")
        releases.mkdir(parents=True, exist_ok=True)
        staging = releases / f".tmp-{uuid4()}-{spec.release_id}"
        try:
            staging.mkdir(mode=0o700)
            self._write(
                staging / "reports" / "experiment-report.json",
                _json_bytes(sanitized_report),
            )
            self._write(staging / "skill-versions" / "v1-manifest.json", _json_bytes(sanitized_v1))
            self._write(staging / "skill-versions" / "v2-manifest.json", _json_bytes(sanitized_v2))
            comparison = self._comparison(v1, v2, spec.claim_limit, actual_class)
            self._write(staging / "comparison" / "v1-v2.md", comparison.encode("utf-8"))

            artifact_entries = []
            for artifact, content in checked_artifacts:
                destination = staging / "audit" / "artifacts" / artifact.bundle_path
                self._write(destination, content)
                artifact_entries.append(
                    {
                        "path": f"audit/artifacts/{artifact.bundle_path}",
                        "sha256": artifact.sha256,
                        "size_bytes": len(content),
                    }
                )
            artifact_manifest = {
                "schema_version": "ase/evidence-artifact-manifest/v1alpha1",
                "artifacts": artifact_entries,
            }
            self._write(
                staging / "audit" / "artifact-manifest.json", _json_bytes(artifact_manifest)
            )

            files = self._file_entries(staging)
            release_manifest = {
                "schema_version": self.schema_version,
                "release_id": spec.release_id,
                "evidence_class": actual_class,
                "simulated": spec.expected_simulated,
                "claim_limit": spec.claim_limit,
                "sanitizer_version": self.sanitizer_version,
                "skill_name": self._field(v2, "skill_name"),
                "parent_version": self._field(v1, "version"),
                "version": self._field(v2, "version"),
                "parent_content_sha256": self._field(v1, "content_sha256"),
                "content_sha256": self._field(v2, "content_sha256"),
                "files": files,
            }
            manifest_bytes = _json_bytes(release_manifest)
            self._write(staging / "release-manifest.json", manifest_bytes)
            manifest_digest = _sha256(manifest_bytes)
            self._write(staging / "release-manifest.sha256", f"{manifest_digest}\n".encode())
            _fsync_directory(staging)
            os.rename(staging, target)
            _fsync_directory(releases)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.verify(target)
        return EvidenceReleaseResult(
            release_dir=target,
            manifest_path=target / "release-manifest.json",
            report_path=target / "reports" / "experiment-report.json",
            comparison_path=target / "comparison" / "v1-v2.md",
            artifact_manifest_path=target / "audit" / "artifact-manifest.json",
            release_sha256=manifest_digest,
        )

    def verify(self, release_dir: Path) -> Mapping[str, object]:
        """Verify the release sidecar, member set, sizes, hashes, and safe paths."""
        release = release_dir.resolve(strict=True)
        manifest_path = release / "release-manifest.json"
        sidecar_path = release / "release-manifest.sha256"
        if not manifest_path.is_file() or not sidecar_path.is_file():
            raise EvidenceReleaseError("release manifest or digest sidecar is missing")
        manifest_bytes = manifest_path.read_bytes()
        expected_manifest_sha = sidecar_path.read_text(encoding="utf-8").strip()
        if not _HEX_DIGEST.fullmatch(expected_manifest_sha):
            raise EvidenceReleaseError("release manifest digest is invalid")
        if _sha256(manifest_bytes) != expected_manifest_sha:
            raise EvidenceReleaseError("release manifest hash mismatch")
        manifest = self._decode_json(manifest_bytes, "release manifest")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise EvidenceReleaseError("release manifest files must be a list")
        declared: Dict[str, Mapping[str, object]] = {}
        for entry in files:
            if not isinstance(entry, dict):
                raise EvidenceReleaseError("release manifest file entry must be an object")
            relative = entry.get("path")
            if not isinstance(relative, str):
                raise EvidenceReleaseError("release file path is missing")
            self._safe_relative(relative)
            if relative in declared:
                raise EvidenceReleaseError(f"duplicate release file: {relative}")
            declared[relative] = entry
        members = tuple(release.rglob("*"))
        if any(path.is_symlink() for path in members):
            raise EvidenceReleaseError("release must not contain symbolic links")
        actual = {
            path.relative_to(release).as_posix()
            for path in members
            if path.is_file()
            and path.name not in {"release-manifest.json", "release-manifest.sha256"}
        }
        if actual != set(declared):
            raise EvidenceReleaseError("release member set mismatch")
        for relative, entry in declared.items():
            target = release / relative
            if target.is_symlink() or not target.is_file():
                raise EvidenceReleaseError(f"release member is not a regular file: {relative}")
            content = target.read_bytes()
            if entry.get("size_bytes") != len(content):
                raise EvidenceReleaseError(f"release member size mismatch: {relative}")
            if entry.get("sha256") != _sha256(content):
                raise EvidenceReleaseError(f"release member hash mismatch: {relative}")
        self._verify_artifact_manifest(release)
        return manifest

    def _validate_artifacts(
        self, evidence_root: Path, artifacts: Sequence[ReleaseArtifact]
    ) -> Tuple[Tuple[ReleaseArtifact, bytes], ...]:
        checked = []
        names = set()
        for artifact in artifacts:
            self._safe_relative(artifact.bundle_path)
            if artifact.bundle_path in names:
                raise EvidenceReleaseError(f"duplicate artifact path: {artifact.bundle_path}")
            names.add(artifact.bundle_path)
            if not _HEX_DIGEST.fullmatch(artifact.sha256):
                raise EvidenceReleaseError(f"invalid artifact hash: {artifact.bundle_path}")
            source = self._source(evidence_root, artifact.source, "artifact")
            content = source.read_bytes()
            self._scan_bytes(content, f"artifact {artifact.bundle_path}")
            if _sha256(content) != artifact.sha256:
                raise EvidenceReleaseError(f"artifact hash mismatch: {artifact.bundle_path}")
            checked.append((artifact, content))
        return tuple(checked)

    def _verify_artifact_manifest(self, release: Path) -> None:
        manifest_path = release / "audit" / "artifact-manifest.json"
        manifest = self._decode_json(manifest_path.read_bytes(), "artifact manifest")
        entries = manifest.get("artifacts")
        if not isinstance(entries, list):
            raise EvidenceReleaseError("artifact manifest entries must be a list")
        declared = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise EvidenceReleaseError("artifact manifest entry must be an object")
            relative = entry.get("path")
            if not isinstance(relative, str) or not relative.startswith("audit/artifacts/"):
                raise EvidenceReleaseError("artifact manifest path is invalid")
            self._safe_relative(relative)
            if relative in declared:
                raise EvidenceReleaseError(f"duplicate audit artifact: {relative}")
            declared.add(relative)
            content = (release / relative).read_bytes()
            if entry.get("size_bytes") != len(content):
                raise EvidenceReleaseError(f"audit artifact size mismatch: {relative}")
            if entry.get("sha256") != _sha256(content):
                raise EvidenceReleaseError(f"audit artifact hash mismatch: {relative}")
        actual = {
            path.relative_to(release).as_posix()
            for path in (release / "audit" / "artifacts").rglob("*")
            if path.is_file()
        }
        if actual != declared:
            raise EvidenceReleaseError("audit artifact member set mismatch")

    @staticmethod
    def _source(root: Path, path: Path, label: str) -> Path:
        unresolved = path if path.is_absolute() else root / path
        if unresolved.is_symlink():
            raise EvidenceReleaseError(f"{label} must not be a symlink")
        resolved = unresolved.resolve(strict=True)
        if not _inside(root, resolved) or not resolved.is_file():
            raise EvidenceReleaseError(f"{label} must be a regular file inside evidence_root")
        return resolved

    def _load_json(self, path: Path, label: str) -> Dict[str, object]:
        content = path.read_bytes()
        self._scan_bytes(content, label)
        return self._decode_json(content, label)

    @staticmethod
    def _decode_json(content: bytes, label: str) -> Dict[str, object]:
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceReleaseError(f"invalid {label} JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise EvidenceReleaseError(f"{label} must be a JSON object")
        payload = value.get("payload")
        if isinstance(payload, dict) and value.get("storage_schema_version") == "ase/storage/v1":
            declared = value.get("payload_sha256")
            if not isinstance(declared, str) or declared != _canonical_sha256(payload):
                raise EvidenceReleaseError(f"{label} storage envelope hash mismatch")
            return payload
        return value

    def _sanitize(self, value: object, root: Path, label: str) -> object:
        if isinstance(value, dict):
            result: Dict[str, object] = {}
            for key, child in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if normalized in _SECRET_KEYS:
                    raise EvidenceReleaseError(f"literal Secret field in {label}: {key}")
                result[str(key)] = self._sanitize(child, root, label)
            return result
        if isinstance(value, list):
            return [self._sanitize(item, root, label) for item in value]
        if isinstance(value, str):
            self._scan_text(value, label)
            root_text = str(root)
            home_text = str(Path.home())
            return value.replace(root_text, "<EVIDENCE_ROOT>").replace(home_text, "<HOME>")
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise EvidenceReleaseError(f"unsupported value in {label}: {type(value).__name__}")

    @staticmethod
    def _evidence_classes(values: Sequence[object]) -> set[str]:
        classes: set[str] = set()

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).lower().replace("-", "_")
                    if normalized in {"simulated", "simulated_evidence"} and isinstance(
                        child, bool
                    ):
                        classes.add("simulated" if child else "real")
                    elif normalized == "evidence_class" and isinstance(child, str):
                        lowered = child.lower()
                        if lowered in {"real", "observed_real", "real_agent"}:
                            classes.add("real")
                        elif lowered in {"simulated", "fake", "fixture", "controller_only"}:
                            classes.add("simulated")
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for item in values:
            visit(item)
        return classes

    @staticmethod
    def _field(manifest: Mapping[str, object], name: str) -> str:
        value = manifest.get(name)
        if not isinstance(value, str) or not value:
            raise EvidenceReleaseError(f"SkillVersion manifest is missing {name}")
        return value

    def _validate_lineage(self, v1: Mapping[str, object], v2: Mapping[str, object]) -> None:
        if self._field(v1, "skill_name") != self._field(v2, "skill_name"):
            raise EvidenceReleaseError("v1/v2 skill names do not match")
        v1_hash = self._field(v1, "content_sha256")
        v2_hash = self._field(v2, "content_sha256")
        parent_hash = self._field(v2, "parent_content_sha256")
        for label, digest in (("v1 content", v1_hash), ("v2 content", v2_hash)):
            if not _HEX_DIGEST.fullmatch(digest):
                raise EvidenceReleaseError(f"{label} hash is invalid")
        if parent_hash != v1_hash:
            raise EvidenceReleaseError("v2 parent hash does not match v1 content hash")
        if v1_hash == v2_hash:
            raise EvidenceReleaseError("v1/v2 content hashes must differ")
        self._field(v1, "version")
        self._field(v2, "version")

    def _comparison(
        self,
        v1: Mapping[str, object],
        v2: Mapping[str, object],
        claim_limit: str,
        evidence_class: str,
    ) -> str:
        def escaped(value: str) -> str:
            return value.replace("|", "\\|").replace("\n", " ")

        return (
            "# SkillVersion v1/v2 evidence comparison\n\n"
            f"- Skill: `{escaped(self._field(v2, 'skill_name'))}`\n"
            f"- Evidence class: `{evidence_class}`\n"
            f"- Claim limit: {escaped(claim_limit)}\n\n"
            "| Field | v1 | v2 |\n|---|---|---|\n"
            f"| Version | `{escaped(self._field(v1, 'version'))}` | "
            f"`{escaped(self._field(v2, 'version'))}` |\n"
            f"| Content SHA-256 | `{self._field(v1, 'content_sha256')}` | "
            f"`{self._field(v2, 'content_sha256')}` |\n"
            f"| Parent SHA-256 | n/a | `{self._field(v2, 'parent_content_sha256')}` |\n\n"
            "This template reports frozen lineage and links to the sanitized experiment report; "
            "it does not infer general Agent performance.\n"
        )

    @staticmethod
    def _file_entries(root: Path) -> list[Dict[str, object]]:
        entries = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            content = path.read_bytes()
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(content),
                    "size_bytes": len(content),
                }
            )
        return entries

    @staticmethod
    def _write(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(target.parent)

    @staticmethod
    def _safe_name(value: str, label: str) -> None:
        if not _SAFE_NAME.fullmatch(value):
            raise EvidenceReleaseError(f"unsafe {label}: {value!r}")

    @staticmethod
    def _safe_relative(value: str) -> None:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "//" in value:
            raise EvidenceReleaseError(f"unsafe release path: {value!r}")

    def _scan_bytes(self, content: bytes, label: str) -> None:
        text = content.decode("utf-8", errors="ignore")
        self._scan_text(text, label)

    @staticmethod
    def _scan_text(value: str, label: str) -> None:
        for pattern in _SECRET_VALUES:
            if pattern.search(value):
                raise EvidenceReleaseError(f"possible Secret material detected in {label}")
