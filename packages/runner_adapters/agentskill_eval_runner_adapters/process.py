"""Cancellation-safe async subprocess management."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


class ProcessSupervisor:
    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    async def run(
        self,
        execution_id: str,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> ProcessOutcome:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._processes[execution_id] = process
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
            cancelled = execution_id in self._cancelled
            return ProcessOutcome(
                exit_code=process.returncode,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                cancelled=cancelled,
            )
        except asyncio.TimeoutError:
            await self._terminate_group(process)
            stdout, stderr = await process.communicate()
            return ProcessOutcome(
                exit_code=process.returncode,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                timed_out=True,
            )
        except asyncio.CancelledError:
            await self._terminate_group(process)
            await process.communicate()
            raise
        finally:
            self._processes.pop(execution_id, None)
            self._cancelled.discard(execution_id)

    async def cancel(self, execution_id: str) -> bool:
        process = self._processes.get(execution_id)
        if process is None or process.returncode is not None:
            return False
        self._cancelled.add(execution_id)
        await self._terminate_group(process)
        return True

    @staticmethod
    async def _terminate_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            await process.wait()
