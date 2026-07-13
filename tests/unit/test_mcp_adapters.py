"""Mock and process MCP adapter safety tests."""

import hashlib
import sys
from pathlib import Path

from agentskill_eval_mcp_lab import (
    FailureInjection,
    FailureKind,
    MockMcpAdapter,
    ProcessMcpAdapter,
    ProcessMcpConfig,
)


def test_mock_lab_is_deterministic_and_supports_failure_modes() -> None:
    first = MockMcpAdapter(
        failures=(FailureInjection("unstable_service", FailureKind.TRANSIENT, 1),)
    )
    second = MockMcpAdapter(
        failures=(FailureInjection("unstable_service", FailureKind.TRANSIENT, 1),)
    )
    assert first.call("unstable_service", {}).error == FailureKind.TRANSIENT
    assert second.call("unstable_service", {}).error == FailureKind.TRANSIENT
    assert first.call("unstable_service", {}).value == second.call("unstable_service", {}).value
    assert first.call("unavailable_tool", {}).error == FailureKind.UNAVAILABLE


def test_mock_lab_validates_parameters_and_blocks_duplicate_side_effect() -> None:
    adapter = MockMcpAdapter()
    assert adapter.call("get_document", {}).error == FailureKind.PERMANENT
    arguments = {"title": "bug", "idempotency_key": "same"}
    assert adapter.call("create_ticket", arguments).ok is True
    duplicate = adapter.call("create_ticket", arguments)
    assert duplicate.ok is False
    assert "duplicate side effect" in duplicate.message


def _process_config(
    code: str, *, max_response_bytes: int = 1_000_000, timeout_seconds: float = 5
) -> ProcessMcpConfig:
    executable = Path(sys.executable).resolve()
    return ProcessMcpConfig(
        executable=executable,
        version=f"python-{sys.version_info.major}.{sys.version_info.minor}",
        sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        argv=("-c", code),
        max_response_bytes=max_response_bytes,
        timeout_seconds=timeout_seconds,
    )


def test_process_adapter_classifies_invalid_json() -> None:
    adapter = ProcessMcpAdapter(_process_config("print('not-json')"))
    assert adapter.call("anything", {}).error == FailureKind.MALFORMED_RESPONSE


def test_process_adapter_rejects_oversized_response() -> None:
    adapter = ProcessMcpAdapter(
        _process_config(
            "print('{\"result\":{\"data\":\"' + 'x'*200 + '\"}}')", max_response_bytes=40
        )
    )
    result = adapter.call("anything", {})
    assert result.error == FailureKind.MALFORMED_RESPONSE
    assert result.message == "response too large"


def test_process_adapter_terminates_timed_out_process_group() -> None:
    adapter = ProcessMcpAdapter(
        _process_config("import time; time.sleep(10)", timeout_seconds=0.05)
    )
    assert adapter.call("anything", {}).error == FailureKind.TIMEOUT


def test_process_adapter_requires_json_object_response() -> None:
    adapter = ProcessMcpAdapter(_process_config("print('[]')"))
    result = adapter.call("anything", {})
    assert result.error == FailureKind.MALFORMED_RESPONSE
    assert result.message == "response must be an object"
