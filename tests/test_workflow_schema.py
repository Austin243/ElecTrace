from __future__ import annotations

import json
from pathlib import Path

import pytest

from electrace.workflow import WorkflowManifest, load_workflow_manifest

ROOT = Path(__file__).parents[1]


def test_public_workflow_example_matches_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "schemas/electrace.workflow.v1.schema.json").read_text())
    payload = load_workflow_manifest(ROOT / "examples/electrace.workflow.yaml").to_dict()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(payload, schema)


def test_schema_and_runtime_reject_unknown_root_keys() -> None:
    schema = json.loads((ROOT / "schemas/electrace.workflow.v1.schema.json").read_text())
    payload = load_workflow_manifest(ROOT / "examples/electrace.workflow.yaml").to_dict()
    payload["unknown"] = True
    with pytest.raises(ValueError):
        WorkflowManifest.from_dict(payload)
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)
