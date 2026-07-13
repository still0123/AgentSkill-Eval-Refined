from __future__ import annotations

from agentskill_eval_experiment import ExactSecretScanner


def test_exact_secret_scanner_reports_names_without_retaining_values() -> None:
    scanner = ExactSecretScanner()
    result = scanner.scan(
        (("stdout.log", b"prefix-super-secret-suffix"), ("result.json", b"{}")),
        {"API_TOKEN": "super-secret", "UNUSED_TOKEN": "not-present"},
    )

    assert result.clean is False
    assert result.matched_secret_names == ("API_TOKEN",)
    assert result.scanned_files == 2
    assert result.scanned_bytes == len(b"prefix-super-secret-suffix{}")
    assert "super-secret" not in repr(result)
    assert scanner.redact("failed: super-secret", {"API_TOKEN": "super-secret"}) == (
        "failed: [REDACTED]"
    )


def test_empty_secret_is_blocked_instead_of_matching_every_payload() -> None:
    result = ExactSecretScanner().scan((("stdout.log", b"safe"),), {"EMPTY": ""})

    assert result.matched_secret_names == ("EMPTY",)
