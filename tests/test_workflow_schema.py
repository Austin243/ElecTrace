from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

from electrace.workflow import WorkflowManifest, load_workflow_manifest

_ROOT = Path(__file__).parents[1]
_SCHEMA_PATH = _ROOT / "schemas" / "electrace.workflow.v1.schema.json"
_EXAMPLE_PATH = _ROOT / "examples" / "electrace.workflow.yaml"


def _load_schema() -> dict[str, Any]:
    payload = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _validate_with_jsonschema_when_installed(
    schema: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Use the docs extra when present without making it a test dependency."""

    if importlib.util.find_spec("jsonschema") is None:
        return
    jsonschema = importlib.import_module("jsonschema")
    validator = jsonschema.Draft202012Validator
    validator.check_schema(schema)
    validator(schema).validate(payload)


def _reject_with_jsonschema_when_installed(schema: dict[str, Any], payload: dict[str, Any]) -> None:
    """Assert invalid examples are rejected when the optional validator exists."""

    if importlib.util.find_spec("jsonschema") is None:
        return
    jsonschema = importlib.import_module("jsonschema")
    validator = jsonschema.Draft202012Validator
    validator.check_schema(schema)
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validator(schema).validate(payload)


def _frame_schema_accepts(schema: dict[str, Any], value: str) -> bool:
    frame_schema = schema["properties"]["frames"]
    trimmed_pattern = schema["$defs"]["trimmedString"]["pattern"]
    if re.search(trimmed_pattern, value) is None:
        return False
    alternatives = frame_schema["allOf"][1]["oneOf"]
    accepted = any(
        alternative.get("const") == value
        or ("pattern" in alternative and re.search(alternative["pattern"], value) is not None)
        for alternative in alternatives
    )
    zero_step = frame_schema["allOf"][2]["not"]["pattern"]
    return accepted and re.search(zero_step, value) is None


def test_public_workflow_example_matches_runtime_and_schema_contract() -> None:
    schema = _load_schema()
    manifest = load_workflow_manifest(_EXAMPLE_PATH)
    payload = manifest.to_dict()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("/schemas/electrace.workflow.v1.schema.json")
    assert set(schema["required"]) == set(schema["properties"]) == set(payload)
    assert schema["additionalProperties"] is False
    assert set(schema["$defs"]["project"]["required"]) == set(payload["project"])
    assert schema["$defs"]["project"]["additionalProperties"] is False
    assert set(schema["$defs"]["project"]["properties"]["series_kind"]["enum"]) == {
        "auto",
        "neb",
        "ordered",
        "pressure",
        "static",
    }
    assert set(schema["$defs"]["step"]["properties"]["kind"]["enum"]) == {
        "bader",
        "baderkit",
        "critic2",
        "lobster",
        "vasp",
    }
    assert set(schema["$defs"]["scheduler"]["properties"]["kind"]["enum"]) == {
        "local",
        "slurm",
    }
    assert (
        schema["$defs"]["scheduler"]["allOf"][0]["then"]["properties"]["directives"][
            "maxProperties"
        ]
        == 0
    )
    directive_name = schema["$defs"]["schedulerDirectiveName"]
    assert directive_name["allOf"][0]["pattern"] == "^[a-z][a-z0-9-]*$"
    assert set(directive_name["allOf"][1]["not"]["enum"]) == {
        "command",
        "job-name",
        "script",
        "wrap",
    }
    directive_value = schema["$defs"]["schedulerDirectiveValue"]
    string_value = next(
        branch for branch in directive_value["oneOf"] if branch.get("type") == "string"
    )
    assert string_value["pattern"] == r"^[A-Za-z0-9_./,:@%+=\[\]*?{}~-]+$"
    assert "unique step ids" in schema["$comment"]

    assert payload["frames"] == "0,2:7:2"
    assert set(payload["executables"]) == {"vasp", "baderkit", "lobster"}
    assert payload["scheduler"]["kind"] == "slurm"
    assert payload["scheduler"]["directives"]["exclude"] == "node[1-8]"
    steps = {step["id"]: step for step in payload["steps"]}
    assert steps["badelf"]["kind"] == "baderkit"
    assert steps["badelf"]["capability"] == "elf_basins"
    assert steps["badelf"]["depends_on"] == ["vasp"]
    assert "template_dir" not in steps["badelf"]
    assert steps["lobster"]["kind"] == "lobster"
    assert steps["lobster"]["capability"] == "cohp"
    assert steps["lobster"]["depends_on"] == ["vasp"]

    # The canonical model result is strict JSON and validates against the full
    # public schema when the optional docs validator is installed.
    json.dumps(payload, allow_nan=False)
    _validate_with_jsonschema_when_installed(schema, payload)


@pytest.mark.parametrize(
    "selection",
    ["all", "0", "+0", "-0", "0:7", "::-1", "0,2:7:2", "0 , 2:7:2"],
)
def test_frame_selection_schema_accepts_every_representative_runtime_form(
    selection: str,
) -> None:
    schema = _load_schema()
    payload = load_workflow_manifest(_EXAMPLE_PATH).to_dict()
    payload["frames"] = selection

    assert _frame_schema_accepts(schema, selection)
    assert WorkflowManifest.from_dict(payload).frames == selection


@pytest.mark.parametrize(
    "selection",
    ["", " 0", "0 ", "all,0", "-1", "0::0", "0,,1", "0:1:2:3"],
)
def test_frame_selection_schema_rejects_representative_runtime_errors(
    selection: str,
) -> None:
    schema = _load_schema()
    payload = load_workflow_manifest(_EXAMPLE_PATH).to_dict()
    payload["frames"] = selection

    assert not _frame_schema_accepts(schema, selection)
    with pytest.raises((TypeError, ValueError)):
        WorkflowManifest.from_dict(payload)


def test_scheduler_schema_matches_local_and_slurm_runtime_rules() -> None:
    schema = _load_schema()
    payload = load_workflow_manifest(_EXAMPLE_PATH).to_dict()

    local = copy.deepcopy(payload)
    local["scheduler"] = {"kind": "local", "directives": {}}
    assert WorkflowManifest.from_dict(local).scheduler.kind == "local"
    _validate_with_jsonschema_when_installed(schema, local)

    local_with_directive = copy.deepcopy(local)
    local_with_directive["scheduler"]["directives"] = {"nodes": 1}
    with pytest.raises(ValueError, match="must be empty"):
        WorkflowManifest.from_dict(local_with_directive)

    unsafe_slurm = copy.deepcopy(payload)
    unsafe_slurm["scheduler"]["directives"] = {"wrap": "python payload.py"}
    with pytest.raises(ValueError, match="not allowed to embed executable content"):
        WorkflowManifest.from_dict(unsafe_slurm)
    _reject_with_jsonschema_when_installed(schema, unsafe_slurm)

    for directives in (
        {"cpus_per_task": 8},
        {"job-name": "embedded-name"},
        {"comment": "contains whitespace"},
        {"comment": ""},
    ):
        invalid = copy.deepcopy(payload)
        invalid["scheduler"]["directives"] = directives
        with pytest.raises(ValueError):
            WorkflowManifest.from_dict(invalid)
        _reject_with_jsonschema_when_installed(schema, invalid)

    for value in ("gpu:a100", 2, 2.5, True, False, None):
        valid = copy.deepcopy(payload)
        valid["scheduler"]["directives"] = {"constraint": value}
        assert WorkflowManifest.from_dict(valid).scheduler.directives["constraint"] == value
        _validate_with_jsonschema_when_installed(schema, valid)
