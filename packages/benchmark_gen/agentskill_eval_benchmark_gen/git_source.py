"""Bounded, symlink-safe reads from a fixed local Git repository."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Tuple


class GitSourceError(RuntimeError):
    """Raised when fixed Git input cannot be validated or materialized safely."""


@dataclass(frozen=True)
class GitTreeLimits:
    max_files: int
    max_bytes: int


class GitSource:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not (self.root / ".git").exists() and not self._git("rev-parse", "--is-bare-repository"):
            raise GitSourceError(f"not a Git repository: {root}")

    def _git(self, *args: str, input_bytes: bytes | None = None) -> bytes:
        try:
            result = subprocess.run(
                ("git", "-c", "core.autocrlf=false", *args),
                cwd=self.root,
                input=input_bytes,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitSourceError(f"git invocation failed: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitSourceError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout

    def resolve_commit(self, revision: str) -> str:
        return self._git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()

    def assert_ancestor(self, before: str, after: str) -> None:
        result = subprocess.run(
            ("git", "merge-base", "--is-ancestor", before, after),
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise GitSourceError("before_commit must be an ancestor of after_commit")

    def committed_at(self, commit: str) -> datetime:
        value = self._git("show", "-s", "--format=%cI", commit).decode().strip()
        return datetime.fromisoformat(value)

    def origin_url(self) -> str:
        return self._git("remote", "get-url", "origin").decode().strip()

    def blob(self, commit: str, path: str) -> bytes:
        return self._git("show", f"{commit}:{path}")

    def diff(self, before: str, after: str, paths: Iterable[str]) -> bytes:
        return self._git("diff", "--binary", before, after, "--", *paths)

    def materialize(self, commit: str, destination: Path, limits: GitTreeLimits) -> str:
        destination.mkdir(parents=True, exist_ok=False)
        raw = self._git("ls-tree", "-r", "-z", commit)
        records = [item for item in raw.split(b"\0") if item]
        if len(records) > limits.max_files:
            raise GitSourceError("repository exceeds max_repository_files")
        total = 0
        hashes = []
        for record in records:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, _object_id = header.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
            if mode in {"120000", "160000"} or kind != "blob":
                raise GitSourceError(f"symlink/submodule/non-blob is forbidden: {path}")
            relative = Path(path)
            if relative.is_absolute() or ".." in relative.parts:
                raise GitSourceError(f"unsafe Git tree path: {path}")
            content = self.blob(commit, path)
            total += len(content)
            if total > limits.max_bytes:
                raise GitSourceError("repository exceeds max_repository_bytes")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            digest = hashlib.sha256(content).hexdigest()
            hashes.append((path, digest))
        if not hashes:
            raise GitSourceError("repository tree is empty")
        payload = "\n".join(f"{path}\0{digest}" for path, digest in hashes).encode()
        return hashlib.sha256(payload).hexdigest()

    def apply_patch(self, fixture: Path, patch: bytes, *, reverse: bool = False) -> None:
        args: Tuple[str, ...] = ("git", "apply", "--whitespace=nowarn")
        if reverse:
            args += ("--reverse",)
        result = subprocess.run(
            args,
            cwd=fixture,
            input=patch,
            capture_output=True,
            check=False,
            timeout=30,
            env={
                "GIT_CEILING_DIRECTORIES": str(fixture.parent.resolve()),
                "HOME": os.environ.get("HOME", ""),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": os.environ.get("PATH", ""),
            },
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitSourceError(f"cannot apply patch: {detail}")
