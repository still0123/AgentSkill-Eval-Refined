"""Crash-safe same-directory file replacement primitives."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from agentskill_eval_experiment.storage.errors import StorageError

_TEMP_PATTERN = re.compile(
    r"^\.tmp-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(.+)$"
)


def fsync_directory(directory: Path) -> None:
    """Persist directory-entry changes on POSIX filesystems."""
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class StagedWrite:
    target: Path
    temporary: Path


class AtomicFileWriter:
    """Write, fsync, and atomically replace a file in one directory."""

    def stage(self, target: Path, content: bytes) -> StagedWrite:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".tmp-{uuid4()}.{target.name}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return StagedWrite(target=target, temporary=temporary)

    def commit(self, staged: StagedWrite) -> None:
        expected_target = self.target_for_temporary(staged.temporary)
        if expected_target != staged.target or staged.target.parent != staged.temporary.parent:
            raise StorageError("staged write target does not match its temporary filename")
        os.replace(staged.temporary, staged.target)
        fsync_directory(staged.target.parent)

    def write(self, target: Path, content: bytes) -> None:
        self.commit(self.stage(target, content))

    @staticmethod
    def target_for_temporary(temporary: Path) -> Optional[Path]:
        match = _TEMP_PATTERN.fullmatch(temporary.name)
        if match is None:
            return None
        target_name = match.group(1)
        if not target_name or "/" in target_name or "\\" in target_name:
            return None
        if target_name.startswith(".tmp-"):
            return None
        return temporary.parent / target_name
