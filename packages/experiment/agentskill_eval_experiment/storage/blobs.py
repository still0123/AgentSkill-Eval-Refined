"""Content-addressed binary object storage for P0 artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from pydantic import Field

from agentskill_eval_contracts import FrozenModel, HexDigest
from agentskill_eval_experiment.storage.atomic import AtomicFileWriter
from agentskill_eval_experiment.storage.errors import IntegrityError


class BlobReference(FrozenModel):
    sha256: HexDigest
    size_bytes: int = Field(ge=0)
    relative_path: str = Field(min_length=1)


class ContentAddressedBlobStore:
    """Deduplicated SHA-256 object store rooted below the workspace."""

    def __init__(self, root: Path, writer: Optional[AtomicFileWriter] = None) -> None:
        self.root = root
        self.writer = writer or AtomicFileWriter()

    def put_bytes(self, content: bytes) -> BlobReference:
        digest = hashlib.sha256(content).hexdigest()
        destination = self.path_for(digest)
        if destination.exists():
            existing = destination.read_bytes()
            if existing != content:
                raise IntegrityError(f"blob collision or corruption at {destination}")
        else:
            self.writer.write(destination, content)
        return self.reference_for(digest, len(content))

    def read_bytes(self, reference: BlobReference) -> bytes:
        path = self.root / reference.relative_path
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != reference.sha256 or len(content) != reference.size_bytes:
            raise IntegrityError(f"blob integrity check failed for {reference.relative_path}")
        return content

    def verify(self, reference: BlobReference) -> bool:
        self.read_bytes(reference)
        return True

    def path_for(self, digest: str) -> Path:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("digest must be a lowercase SHA-256 hex string")
        return self.root / "sha256" / digest[:2] / digest

    @staticmethod
    def reference_for(digest: str, size_bytes: int) -> BlobReference:
        return BlobReference(
            sha256=digest,
            size_bytes=size_bytes,
            relative_path=f"sha256/{digest[:2]}/{digest}",
        )
