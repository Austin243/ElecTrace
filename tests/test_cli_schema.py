from __future__ import annotations

import json
from pathlib import Path

import pytest

from electrace.cli_output import failure, success


def test_public_cli_schema_accepts_both_envelopes() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    path = Path(__file__).parents[1] / "schemas" / "electrace.cli.v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    validator.validate(success("inspect", {"frames": 2}))
    validator.validate(failure("inspect", "invalid_input", "bad input"))
