from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import electrace.workflow.manifest as manifest_module
from electrace.io.discovery import FrameSource
from electrace.workflow import (
    WORKFLOW_SCHEMA_VERSION,
    ExecutableSpec,
    FrameSelectionError,
    SchedulerSpec,
    WorkflowManifest,
    WorkflowProject,
    WorkflowStep,
    dump_starter_manifest,
    load_workflow_manifest,
    parse_frame_selection,
    resolve_frame_selection,
    resolve_selected_frames,
    starter_workflow_manifest,
)


def _manifest_data() -> dict[str, Any]:
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "project": {
            "root": "./images",
            "series_kind": "neb",
            "workspace": ".electrace-work",
            "structure": "CONTCAR",
            "fallback": "POSCAR",
        },
        "frames": "0,2:5:2",
        "executables": {
            "vasp": {"path": "vasp_std", "argv_prefix": ["srun", "--cpu-bind=cores"]},
            "bader": {"path": "/opt/bader/bader"},
        },
        "scheduler": {
            "kind": "slurm",
            "directives": {
                "partition": "compute",
                "time": "01:00:00",
                "nodes": 1,
                "exclusive": False,
            },
        },
        "steps": [
            {
                "id": "vasp",
                "kind": "vasp",
                "executable": "vasp",
                "template_dir": "templates/vasp",
                "options": {"restart": False, "encut": 600.0},
                "resources": {"ranks": 8},
            },
            {
                "id": "bader",
                "kind": "bader",
                "executable": "bader",
                "capability": "atomic_charge",
                "depends_on": ["vasp"],
                "options": {"reference": "CHGCAR_sum"},
            },
        ],
    }


def _write_data(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    return path


def _frame(tmp_path: Path, index: int) -> FrameSource:
    directory = tmp_path / f"{index:02d}"
    return FrameSource(
        index=index,
        label=directory.name,
        directory=directory,
        structure=directory / "CONTCAR",
        metadata={"directory_index": index + 8},
    )


def test_valid_manifest_loads_to_typed_json_ready_models(tmp_path: Path) -> None:
    source = _write_data(tmp_path / "workflow.yaml", _manifest_data())

    manifest = load_workflow_manifest(source)

    assert manifest.schema_version == WORKFLOW_SCHEMA_VERSION
    assert manifest.project == WorkflowProject(
        root="./images",
        series_kind="neb",
        workspace=".electrace-work",
        structure="CONTCAR",
        fallback="POSCAR",
    )
    assert manifest.project.root == "./images"
    assert manifest.executables["vasp"].argv_prefix == ("srun", "--cpu-bind=cores")
    assert manifest.scheduler.directives["nodes"] == 1
    assert manifest.steps[1].kind == "bader"
    assert manifest.steps[1].depends_on == ("vasp",)
    encoded = json.dumps(manifest.to_dict(), allow_nan=False)
    assert '"schema_version": "electrace.workflow.v1"' in encoded


def test_programmatic_dataclasses_enforce_and_serialize_the_contract() -> None:
    manifest = WorkflowManifest(
        schema_version=WORKFLOW_SCHEMA_VERSION,
        project=WorkflowProject(".", "static", "work", "CONTCAR", "POSCAR"),
        frames="1:5:2",
        executables={"critic2": ExecutableSpec("critic2", ("srun",))},
        scheduler=SchedulerSpec("local"),
        steps=(
            WorkflowStep(
                id="critic2",
                kind="critic2",
                executable="critic2",
                capability="qtaim_critical_points",
                options={"nested": {"thresholds": [0.1, 0.2]}},
            ),
        ),
    )

    assert manifest.to_dict()["executables"] == {
        "critic2": {"path": "critic2", "argv_prefix": ["srun"]}
    }
    assert manifest.to_dict()["steps"][0]["depends_on"] == []


def test_starter_dump_round_trips_and_preserves_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "workflow.yaml"
    written = dump_starter_manifest(target, root="./images")

    assert written == target
    assert (
        load_workflow_manifest(target).to_dict()
        == starter_workflow_manifest(root="./images").to_dict()
    )
    starter = load_workflow_manifest(target)
    assert starter.steps[0].options["incar_overrides"] == {
        "LELF": True,
        "LCHARG": True,
        "NSW": 0,
    }
    with pytest.raises(FileExistsError):
        dump_starter_manifest(target)

    dump_starter_manifest(target, root="./replacement", overwrite=True)
    assert load_workflow_manifest(target).project.root == "./replacement"


def test_starter_force_refuses_to_follow_manifest_symlink(tmp_path: Path) -> None:
    protected = tmp_path / "protected.yaml"
    protected.write_text("user content\n", encoding="utf-8")
    target = tmp_path / "workflow.yaml"
    target.symlink_to(protected)

    with pytest.raises(ValueError, match="symlinked workflow manifest"):
        dump_starter_manifest(target, overwrite=True)

    assert target.is_symlink()
    assert protected.read_text(encoding="utf-8") == "user content\n"


@pytest.mark.parametrize(
    ("section", "unknown_key"),
    [
        ("root", "matcing"),
        ("project", "series_knd"),
        ("executable", "arguments"),
        ("scheduler", "queue"),
        ("step", "after"),
    ],
)
def test_unknown_fixed_keys_are_rejected(tmp_path: Path, section: str, unknown_key: str) -> None:
    data = _manifest_data()
    if section == "root":
        data[unknown_key] = {}
    elif section == "project":
        data["project"][unknown_key] = "neb"
    elif section == "executable":
        data["executables"]["vasp"][unknown_key] = []
    elif section == "scheduler":
        data["scheduler"][unknown_key] = "compute"
    else:
        data["steps"][0][unknown_key] = "vasp"

    with pytest.raises(ValueError, match="unknown keys"):
        load_workflow_manifest(_write_data(tmp_path / f"{section}.yaml", data))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.update(schema_version="electrace.workflow.v2"), "schema_version"),
        (lambda data: data.update(frames=[0, 1]), "frames must be a string"),
        (
            lambda data: data["project"].update(series_kind=True),
            "project.series_kind must be a string",
        ),
        (
            lambda data: data["executables"]["vasp"].update(argv_prefix="srun"),
            "argv_prefix must be a sequence",
        ),
        (
            lambda data: data["scheduler"].update(directives=[]),
            "scheduler.directives must be a mapping",
        ),
        (
            lambda data: data["steps"][1].update(depends_on="vasp"),
            "depends_on must be a sequence",
        ),
    ],
)
def test_wrong_manifest_field_types_are_rejected(tmp_path: Path, mutate: Any, message: str) -> None:
    data = _manifest_data()
    mutate(data)

    with pytest.raises((TypeError, ValueError), match=message):
        load_workflow_manifest(_write_data(tmp_path / "wrong-type.yaml", data))


def test_all_declared_step_kinds_are_accepted(tmp_path: Path) -> None:
    for kind in ("vasp", "bader", "baderkit", "critic2", "lobster"):
        data = _manifest_data()
        data["executables"] = {kind: {"path": kind}}
        data["steps"] = [{"id": kind, "kind": kind, "executable": kind}]
        manifest = load_workflow_manifest(_write_data(tmp_path / f"{kind}.yaml", data))
        assert manifest.steps[0].kind == kind


def test_dependency_identity_and_executable_references_are_strict(tmp_path: Path) -> None:
    cases: list[tuple[dict[str, Any], str]] = []

    duplicate = _manifest_data()
    duplicate["steps"][1]["id"] = "vasp"
    cases.append((duplicate, "duplicate ids"))

    executable = _manifest_data()
    executable["steps"][1]["executable"] = "missing"
    cases.append((executable, "unknown executable"))

    unknown = _manifest_data()
    unknown["steps"][1]["depends_on"] = ["density"]
    cases.append((unknown, "unknown dependencies"))

    forward = _manifest_data()
    forward["steps"][0]["depends_on"] = ["bader"]
    forward["steps"][1]["depends_on"] = []
    cases.append((forward, "forward dependencies"))

    cycle = _manifest_data()
    cycle["steps"][0]["depends_on"] = ["bader"]
    cycle["steps"][1]["depends_on"] = ["vasp"]
    cases.append((cycle, "dependency cycle"))

    for index, (data, message) in enumerate(cases):
        with pytest.raises(ValueError, match=message):
            load_workflow_manifest(_write_data(tmp_path / f"dependency-{index}.yaml", data))


def test_duplicate_yaml_mapping_keys_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.yaml"
    source.write_text(
        "schema_version: electrace.workflow.v1\nschema_version: electrace.workflow.v1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate mapping key 'schema_version'"):
        load_workflow_manifest(source)


def test_manifest_byte_limit_is_enforced_during_the_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_data(tmp_path / "large.yaml", _manifest_data())
    monkeypatch.setattr(manifest_module, "MAX_WORKFLOW_MANIFEST_BYTES", 64)

    with pytest.raises(ValueError, match="exceeds 64 bytes"):
        load_workflow_manifest(source)


def test_yaml_only_and_non_finite_values_are_rejected(tmp_path: Path) -> None:
    date_data = _manifest_data()
    date_text = json.dumps(date_data, indent=2).replace(
        '"options": {\n        "restart": false,',
        '"options": {\n        "submitted": 2026-08-08,\n        "restart": false,',
        1,
    )
    date_source = tmp_path / "date.yaml"
    date_source.write_text(date_text, encoding="utf-8")
    with pytest.raises(ValueError, match="non-JSON-compatible value of type date"):
        load_workflow_manifest(date_source)

    non_finite = _manifest_data()
    non_finite_text = json.dumps(non_finite, indent=2).replace('"encut": 600.0', '"encut": .nan')
    non_finite_source = tmp_path / "nan.yaml"
    non_finite_source.write_text(non_finite_text, encoding="utf-8")
    with pytest.raises(ValueError, match="finite for JSON serialization"):
        load_workflow_manifest(non_finite_source)

    set_data = _manifest_data()
    set_text = json.dumps(set_data, indent=2).replace(
        '"reference": "CHGCAR_sum"', '"reference": !!set {CHGCAR: null}'
    )
    set_source = tmp_path / "set.yaml"
    set_source.write_text(set_text, encoding="utf-8")
    with pytest.raises(ValueError, match="non-JSON-compatible value of type set"):
        load_workflow_manifest(set_source)

    alias_source = tmp_path / "alias.yaml"
    alias_source.write_text("value: &shared [1, 2]\ncopy: *shared\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aliases are not allowed"):
        load_workflow_manifest(alias_source)


def test_programmatic_recursive_json_values_are_rejected() -> None:
    recursive: list[Any] = []
    recursive.append(recursive)

    with pytest.raises(ValueError, match="cyclic sequence"):
        WorkflowStep(
            id="vasp",
            kind="vasp",
            executable="vasp",
            options={"recursive": recursive},
        )


def test_control_characters_and_command_directives_are_rejected(tmp_path: Path) -> None:
    newline = _manifest_data()
    newline["steps"][0]["options"]["note"] = "safe\n--wrap=payload"
    with pytest.raises(ValueError, match="control characters or newlines"):
        load_workflow_manifest(_write_data(tmp_path / "newline.yaml", newline))

    wrap = _manifest_data()
    wrap["scheduler"]["directives"]["wrap"] = "arbitrary command"
    with pytest.raises(ValueError, match="not allowed to embed executable content"):
        load_workflow_manifest(_write_data(tmp_path / "wrap.yaml", wrap))

    directive = _manifest_data()
    directive["scheduler"]["directives"]["--partition"] = "compute"
    with pytest.raises(ValueError, match="unsafe characters"):
        load_workflow_manifest(_write_data(tmp_path / "directive.yaml", directive))

    underscore = _manifest_data()
    underscore["scheduler"]["directives"]["cpus_per_task"] = 2
    with pytest.raises(ValueError, match="unsafe characters"):
        load_workflow_manifest(_write_data(tmp_path / "underscore.yaml", underscore))

    job_name = _manifest_data()
    job_name["scheduler"]["directives"]["job-name"] = "embedded-name"
    with pytest.raises(ValueError, match="reserved"):
        load_workflow_manifest(_write_data(tmp_path / "job-name.yaml", job_name))

    unsafe_value = _manifest_data()
    unsafe_value["scheduler"]["directives"]["comment"] = "contains whitespace"
    with pytest.raises(ValueError, match="unsafe string value"):
        load_workflow_manifest(_write_data(tmp_path / "directive-value.yaml", unsafe_value))

    local = _manifest_data()
    local["scheduler"]["kind"] = "local"
    with pytest.raises(ValueError, match="must be empty"):
        load_workflow_manifest(_write_data(tmp_path / "local.yaml", local))


def test_parse_frame_selection_supports_indices_and_python_slices() -> None:
    assert parse_frame_selection("all", 5) == (0, 1, 2, 3, 4)
    assert parse_frame_selection("0, 2:5:2", 5) == (0, 2, 4)
    assert parse_frame_selection("-3:", 5) == (2, 3, 4)
    assert parse_frame_selection("::-2", 5) == (4, 2, 0)
    assert parse_frame_selection("1:99", 5) == (1, 2, 3, 4)


@pytest.mark.parametrize(
    "selection",
    ["", "ALL", "all,1", "1,,2", "frame-1", "-1", "1:2:0", "1:2:3:4"],
)
def test_malformed_frame_selections_are_rejected(selection: str) -> None:
    with pytest.raises(FrameSelectionError):
        parse_frame_selection(selection, 4)


def test_unresolvable_or_duplicate_frame_selections_are_rejected() -> None:
    with pytest.raises(FrameSelectionError, match="outside"):
        parse_frame_selection("4", 4)
    with pytest.raises(FrameSelectionError, match="more than once"):
        parse_frame_selection("0:2,1", 4)
    with pytest.raises(FrameSelectionError, match="no frames"):
        parse_frame_selection("2:2", 4)
    with pytest.raises(TypeError, match="integer"):
        parse_frame_selection("all", True)


def test_selection_resolves_canonical_frame_sources(tmp_path: Path) -> None:
    frames = tuple(_frame(tmp_path, index) for index in range(5))

    selected = resolve_frame_selection("0,2:5:2", frames)
    wrapped = resolve_selected_frames(frames, "::-2")

    assert [frame.index for frame in selected] == [0, 2, 4]
    assert [frame.index for frame in wrapped] == [4, 2, 0]
    assert [frame.metadata["directory_index"] for frame in selected] == [8, 10, 12]


def test_selection_rejects_noncanonical_frame_source_order(tmp_path: Path) -> None:
    frames = [_frame(tmp_path, 0), _frame(tmp_path, 2)]

    with pytest.raises(FrameSelectionError, match="canonical order"):
        resolve_frame_selection("all", frames)


def test_nested_input_mapping_is_copied_before_validation(tmp_path: Path) -> None:
    data = _manifest_data()
    original = copy.deepcopy(data)

    manifest = load_workflow_manifest(_write_data(tmp_path / "copy.yaml", data))
    manifest.steps[0].options["encut"] = 700

    assert data == original
    assert manifest.to_dict()["steps"][0]["options"]["encut"] == 700
