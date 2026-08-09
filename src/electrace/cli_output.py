"""Small, versioned JSON envelopes for the ElecTrace command line."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

CLI_SCHEMA_VERSION: Final = "electrace.cli.v1"


def success(
    command: str,
    data: object = None,
    *,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Return the common successful response shape."""

    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "data": data,
        "warnings": list(warnings),
    }


def failure(
    command: str,
    code: str,
    message: str,
    *,
    details: object = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Return the common failed response shape."""

    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "error": {"code": code, "message": message, "details": details},
        "warnings": list(warnings),
    }


def serialize(envelope: Mapping[str, object]) -> str:
    """Serialize one compact JSON document followed by a newline."""

    return (
        json.dumps(
            envelope,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


__all__ = ["CLI_SCHEMA_VERSION", "failure", "serialize", "success"]
