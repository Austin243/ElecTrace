from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from electrace.cli_output import failure, success

_SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "electrace.cli.v1.schema.json"


def _schema() -> dict[str, Any]:
    payload = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _validator(schema: dict[str, Any]) -> Any | None:
    if importlib.util.find_spec("jsonschema") is None:
        return None
    jsonschema = importlib.import_module("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_cli_success_and_failure_envelopes_match_the_public_schema() -> None:
    schema = _schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("/schemas/electrace.cli.v1.schema.json")
    assert {branch["$ref"] for branch in schema["oneOf"]} == {
        "#/$defs/success",
        "#/$defs/failure",
    }
    validator = _validator(schema)
    if validator is None:
        return

    validator.validate(
        success(
            "workflow.validate",
            {"schema_version": "electrace.workflow-validation.v1", "jobs": []},
            warnings=("executable is not installed on this host",),
            artifacts=({"path": "workflow.yaml", "role": "manifest"},),
        )
    )
    validator.validate(
        failure(
            "workflow.validate",
            "invalid_input",
            "manifest has an invalid frame selector",
            details={"path": "workflow.yaml"},
        )
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "electrace.cli.v1",
            "ok": True,
            "command": "inspect",
            "warnings": [],
            "artifacts": [],
        },
        {
            "schema_version": "electrace.cli.v1",
            "ok": False,
            "command": "inspect",
            "data": {},
            "warnings": [],
            "artifacts": [],
        },
        {
            "schema_version": "electrace.cli.v0",
            "ok": True,
            "command": "inspect",
            "data": {},
            "warnings": [],
            "artifacts": [],
        },
    ],
)
def test_cli_schema_rejects_incomplete_or_mismatched_envelopes(
    payload: dict[str, Any],
) -> None:
    validator = _validator(_schema())
    if validator is None:
        return
    jsonschema = importlib.import_module("jsonschema")
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validator.validate(payload)
