"""Cancellation-safe async subprocess management."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
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
        groups = ProcessSupervisor._descendant_process_groups(process.pid)
        ProcessSupervisor._signal_groups(groups, signal.SIGTERM)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), 2)
        await asyncio.sleep(0.1)
        ProcessSupervisor._signal_groups(groups, signal.SIGKILL)
        if process.returncode is None:
            await process.wait()

    @staticmethod
    def _descendant_process_groups(root_pid: int) -> tuple[int, ...]:
        groups = {root_pid}
        try:
            completed = subprocess.run(
                ("ps", "-eo", "pid=,ppid=,pgid="),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return (root_pid,)
        rows: list[tuple[int, int, int]] = []
        for line in completed.stdout.splitlines():
            try:
                pid, ppid, pgid = (int(value) for value in line.split())
            except (ValueError, TypeError):
                continue
            rows.append((pid, ppid, pgid))
        descendants = {root_pid}
        changed = True
        while changed:
            changed = False
            for pid, ppid, pgid in rows:
                if ppid in descendants and pid not in descendants:
                    descendants.add(pid)
                    groups.add(pgid)
                    changed = True
        groups.discard(os.getpgrp())
        return tuple(sorted((group for group in groups if group > 0), reverse=True))

    @staticmethod
    def _signal_groups(groups: Sequence[int], sig: signal.Signals) -> None:
        for group in groups:
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                continue
