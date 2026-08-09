from __future__ import annotations

import json

import pytest

from electrace.cli_output import CLI_SCHEMA_VERSION, failure, serialize, success


def test_success_and_failure_use_one_small_envelope() -> None:
    assert success("inspect", {"frames": 3}, warnings=("partial",)) == {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": True,
        "command": "inspect",
        "data": {"frames": 3},
        "warnings": ["partial"],
    }
    assert failure("inspect", "invalid_input", "bad input") == {
        "schema_version": CLI_SCHEMA_VERSION,
        "ok": False,
        "command": "inspect",
        "error": {
            "code": "invalid_input",
            "message": "bad input",
            "details": None,
        },
        "warnings": [],
    }


def test_serialize_emits_one_compact_document_and_rejects_nan() -> None:
    document = serialize(success("inspect", {"value": 1.0}))

    assert document.count("\n") == 1
    assert json.loads(document)["data"] == {"value": 1.0}
    with pytest.raises(ValueError):
        serialize(success("inspect", {"value": float("nan")}))
