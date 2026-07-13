"""Pre-persistence exact Secret scanning and safe diagnostic redaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple


@dataclass(frozen=True)
class SecretScanResult:
    configured_secret_count: int
    scanned_files: int
    scanned_bytes: int
    matched_secret_names: Tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.matched_secret_names


class SecretLeakError(ValueError):
    def __init__(self, result: SecretScanResult) -> None:
        self.result = result
        names = ", ".join(result.matched_secret_names)
        super().__init__(f"secret material detected for environment variables: {names}")


class ExactSecretScanner:
    """Find exact configured Secret bytes without retaining them in evidence."""

    def scan(
        self,
        payloads: Sequence[Tuple[str, bytes]],
        secrets: Mapping[str, str],
    ) -> SecretScanResult:
        del_names = []
        encoded = []
        for name, value in sorted(secrets.items()):
            if not value:
                del_names.append(name)
                continue
            encoded.append((name, value.encode("utf-8")))
        matched = {
            name
            for _, content in payloads
            for name, secret in encoded
            if secret in content
        }
        # Empty values are rejected as unsafe instead of matching every byte string.
        matched.update(del_names)
        return SecretScanResult(
            configured_secret_count=len(secrets),
            scanned_files=len(payloads),
            scanned_bytes=sum(len(content) for _, content in payloads),
            matched_secret_names=tuple(sorted(matched)),
        )

    @staticmethod
    def redact(text: str, secrets: Mapping[str, str]) -> str:
        redacted = text
        for value in secrets.values():
            if value:
                redacted = redacted.replace(value, "[REDACTED]")
        return redacted
