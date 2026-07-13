from pathlib import Path


def first_line(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        return handle.readline().rstrip("\n")
