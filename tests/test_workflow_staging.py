from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import electrace.workflow.staging as staging_module
from electrace.workflow.staging import (
    WORKSPACE_RECORD,
    descriptor_outputs,
    load_staged_workflow,
    stage_workflow,
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


def _workflow_fixture(tmp_path: Path, *, workspace: str = "workspace") -> Path:
    images = tmp_path / "images"
    for index in range(3):
        frame = images / f"{index:02d}"
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")
    template = tmp_path / "templates" / "elf"
    template.mkdir(parents=True)
    (template / "INCAR").write_text("ENCUT = 400\nLELF = .FALSE.\n", encoding="utf-8")
    (template / "KPOINTS").write_text("Gamma\n0\nGamma\n1 1 1\n0 0 0\n", encoding="utf-8")
    (template / "POTCAR").write_text("private fixture\n", encoding="utf-8")
    document = {
        "schema_version": "electrace.workflow.v1",
        "project": {
            "root": "images",
            "series_kind": "neb",
            "workspace": workspace,
            "structure": "CONTCAR",
            "fallback": "POSCAR",
        },
        "frames": "0:3:2",
        "executables": {
            "vasp": {"path": "/opt/vasp_std", "argv_prefix": ["srun"]},
            "baderkit": {"path": "/opt/baderkit"},
        },
        "scheduler": {"kind": "local", "directives": {}},
        "steps": [
            {
                "id": "vasp_elf",
                "kind": "vasp",
                "executable": "vasp",
                "template_dir": "templates/elf",
                "depends_on": [],
                "options": {
                    "outputs": ["ELFCAR", "CHGCAR", "CONTCAR"],
                    "incar_overrides": {"LELF": True, "LCHARG": True},
                },
                "resources": {},
            },
            {
                "id": "elf_basins",
                "kind": "baderkit",
                "executable": "baderkit",
                "capability": "elf_basins",
                "depends_on": ["vasp_elf"],
                "options": {},
                "resources": {},
            },
        ],
    }
    manifest = tmp_path / "electrace-workflow.yaml"
    manifest.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return manifest


def test_stage_selects_frames_without_touching_sources_and_builds_job_dag(
    tmp_path: Path,
) -> None:
    manifest = _workflow_fixture(tmp_path)

    staged = stage_workflow(manifest)

    assert [frame.index for frame in staged.frames] == [0, 2]
    assert [job.id for job in staged.jobs] == [
        "f000000.vasp_elf",
        "f000000.elf_basins",
        "f000002.vasp_elf",
        "f000002.elf_basins",
    ]
    first_vasp, first_badelf = staged.jobs[:2]
    assert first_vasp.argv == ("srun", "/opt/vasp_std")
    assert first_badelf.argv == (
        "/opt/baderkit",
        "badelf",
        "CHGCAR",
        "ELFCAR",
    )
    assert first_badelf.dependencies == (first_vasp.id,)
    assert first_badelf.expected_outputs == (first_badelf.cwd / "badelf.json",)
    assert (first_badelf.cwd / "CHGCAR").is_symlink()
    assert (first_badelf.cwd / "ELFCAR").is_symlink()
    assert (first_badelf.cwd / "CHGCAR").readlink() == Path("../vasp_elf/CHGCAR")
    incar = (first_vasp.cwd / "INCAR").read_text(encoding="utf-8")
    assert "LELF = .TRUE." in incar
    assert "LCHARG = .TRUE." in incar
    assert (tmp_path / "images/00/POSCAR").read_text(encoding="utf-8") == POSCAR
    assert staged.record_path.name == WORKSPACE_RECORD
    assert load_staged_workflow(manifest).to_dict() == staged.to_dict()
    assert descriptor_outputs(staged) == {
        "f000000.elf_basins": (first_badelf.cwd / "badelf.json",),
        "f000002.elf_basins": (staged.jobs[3].cwd / "badelf.json",),
    }


def test_stage_refuses_unmarked_workspace_even_with_force(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = workspace / "user-file"
    protected.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="unmarked"):
        stage_workflow(manifest, overwrite=True)

    assert protected.read_text(encoding="utf-8") == "keep"


def test_failed_force_restaging_preserves_the_previous_workspace(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    staged = stage_workflow(manifest)
    sentinel = staged.workspace / "previous-run"
    sentinel.write_text("preserve", encoding="utf-8")
    (tmp_path / "templates/elf/INCAR").unlink()

    with pytest.raises(FileNotFoundError, match="INCAR template"):
        stage_workflow(manifest, overwrite=True)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert load_staged_workflow(manifest).to_dict() == staged.to_dict()


def test_staged_record_is_size_bounded_and_bound_to_the_manifest_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _workflow_fixture(tmp_path)
    staged = stage_workflow(manifest)
    record = json.loads(staged.record_path.read_text(encoding="utf-8"))
    record["jobs"][0]["argv"] = ["/tmp/tampered-command"]
    staged.record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest-derived plan"):
        load_staged_workflow(manifest)

    staged = stage_workflow(manifest, overwrite=True)
    monkeypatch.setattr(staging_module, "MAX_WORKSPACE_RECORD_BYTES", 128)
    with pytest.raises(ValueError, match="exceeds 128 bytes"):
        load_staged_workflow(manifest)


@pytest.mark.parametrize(
    "tamper",
    ("source_directory", "structure", "energy_ev", "label", "index", "order"),
)
def test_staged_record_frame_provenance_is_bound_to_manifest_discovery(
    tmp_path: Path,
    tamper: str,
) -> None:
    manifest = _workflow_fixture(tmp_path)
    staged = stage_workflow(manifest)
    record = json.loads(staged.record_path.read_text(encoding="utf-8"))
    frames = record["frames"]
    if tamper in {"source_directory", "structure"}:
        frames[0][tamper] = frames[1][tamper]
    elif tamper == "energy_ev":
        frames[0][tamper] = 123.5
    elif tamper == "label":
        frames[0][tamper] = "substituted-frame"
    elif tamper == "index":
        frames[0][tamper] = 99
    else:
        frames.reverse()
    staged.record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="frame provenance"):
        load_staged_workflow(manifest)


def test_manifest_change_requires_restaging(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    stage_workflow(manifest)
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after staging"):
        load_staged_workflow(manifest)


def test_workspace_inside_source_tree_is_rejected(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path, workspace="images/.electrace-work")

    with pytest.raises(ValueError, match=r"outside project\.root"):
        stage_workflow(manifest)


def test_source_inside_managed_workspace_is_rejected_without_data_loss(tmp_path: Path) -> None:
    manifest = _workflow_fixture(tmp_path)
    staged = stage_workflow(manifest)
    nested_source = staged.workspace / "images"
    for index in range(2):
        frame = nested_source / f"{index:02d}"
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")
    sentinel = nested_source / "00/POSCAR"

    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    document["project"]["root"] = "workspace/images"
    manifest.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match=r"must not contain"):
        stage_workflow(manifest, overwrite=True)

    assert sentinel.read_text(encoding="utf-8") == POSCAR
    assert staged.record_path.is_file()


def test_symlinked_source_root_cannot_hide_workspace_inside_real_source(
    tmp_path: Path,
) -> None:
    manifest = _workflow_fixture(tmp_path, workspace="images/generated-workspace")
    source_alias = tmp_path / "source-alias"
    source_alias.symlink_to(tmp_path / "images", target_is_directory=True)
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    document["project"]["root"] = source_alias.name
    manifest.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match=r"outside project\.root"):
        stage_workflow(manifest)

    assert not (tmp_path / "images/generated-workspace").exists()
