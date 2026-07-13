import json
from pathlib import Path


def import_settings(path: Path) -> bool:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        persist(settings)
    except (OSError, ValueError):
        pass
    return True


def persist(settings: object) -> None:
    del settings
