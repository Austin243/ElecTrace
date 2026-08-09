from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from electrace.workflow import (
    FrameSelectionError,
    WorkflowManifest,
    WorkflowManifestError,
    dump_starter_manifest,
    load_workflow_manifest,
    parse_frame_selection,
    starter_workflow_manifest,
)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "electrace.workflow.v1",
        "project": {
            "root": "images",
            "series_kind": "neb",
            "workspace": "work",
            "structure": "CONTCAR",
            "fallback": "POSCAR",
        },
        "frames": "0,2:5:2",
        "executables": {"vasp": {"path": "vasp_std"}, "baderkit": {"path": "baderkit"}},
        "scheduler": {"kind": "local", "directives": {}},
        "steps": [
            {"id": "vasp", "kind": "vasp", "executable": "vasp"},
            {
                "id": "bader",
                "kind": "baderkit",
                "executable": "baderkit",
                "capability": "bader_charges",
                "depends_on": ["vasp"],
            },
        ],
    }


def _write(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_manifest_round_trip_and_starter_write(tmp_path: Path) -> None:
    source = _write(tmp_path / "workflow.yaml", _manifest())
    manifest = load_workflow_manifest(source)
    assert WorkflowManifest.from_dict(manifest.to_dict()) == manifest
    assert [step.id for step in manifest.steps] == ["vasp", "bader"]

    starter_path = tmp_path / "starter.yaml"
    assert dump_starter_manifest(starter_path, root="/images") == starter_path
    starter = load_workflow_manifest(starter_path)
    assert starter == starter_workflow_manifest(root="/images")
    with pytest.raises(FileExistsError):
        dump_starter_manifest(starter_path)
    dump_starter_manifest(starter_path, overwrite=True)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(schema_version="wrong"),
        lambda data: data["steps"].append(dict(data["steps"][0])),
        lambda data: data["steps"][1].update(executable="missing"),
        lambda data: data["steps"][0].update(depends_on=["bader"]),
        lambda data: data.update(extra=True),
    ],
)
def test_manifest_rejects_invalid_contract(
    tmp_path: Path, mutation: Callable[[dict[str, object]], None]
) -> None:
    data = _manifest()
    mutation(data)
    with pytest.raises((TypeError, ValueError)):
        load_workflow_manifest(_write(tmp_path / "bad.yaml", data))


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    alias = tmp_path / "bad.yaml"
    alias.write_text("value: [unclosed\n", encoding="utf-8")
    with pytest.raises(WorkflowManifestError):
        load_workflow_manifest(alias)


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "duplicate.yaml"
    manifest.write_text("schema_version: first\nschema_version: second\n", encoding="utf-8")
    with pytest.raises(WorkflowManifestError, match=r"duplicate.*schema_version"):
        load_workflow_manifest(manifest)


def test_frame_selection_supports_indices_slices_and_reverse_order() -> None:
    assert parse_frame_selection("all", 5) == (0, 1, 2, 3, 4)
    assert parse_frame_selection("0,2:6:2", 6) == (0, 2, 4)
    assert parse_frame_selection("4:0:-2", 5) == (4, 2)


@pytest.mark.parametrize("selection", ["", "all,0", "-1", "0::0", "0,0", "9"])
def test_frame_selection_rejects_ambiguous_or_empty_requests(selection: str) -> None:
    with pytest.raises((FrameSelectionError, ValueError)):
        parse_frame_selection(selection, 5)
