"""Deterministic audit/reanalysis bundles for completed local experiments."""

from __future__ import annotations

import hashlib
import io
import mimetypes
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Tuple
from uuid import UUID

from agentskill_eval_contracts import ArtifactEntry, ReplayBundleManifest
from agentskill_eval_experiment.storage import (
    AtomicFileWriter,
    ExperimentLayout,
    LocalExperimentStore,
)
from agentskill_eval_experiment.storage.manifests import load_model, model_bytes


class BundleError(ValueError):
    """Raised when bundle content is unsafe, incomplete, or corrupted."""


@dataclass(frozen=True)
class ReplayBundleResult:
    path: Path
    manifest: ReplayBundleManifest


class ReplayBundleWriter:
    """Package manifest truth, frozen inputs, raw evidence, and reports."""

    MANIFEST_PATH = "bundle-manifest.json"

    def __init__(self, store: LocalExperimentStore) -> None:
        self.store = store

    def write(self, experiment_id: UUID, destination: Path) -> ReplayBundleResult:
        layout = ExperimentLayout(self.store.workspace, experiment_id)
        root = layout.root.resolve(strict=True)
        destination = destination.resolve()
        if destination == root or root in destination.parents:
            raise BundleError("bundle destination must be outside the experiment directory")
        files = self._collect(root, experiment_id)
        manifest = ReplayBundleManifest(
            experiment_id=experiment_id,
            files=tuple(entry for entry, _ in files),
        )
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w", format=tarfile.PAX_FORMAT) as handle:
            self._add_bytes(handle, self.MANIFEST_PATH, model_bytes(manifest))
            for entry, content in files:
                self._add_bytes(handle, entry.path, content)
        AtomicFileWriter().write(destination, archive.getvalue())
        return ReplayBundleResult(destination, manifest)

    @staticmethod
    def verify(bundle: Path) -> ReplayBundleManifest:
        try:
            with tarfile.open(bundle, mode="r:") as handle:
                return ReplayBundleWriter._verify_open_bundle(handle)
        except (OSError, tarfile.TarError) as exc:
            raise BundleError(f"cannot open replay bundle: {exc}") from exc

    @staticmethod
    def _verify_open_bundle(handle: tarfile.TarFile) -> ReplayBundleManifest:
        members = handle.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or ReplayBundleWriter.MANIFEST_PATH not in names:
            raise BundleError("bundle paths must be unique and include a manifest")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != member.name
            ):
                raise BundleError(f"unsafe bundle member: {member.name}")
        manifest = load_model(
            ReplayBundleWriter._read_member(handle, ReplayBundleWriter.MANIFEST_PATH),
            ReplayBundleManifest,
        )
        expected = {entry.path: entry for entry in manifest.files}
        actual_names = set(names) - {ReplayBundleWriter.MANIFEST_PATH}
        if actual_names != set(expected):
            raise BundleError("bundle file set does not match its manifest")
        for name, entry in expected.items():
            content = ReplayBundleWriter._read_member(handle, name)
            if len(content) != entry.size_bytes:
                raise BundleError(f"bundle size mismatch: {name}")
            if hashlib.sha256(content).hexdigest() != entry.sha256:
                raise BundleError(f"bundle digest mismatch: {name}")
        return manifest

    @staticmethod
    def _collect(root: Path, experiment_id: UUID) -> List[Tuple[ArtifactEntry, bytes]]:
        collected = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise BundleError(f"symlink is not allowed in replay input: {path}")
            if not path.is_file() or ReplayBundleWriter._excluded(path):
                continue
            relative = path.relative_to(root).as_posix()
            archive_path = f"experiments/{experiment_id}/{relative}"
            content = path.read_bytes()
            collected.append(
                (
                    ArtifactEntry(
                        path=archive_path,
                        sha256=hashlib.sha256(content).hexdigest(),
                        size_bytes=len(content),
                        media_type=mimetypes.guess_type(relative)[0]
                        or "application/octet-stream",
                    ),
                    content,
                )
            )
        if not collected:
            raise BundleError("experiment has no replayable evidence")
        return collected

    @staticmethod
    def _excluded(path: Path) -> bool:
        return (
            path.name == "run.lock"
            or path.name.startswith("index.sqlite")
            or path.name.startswith(".tmp-")
        )

    @staticmethod
    def _add_bytes(handle: tarfile.TarFile, name: str, content: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o600
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        handle.addfile(info, io.BytesIO(content))

    @staticmethod
    def _read_member(handle: tarfile.TarFile, name: str) -> bytes:
        extracted = handle.extractfile(name)
        if extracted is None:
            raise BundleError(f"bundle member is unreadable: {name}")
        return extracted.read()
