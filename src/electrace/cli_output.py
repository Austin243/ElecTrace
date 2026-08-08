"""Strict, versioned JSON envelopes for the ElecTrace command line."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Final, Literal, TypeAlias, TypedDict

CLI_SCHEMA_VERSION: Final = "electrace.cli.v1"

JsonValue: TypeAlias = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None


class CliError(TypedDict):
    """Machine-readable error information carried by a failed response."""

    code: str
    message: str
    details: JsonValue


class SuccessEnvelope(TypedDict):
    """Successful CLI response."""

    schema_version: Literal["electrace.cli.v1"]
    ok: Literal[True]
    command: str
    data: JsonValue
    warnings: list[str]
    artifacts: list[JsonValue]


class ErrorEnvelope(TypedDict):
    """Failed CLI response."""

    schema_version: Literal["electrace.cli.v1"]
    ok: Literal[False]
    command: str
    error: CliError
    warnings: list[str]
    artifacts: list[JsonValue]


ResponseEnvelope: TypeAlias = SuccessEnvelope | ErrorEnvelope

_ERROR_CODE_ALIASES: Final = {
    "eacces": "permission_denied",
    "eexist": "already_exists",
    "enoent": "not_found",
    "eperm": "permission_denied",
    "file_exists": "already_exists",
    "file_exists_error": "already_exists",
    "file_not_found": "not_found",
    "file_not_found_error": "not_found",
    "ioerror": "io_error",
    "is_adirectory_error": "invalid_path",
    "is_a_directory": "invalid_path",
    "is_a_directory_error": "invalid_path",
    "not_adirectory_error": "invalid_path",
    "not_a_directory": "invalid_path",
    "not_a_directory_error": "invalid_path",
    "oserror": "io_error",
    "os_error": "io_error",
    "permission_error": "permission_denied",
    "timed_out": "timeout",
    "timeout_error": "timeout",
    "type_error": "invalid_input",
    "value_error": "invalid_input",
}
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ERROR_CODE_SEPARATOR = re.compile(r"[^a-z0-9]+")


def normalize_error_code(code: str | BaseException) -> str:
    """Return a stable ``snake_case`` code for a label or built-in exception."""

    if isinstance(code, BaseException):
        if isinstance(code, FileNotFoundError):
            return "not_found"
        if isinstance(code, FileExistsError):
            return "already_exists"
        if isinstance(code, PermissionError):
            return "permission_denied"
        if isinstance(code, (IsADirectoryError, NotADirectoryError)):
            return "invalid_path"
        if isinstance(code, TimeoutError):
            return "timeout"
        if isinstance(code, (TypeError, ValueError)):
            return "invalid_input"
        if isinstance(code, OSError):
            return "io_error"
        if isinstance(code, KeyboardInterrupt):
            return "interrupted"
        return "internal_error"
    if not isinstance(code, str):
        raise TypeError("error code must be a string or exception")
    words = _CAMEL_CASE_BOUNDARY.sub("_", code.strip()).casefold()
    normalized = _ERROR_CODE_SEPARATOR.sub("_", words).strip("_")
    if not normalized:
        raise ValueError("error code must not be empty")
    if normalized[0].isdigit():
        normalized = f"error_{normalized}"
    return _ERROR_CODE_ALIASES.get(normalized, normalized)


def _required_text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _json_value(value: object, *, context: str, active: set[int] | None = None) -> JsonValue:
    """Copy *value* into the strict JSON value domain."""

    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{context} contains a non-finite number")
        return float(value)

    stack = active if active is not None else set()
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in stack:
            raise ValueError(f"{context} contains a circular reference")
        stack.add(marker)
        try:
            result: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{context} contains a non-string object key: {key!r}")
                result[key] = _json_value(item, context=f"{context}.{key}", active=stack)
            return result
        finally:
            stack.remove(marker)

    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in stack:
            raise ValueError(f"{context} contains a circular reference")
        stack.add(marker)
        try:
            return [
                _json_value(item, context=f"{context}[{index}]", active=stack)
                for index, item in enumerate(value)
            ]
        finally:
            stack.remove(marker)

    raise TypeError(f"{context} contains a non-JSON value: {type(value).__name__}")


def _warnings(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError("warnings must be a sequence of strings")
    return [_required_text(value, name="warning") for value in values]


def _artifacts(values: Sequence[object]) -> list[JsonValue]:
    if isinstance(values, (str, bytes)):
        raise TypeError("artifacts must be a sequence of JSON values")
    return [_json_value(value, context=f"artifact[{index}]") for index, value in enumerate(values)]


def success(
    command: str,
    data: object = None,
    *,
    warnings: Sequence[str] = (),
    artifacts: Sequence[object] = (),
) -> SuccessEnvelope:
    """Build a successful, JSON-safe CLI response envelope."""

    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": True,
        "command": _required_text(command, name="command"),
        "data": _json_value(data, context="data"),
        "warnings": _warnings(warnings),
        "artifacts": _artifacts(artifacts),
    }


def failure(
    command: str,
    code: str | BaseException,
    message: str,
    *,
    details: object = None,
    warnings: Sequence[str] = (),
    artifacts: Sequence[object] = (),
) -> ErrorEnvelope:
    """Build a failed, JSON-safe CLI response envelope."""

    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": False,
        "command": _required_text(command, name="command"),
        "error": {
            "code": normalize_error_code(code),
            "message": _required_text(message, name="error message"),
            "details": _json_value(details, context="error details"),
        },
        "warnings": _warnings(warnings),
        "artifacts": _artifacts(artifacts),
    }


def serialize(envelope: ResponseEnvelope) -> str:
    """Serialize one response as deterministic, compact JSON followed by a newline."""

    validated = _json_value(envelope, context="CLI response")
    return (
        json.dumps(
            validated,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


__all__ = [
    "CLI_SCHEMA_VERSION",
    "ErrorEnvelope",
    "JsonValue",
    "ResponseEnvelope",
    "SuccessEnvelope",
    "failure",
    "normalize_error_code",
    "serialize",
    "success",
]
