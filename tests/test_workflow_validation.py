from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from electrace.workflow import (
    WORKFLOW_VALIDATION_SCHEMA_VERSION,
    WorkflowValidation,
    stage_workflow,
    validate_workflow,
)

POSCAR = """\
H
1.0
1 0 0
0 1 0
0 0 1
H
1
Direct
0 0 0
"""


def _write_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _workflow_fixture(tmp_path: Path) -> Path:
    images = tmp_path / "images"
    for index in range(3):
        frame = images / f"{index:02d}"
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")

    template = tmp_path / "templates" / "vasp-elf"
    template.mkdir(parents=True)
    (template / "INCAR").write_text("ENCUT = 400\nLELF = .FALSE.\n", encoding="utf-8")
    (template / "KPOINTS").write_text("Gamma\n0\nGamma\n1 1 1\n0 0 0\n", encoding="utf-8")
    (template / "POTCAR").write_text("test-only placeholder\n", encoding="utf-8")

    wrapper = _write_executable(tmp_path / "bin" / "launcher")
    vasp = _write_executable(tmp_path / "bin" / "vasp_std")
    baderkit = _write_executable(tmp_path / "bin" / "baderkit")
    payload = {
        "schema_version": "electrace.workflow.v1",
        "project": {
            "root": "images",
            "series_kind": "neb",
            "workspace": "generated/workspace",
            "structure": "CONTCAR",
            "fallback": "POSCAR",
        },
        "frames": "0,2",
        "executables": {
            "vasp": {"path": str(vasp), "argv_prefix": [str(wrapper)]},
            "baderkit": {"path": str(baderkit)},
        },
        "scheduler": {
            "kind": "slurm",
            "directives": {"partition": "compute", "time": "00:30:00", "nodes": 1},
        },
        "steps": [
            {
                "id": "vasp_elf",
                "kind": "vasp",
                "executable": "vasp",
                "template_dir": "templates/vasp-elf",
                "options": {
                    "arguments": ["--gamma"],
                    "expected_outputs": ["CHGCAR", "ELFCAR", "CONTCAR"],
                    "incar_overrides": {"LELF": True, "LCHARG": True},
                    "environment": {"OMP_NUM_THREADS": "2"},
                },
            },
            {
                "id": "elf_basins",
                "kind": "baderkit",
                "executable": "baderkit",
                "capability": "elf_basins",
                "depends_on": ["vasp_elf"],
            },
        ],
    }
    manifest = tmp_path / "workflow.yaml"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return manifest


def _tree_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    records: list[tuple[object, ...]] = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root))
        status = path.lstat()
        if path.is_symlink():
            records.append((relative, "symlink", os.readlink(path), status.st_mtime_ns))
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            records.append(
                (relative, "file", status.st_mode, status.st_size, status.st_mtime_ns, digest)
            )
        else:
            records.append((relative, "directory", status.st_mode, status.st_mtime_ns))
    return tuple(records)


def _normalized(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _normalized(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root), "<TMP>")
    return value


def test_validate_workflow_snapshots_plan_without_creating_workspace(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    before = _tree_snapshot(tmp_path)

    validation = validate_workflow(manifest)

    assert isinstance(validation, WorkflowValidation)
    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "generated").exists()
    payload = validation.to_dict()
    json.dumps(payload, allow_nan=False)
    assert _normalized(payload, tmp_path) == {
        "schema_version": WORKFLOW_VALIDATION_SCHEMA_VERSION,
        "manifest": "<TMP>/workflow.yaml",
        "workspace": "<TMP>/generated/workspace",
        "selected_frames": [
            {
                "index": 0,
                "label": "00",
                "analysis_label": "000000-00",
                "source_directory": "<TMP>/images/00",
                "structure": "<TMP>/images/00/POSCAR",
                "workspace_directory": "<TMP>/generated/workspace/frames/000000-00",
                "energy_ev": None,
            },
            {
                "index": 2,
                "label": "02",
                "analysis_label": "000001-02",
                "source_directory": "<TMP>/images/02",
                "structure": "<TMP>/images/02/POSCAR",
                "workspace_directory": "<TMP>/generated/workspace/frames/000002-02",
                "energy_ev": None,
            },
        ],
        "resolved_jobs": [
            {
                "id": "f000000.vasp_elf",
                "frame_index": 0,
                "frame_label": "00",
                "task": "vasp_elf",
                "cwd": "<TMP>/generated/workspace/frames/000000-00/vasp_elf",
                "argv": ["<TMP>/bin/launcher", "<TMP>/bin/vasp_std", "--gamma"],
                "stdin": None,
                "stdout_path": (
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/vasp.stdout.log"
                ),
                "stderr_path": (
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/vasp.stdout.log.stderr.log"
                ),
                "required_inputs": [
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/INCAR",
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/KPOINTS",
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/POSCAR",
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/POTCAR",
                ],
                "expected_outputs": [
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/CHGCAR",
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/ELFCAR",
                    "<TMP>/generated/workspace/frames/000000-00/vasp_elf/CONTCAR",
                ],
                "dependencies": [],
                "environment": {"OMP_NUM_THREADS": "2"},
            },
            {
                "id": "f000000.elf_basins",
                "frame_index": 0,
                "frame_label": "00",
                "task": "elf_basins",
                "cwd": "<TMP>/generated/workspace/frames/000000-00/elf_basins",
                "argv": ["<TMP>/bin/baderkit", "badelf", "CHGCAR", "ELFCAR"],
                "stdin": None,
                "stdout_path": (
                    "<TMP>/generated/workspace/frames/000000-00/elf_basins/baderkit.stdout.log"
                ),
                "stderr_path": (
                    "<TMP>/generated/workspace/frames/000000-00/elf_basins/"
                    "baderkit.stdout.log.stderr.log"
                ),
                "required_inputs": [
                    "<TMP>/generated/workspace/frames/000000-00/elf_basins/ELFCAR",
                    "<TMP>/generated/workspace/frames/000000-00/elf_basins/CHGCAR",
                ],
                "expected_outputs": [
                    "<TMP>/generated/workspace/frames/000000-00/elf_basins/badelf.json"
                ],
                "dependencies": ["f000000.vasp_elf"],
                "environment": {},
            },
            {
                "id": "f000002.vasp_elf",
                "frame_index": 2,
                "frame_label": "02",
                "task": "vasp_elf",
                "cwd": "<TMP>/generated/workspace/frames/000002-02/vasp_elf",
                "argv": ["<TMP>/bin/launcher", "<TMP>/bin/vasp_std", "--gamma"],
                "stdin": None,
                "stdout_path": (
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/vasp.stdout.log"
                ),
                "stderr_path": (
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/vasp.stdout.log.stderr.log"
                ),
                "required_inputs": [
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/INCAR",
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/KPOINTS",
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/POSCAR",
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/POTCAR",
                ],
                "expected_outputs": [
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/CHGCAR",
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/ELFCAR",
                    "<TMP>/generated/workspace/frames/000002-02/vasp_elf/CONTCAR",
                ],
                "dependencies": [],
                "environment": {"OMP_NUM_THREADS": "2"},
            },
            {
                "id": "f000002.elf_basins",
                "frame_index": 2,
                "frame_label": "02",
                "task": "elf_basins",
                "cwd": "<TMP>/generated/workspace/frames/000002-02/elf_basins",
                "argv": ["<TMP>/bin/baderkit", "badelf", "CHGCAR", "ELFCAR"],
                "stdin": None,
                "stdout_path": (
                    "<TMP>/generated/workspace/frames/000002-02/elf_basins/baderkit.stdout.log"
                ),
                "stderr_path": (
                    "<TMP>/generated/workspace/frames/000002-02/elf_basins/"
                    "baderkit.stdout.log.stderr.log"
                ),
                "required_inputs": [
                    "<TMP>/generated/workspace/frames/000002-02/elf_basins/ELFCAR",
                    "<TMP>/generated/workspace/frames/000002-02/elf_basins/CHGCAR",
                ],
                "expected_outputs": [
                    "<TMP>/generated/workspace/frames/000002-02/elf_basins/badelf.json"
                ],
                "dependencies": ["f000002.vasp_elf"],
                "environment": {},
            },
        ],
        "scheduler": {
            "kind": "slurm",
            "directives": {"partition": "compute", "time": "00:30:00", "nodes": 1},
        },
        "warnings": [],
    }


def test_validation_jobs_match_subsequent_staging_exactly(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    validation = validate_workflow(manifest)
    assert not validation.workspace.parent.exists()

    staged = stage_workflow(manifest)

    assert [job.to_dict() for job in validation.resolved_jobs] == [
        job.to_dict() for job in staged.jobs
    ]
    assert [frame.to_dict() for frame in validation.selected_frames] == [
        frame.to_dict() for frame in staged.frames
    ]


def test_read_only_incar_template_validates_and_stages_with_overrides(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    template = tmp_path / "templates/vasp-elf/INCAR"
    template.chmod(0o444)

    validation = validate_workflow(manifest)
    staged = stage_workflow(manifest)

    assert validation.resolved_jobs[0].to_dict() == staged.jobs[0].to_dict()
    staged_incar = staged.jobs[0].cwd / "INCAR"
    assert "LELF = .TRUE." in staged_incar.read_text(encoding="utf-8")
    assert stat.S_IMODE(template.stat().st_mode) == 0o444


def test_validation_rejects_missing_staged_input_without_writing(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    (tmp_path / "templates/vasp-elf/POTCAR").unlink()
    before = _tree_snapshot(tmp_path)

    with pytest.raises(FileNotFoundError, match="required staged inputs: POTCAR"):
        validate_workflow(manifest)

    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "generated").exists()


def test_validation_rejects_unreadable_template_without_writing(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    template = tmp_path / "templates/vasp-elf/POTCAR"
    original = _tree_snapshot(tmp_path)
    original_mode = stat.S_IMODE(template.stat().st_mode)
    template.chmod(0)

    try:
        try:
            template.open("rb").close()
        except PermissionError:
            pass
        else:
            pytest.skip("filesystem permissions do not make mode-000 files unreadable")
        with pytest.raises(PermissionError):
            validate_workflow(manifest)
    finally:
        template.chmod(original_mode)

    assert _tree_snapshot(tmp_path) == original
    assert not (tmp_path / "generated").exists()


def test_validation_rejects_source_inside_workspace_without_writing(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    nested_source = tmp_path / "generated/workspace/images"
    for index in range(2):
        frame = nested_source / f"{index:02d}"
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["project"]["root"] = "generated/workspace/images"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match=r"must not contain"):
        validate_workflow(manifest)

    assert _tree_snapshot(tmp_path) == before


def test_validation_rejects_template_output_collision_without_writing(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    template = tmp_path / "templates" / "badelf"
    template.mkdir()
    (template / "badelf.json").write_text("stale output\n", encoding="utf-8")
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["steps"][1]["template_dir"] = "templates/badelf"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    before = _tree_snapshot(tmp_path)

    with pytest.raises(ValueError, match=r"collide with declared outputs: badelf.json"):
        validate_workflow(manifest)

    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "generated").exists()


def test_validation_rejects_linked_lobster_input_like_staging(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    template = tmp_path / "templates/vasp-elf"
    (template / "lobsterin").write_text("cohpGenerator from 1.0 to 2.0\n", encoding="utf-8")
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["executables"]["lobster"] = dict(payload["executables"]["baderkit"])
    payload["steps"][0]["options"]["expected_outputs"].append("WAVECAR")
    payload["steps"][1] = {
        "id": "bonding",
        "kind": "lobster",
        "executable": "lobster",
        "capability": "cohp",
        "depends_on": ["vasp_elf"],
        "options": {"input_links": {"lobsterin": {"step": "vasp_elf", "file": "lobsterin"}}},
    }
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="regular reviewed lobsterin"):
        validate_workflow(manifest)
    with pytest.raises(FileNotFoundError, match="regular reviewed lobsterin"):
        stage_workflow(manifest)

    assert not (tmp_path / "generated/workspace").exists()


def test_validation_rejects_collection_descriptor_basename_collision(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["steps"].append(
        {
            "id": "elf_basins_again",
            "kind": "baderkit",
            "executable": "baderkit",
            "capability": "elf_basins",
            "depends_on": ["vasp_elf"],
        }
    )
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match=r"descriptor output collision.*badelf.json"):
        validate_workflow(manifest)

    assert not (tmp_path / "generated").exists()


def test_validation_reads_incar_during_override_preflight(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    (tmp_path / "templates/vasp-elf/INCAR").write_bytes(b"ENCUT = 400\n\xff")

    with pytest.raises(UnicodeDecodeError):
        validate_workflow(manifest)

    assert not (tmp_path / "generated").exists()


def test_workflow_validation_payload_matches_its_public_schema(tmp_path: Path) -> None:
    schema_path = (
        Path(__file__).parents[1] / "schemas" / "electrace.workflow-validation.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == (WORKFLOW_VALIDATION_SCHEMA_VERSION)
    assert set(schema["required"]) == set(schema["properties"])
    if importlib.util.find_spec("jsonschema") is None:
        return
    jsonschema = importlib.import_module("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(
        validate_workflow(_workflow_fixture(tmp_path)).to_dict()
    )
