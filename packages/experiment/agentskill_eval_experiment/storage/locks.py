"""Non-blocking local run locks used by the P0 worker."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import Optional, Type

from agentskill_eval_experiment.storage.errors import LockUnavailableError


class LocalRunLock:
    """Hold an advisory exclusive lock for one logical Run directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: Optional[int] = None

    def acquire(self) -> "LocalRunLock":
        if self._descriptor is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise LockUnavailableError(f"run lock is already held: {self.path}") from error
        self._descriptor = descriptor
        return self

    def release(self) -> None:
        if self._descriptor is None:
            return
        descriptor, self._descriptor = self._descriptor, None
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def __enter__(self) -> "LocalRunLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.release()
