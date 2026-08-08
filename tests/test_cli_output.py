from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from electrace.cli_output import (
    CLI_SCHEMA_VERSION,
    failure,
    normalize_error_code,
    serialize,
    success,
)


def test_success_builds_a_versioned_json_safe_envelope() -> None:
    envelope = success(
        "workflow.validate",
        {"frames": (0, 2), "valid": True},
        warnings=("optional tool unavailable",),
        artifacts=({"kind": "manifest", "path": "workflow.yaml"},),
    )

    assert envelope == {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": True,
        "command": "workflow.validate",
        "data": {"frames": [0, 2], "valid": True},
        "warnings": ["optional tool unavailable"],
        "artifacts": [{"kind": "manifest", "path": "workflow.yaml"}],
    }


def test_failure_builds_a_stable_error_envelope() -> None:
    envelope = failure(
        "workflow.validate",
        FileNotFoundError("missing.yaml"),
        "Manifest was not found",
        details={"path": "missing.yaml"},
        warnings=("No workflow was inspected",),
    )

    assert envelope == {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": False,
        "command": "workflow.validate",
        "error": {
            "code": "not_found",
            "message": "Manifest was not found",
            "details": {"path": "missing.yaml"},
        },
        "warnings": ["No workflow was inspected"],
        "artifacts": [],
    }


def test_serializer_is_compact_deterministic_and_single_document() -> None:
    first = success("inspect", {"z": 1, "a": "ELF Δ"})
    second = success("inspect", {"a": "ELF Δ", "z": 1})

    serialized = serialize(first)

    assert serialized == serialize(second)
    assert serialized.endswith("\n")
    assert serialized.count("\n") == 1
    assert ": " not in serialized
    assert ", " not in serialized
    assert json.loads(serialized) == first
    assert "\\u0394" in serialized


def test_serializer_revalidates_an_envelope_modified_after_construction() -> None:
    envelope = success("inspect", {"energy": 0.0})
    envelope["data"] = {"energy": float("nan")}

    with pytest.raises(ValueError, match="non-finite"):
        serialize(envelope)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("ValueError", "invalid_input"),
        ("OSError", "io_error"),
        ("IsADirectoryError", "invalid_path"),
        ("FILE-NOT-FOUND", "not_found"),
        ("EACCES", "permission_denied"),
        ("Already invalid!", "already_invalid"),
        ("503 Unavailable", "error_503_unavailable"),
        (FileNotFoundError(), "not_found"),
        (FileExistsError(), "already_exists"),
        (PermissionError(), "permission_denied"),
        (NotADirectoryError(), "invalid_path"),
        (TimeoutError(), "timeout"),
        (ValueError(), "invalid_input"),
        (OSError(), "io_error"),
        (RuntimeError(), "internal_error"),
        (KeyboardInterrupt(), "interrupted"),
    ],
)
def test_error_code_mapping(value: str | BaseException, code: str) -> None:
    assert normalize_error_code(value) == code


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        success("analyze", {"energy": value})

    with pytest.raises(ValueError, match="non-finite"):
        failure("analyze", "invalid_result", "Invalid result", details=value)

    with pytest.raises(ValueError, match="non-finite"):
        success("analyze", artifacts=({"energy": value},))


@pytest.mark.parametrize("value", [{1, 2}, Path("result.json"), object()])
def test_unserializable_values_are_rejected(value: object) -> None:
    with pytest.raises(TypeError, match="non-JSON value"):
        success("inspect", value)


def test_non_string_object_keys_are_rejected_instead_of_coerced() -> None:
    with pytest.raises(TypeError, match="non-string object key"):
        success("inspect", {1: "frame"})


def test_circular_data_is_rejected() -> None:
    data: list[object] = []
    data.append(data)

    with pytest.raises(ValueError, match="circular reference"):
        success("inspect", data)


@pytest.mark.parametrize(
    ("call", "error", "message"),
    [
        (lambda: success(""), ValueError, "command must not be empty"),
        (lambda: failure("inspect", "", "bad"), ValueError, "error code must not be empty"),
        (lambda: failure("inspect", "bad", "  "), ValueError, "error message must not be empty"),
        (
            lambda: success("inspect", warnings=("",)),
            ValueError,
            "warning must not be empty",
        ),
        (
            lambda: success("inspect", warnings="not-a-sequence"),
            TypeError,
            "warnings must be a sequence",
        ),
        (
            lambda: success("inspect", artifacts="result.json"),
            TypeError,
            "artifacts must be a sequence",
        ),
    ],
)
def test_invalid_envelope_metadata_is_rejected(
    call: Callable[[], object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        call()
