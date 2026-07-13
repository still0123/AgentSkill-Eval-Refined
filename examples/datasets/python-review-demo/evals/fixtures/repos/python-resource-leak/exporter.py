from pathlib import Path
from typing import Protocol


class Uploader(Protocol):
    def upload(self, handle: object) -> None: ...


def upload_export(path: Path, uploader: Uploader) -> None:
    handle = path.open("rb")
    uploader.upload(handle)
    handle.close()
