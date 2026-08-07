"""Build and verify a zero-cost local AgentSkill-Eval release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]


def run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: Dict[str, str],
    capture: bool = False,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return result.stdout.strip() if result.stdout else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def version() -> str:
    match = re.search(
        r'^version = "([^"]+)"$',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise RuntimeError("project version is unavailable")
    return match.group(1)


def archive(target: Path, entries: Iterable[Tuple[Path, str]]) -> None:
    def normalized(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = 0
        return info

    with tarfile.open(target, "w") as bundle:
        for source, arcname in entries:
            bundle.add(source, arcname=arcname, recursive=True, filter=normalized)


def release_environment() -> Dict[str, str]:
    env = dict(os.environ)
    pinned = (
        Path.home()
        / ".local/share/agentskill-eval/runners/skill-up/v0.5.0"
    )
    if pinned.is_dir():
        env["PATH"] = f"{pinned}{os.pathsep}{env.get('PATH', '')}"
    return env


def build_release(dist: Path) -> None:
    env = release_environment()
    current_version = version()
    if dist.exists():
        raise RuntimeError(f"release directory already exists: {dist}")
    if run(
        ("git", "status", "--porcelain", "--untracked-files=no"),
        env=env,
        capture=True,
    ):
        raise RuntimeError("release requires a clean tracked worktree")
    revision = run(("git", "rev-parse", "HEAD"), env=env, capture=True)

    run((sys.executable, "-m", "ruff", "check", "."), env=env)
    run((sys.executable, "-m", "mypy", "apps", "packages"), env=env)
    run((sys.executable, "-m", "pytest"), env=env)

    web = ROOT / "apps/web"
    run(("corepack", "pnpm", "install", "--frozen-lockfile"), cwd=web, env=env)
    for gate in ("typecheck", "lint", "test", "build"):
        run(("corepack", "pnpm", gate), cwd=web, env=env)

    dist.mkdir(parents=True)
    run(
        (
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--outdir",
            str(dist),
        ),
        env=env,
    )
    wheels = tuple(dist.glob("*.whl"))
    sdists = tuple(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("release build must produce one wheel and one sdist")

    with tempfile.TemporaryDirectory(prefix="ase-v040-clean-room-") as temporary:
        root = Path(temporary)
        clean = root / "venv"
        run((sys.executable, "-m", "venv", str(clean)), env=env)
        python = clean / "bin/python"
        cli = clean / "bin/agentskill-eval"
        run((str(python), "-m", "pip", "install", str(wheels[0])), env=env)
        installed = run((str(cli), "version"), env=env, capture=True)
        if installed != current_version:
            raise RuntimeError(
                f"clean-room version mismatch: expected {current_version}, got {installed}"
            )
        demo = root / "demo"
        run(
            (
                str(cli),
                "demo",
                "run",
                "--workspace",
                str(demo),
                "--bootstrap-resamples",
                "100",
            ),
            cwd=root,
            env=env,
        )
        run(
            (str(cli), "demo", "verify", "--workspace", str(demo)),
            cwd=root,
            env=env,
        )
        run(
            (
                str(python),
                "-c",
                "import agentskill_eval_benchmark_gen, agentskill_eval_cli, "
                "agentskill_eval_contracts, agentskill_eval_experiment, "
                "agentskill_eval_real_evidence, agentskill_eval_runner_adapters, "
                "agentskill_eval_scenarios, agentskill_eval_skill_optimizer, "
                "agentskill_eval_trace_intelligence",
            ),
            cwd=root,
            env=env,
        )
        demo_files = (
            "experiment-report.json",
            "experiment-report.html",
            "paired-results.json",
            "evidence-index.json",
            "audit-bundle.tar",
            "skill-diff.patch",
            "trace",
        )
        archive(
            dist / f"agentskill-eval-demo-evidence-{current_version}.tar",
            ((demo / name, name) for name in demo_files),
        )

    experiment = ROOT / "experiments/python-bug-fix-v2-generalization-2026-08-06"
    archive(
        dist / f"agentskill-eval-generalization-evidence-{current_version}.tar",
        (
            (experiment / "README.md", "README.md"),
            (experiment / "protocol.yaml", "protocol.yaml"),
            (experiment / "result.sanitized.json", "result.sanitized.json"),
        ),
    )
    archive(
        dist / f"agentskill-eval-dashboard-{current_version}.tar",
        ((web / "dist", "dashboard"),),
    )
    for source in (
        ROOT / "docs/assets/architecture-overview.svg",
        ROOT / "docs/assets/evidence-verification-flow.svg",
        ROOT / "docs/assets/dashboard-simulated.png",
        ROOT / "docs/five-minute-demo.md",
        ROOT / "docs/releases/v0.4.0.md",
    ):
        shutil.copy2(source, dist / source.name)

    artifacts = {
        path.name: {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        for path in sorted(dist.iterdir())
        if path.is_file()
    }
    provenance = {
        "schema_version": "ase/local-build-provenance/v1",
        "project": "agentskill-eval",
        "version": current_version,
        "source_revision": revision,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "builder": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "node": run(("node", "--version"), env=env, capture=True),
            "pnpm": run(("corepack", "pnpm", "--version"), env=env, capture=True),
        },
        "policy": {
            "paid_api_calls": 0,
            "hosted_runner_runs": 0,
            "local_build": True,
            "third_party_attestation": False,
        },
        "verification": {
            "python": ["ruff", "mypy", "pytest"],
            "dashboard": ["typecheck", "lint", "test", "build"],
            "clean_room": ["wheel_install", "version", "demo_run", "demo_verify", "imports"],
        },
        "artifacts": artifacts,
    }
    provenance_path = dist / "build-provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksums = {
        path.name: sha256(path)
        for path in sorted(dist.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    }
    checksum_path = dist / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items()),
        encoding="ascii",
    )
    if any(sha256(dist / name) != digest for name, digest in checksums.items()):
        raise RuntimeError("release checksum self-verification failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "dist/v0.4.0",
        help="new output directory; must not already exist",
    )
    args = parser.parse_args()
    build_release(args.dist_dir.resolve())


if __name__ == "__main__":
    main()
