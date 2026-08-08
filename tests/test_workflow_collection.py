from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from electrace.config import EventSettings, MatchingSettings, load_config
from electrace.io.descriptors import DescriptorError
from electrace.io.vasp import read_vasp_energy
from electrace.workflow.collection import (
    COLLECTION_RECORD,
    COLLECTION_SCHEMA_VERSION,
    analyze_collected_workflow,
    collect_workflow_outputs,
)
from electrace.workflow.staging import (
    StagedWorkflow,
    descriptor_outputs,
    stage_workflow,
)

POSCAR = """\
H collection fixture
1.0
1 0 0
0 1 0
0 0 1
H
1
Direct
0 0 0
"""

ACF = """\
    # X Y Z CHARGE MIN DIST ATOMIC VOL
    1 0.0 0.0 0.0 1.0 0.0 10.0
"""


def _stage_fixture(
    tmp_path: Path,
    *,
    duplicate_bader: bool = False,
    write_outputs: bool = True,
) -> tuple[Path, StagedWorkflow]:
    images = tmp_path / "images"
    for order, directory_index in enumerate((8, 9)):
        frame = images / f"{directory_index:02d}"
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")
        energy = -10.0 + order * 0.25
        (frame / "OSZICAR").write_text(f" 1 F= {energy} E0= {energy} d E = 0.0\n", encoding="utf-8")
    steps = [
        {
            "id": "charges",
            "kind": "bader",
            "executable": "bader",
            "depends_on": [],
            "options": {},
            "resources": {},
        }
    ]
    if duplicate_bader:
        steps.append(
            {
                "id": "charges_again",
                "kind": "bader",
                "executable": "bader",
                "depends_on": [],
                "options": {},
                "resources": {},
            }
        )
    document = {
        "schema_version": "electrace.workflow.v1",
        "project": {
            "root": "images",
            "series_kind": "neb",
            "workspace": "workspace",
            "structure": "CONTCAR",
            "fallback": "POSCAR",
        },
        "frames": "all",
        "executables": {"bader": {"path": "/opt/bader"}},
        "scheduler": {"kind": "local", "directives": {}},
        "steps": steps,
    }
    manifest = tmp_path / "workflow.yaml"
    manifest.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    staged = stage_workflow(manifest)
    if write_outputs:
        for paths in descriptor_outputs(staged).values():
            for path in paths:
                path.write_text(ACF, encoding="utf-8")
    return manifest, staged


def _first_descriptor(staged: StagedWorkflow) -> Path:
    return next(iter(descriptor_outputs(staged).values()))[0]


def test_collect_copies_selected_evidence_and_emits_strict_config(tmp_path: Path) -> None:
    manifest, staged = _stage_fixture(tmp_path)
    source_text = (tmp_path / "images/08/POSCAR").read_text(encoding="utf-8")

    artifacts = collect_workflow_outputs(manifest)

    analysis_root = staged.workspace / "analysis"
    assert artifacts.config_path == analysis_root / "electrace.toml"
    assert artifacts.input_root == analysis_root / "input"
    assert artifacts.descriptor_root == analysis_root / "descriptors"
    for order, (source_label, energy) in enumerate((("08", -10.0), ("09", -9.75))):
        label = f"{order:06d}-{source_label}"
        assert (artifacts.input_root / label / "CONTCAR").read_text(encoding="utf-8") == POSCAR
        assert read_vasp_energy(artifacts.input_root / label / "OSZICAR") == energy
        assert (artifacts.descriptor_root / label / "ACF.dat").read_text(encoding="utf-8") == ACF
    assert (tmp_path / "images/08/POSCAR").read_text(encoding="utf-8") == source_text
    assert not (tmp_path / "images/analysis").exists()

    config = load_config(artifacts.config_path)
    assert config.series_kind == "ordered"
    assert config.inputs.root == "input"
    assert config.inputs.descriptor_dir == "descriptors"
    assert config.inputs.descriptor_source == "external"
    assert config.matching == MatchingSettings()
    assert config.events == EventSettings()
    assert config.outputs.directory == "results"
    assert config.metadata["workflow_manifest_sha256"] == staged.manifest_sha256
    assert config.metadata["workflow_frame_indices"] == [0, 1]

    marker = json.loads((analysis_root / COLLECTION_RECORD).read_text(encoding="utf-8"))
    assert marker["schema_version"] == COLLECTION_SCHEMA_VERSION
    assert marker["workflow_workspace_record"] == str(staged.record_path)
    assert {record["role"] for record in marker["files"]} == {
        "configuration",
        "descriptor",
        "structure",
        "synthetic_energy",
    }
    assert set(artifacts.files) == {path for path in analysis_root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("failure", ["missing", "empty", "symlink"])
def test_collect_hard_fails_missing_empty_or_symlinked_descriptor_outputs(
    tmp_path: Path, failure: str
) -> None:
    manifest, staged = _stage_fixture(tmp_path, write_outputs=failure != "missing")
    descriptor = _first_descriptor(staged)
    external = tmp_path / "external-acf"
    if failure == "empty":
        descriptor.write_bytes(b"")
    elif failure == "symlink":
        descriptor.unlink()
        external.write_text(ACF, encoding="utf-8")
        descriptor.symlink_to(external)

    with pytest.raises((FileNotFoundError, ValueError), match=failure):
        collect_workflow_outputs(manifest)

    assert not (staged.workspace / "analysis").exists()
    if failure == "symlink":
        assert external.read_text(encoding="utf-8") == ACF


def test_collect_parses_every_descriptor_before_publishing(tmp_path: Path) -> None:
    manifest, staged = _stage_fixture(tmp_path)
    _first_descriptor(staged).write_text("not an ACF file\n", encoding="utf-8")

    with pytest.raises(DescriptorError, match="no atomic charge records"):
        collect_workflow_outputs(manifest)

    assert not (staged.workspace / "analysis").exists()


def test_collect_rejects_descriptor_destination_collisions(tmp_path: Path) -> None:
    manifest, staged = _stage_fixture(tmp_path, duplicate_bader=True)

    with pytest.raises(ValueError, match="descriptor output collision"):
        collect_workflow_outputs(manifest)

    assert not (staged.workspace / "analysis").exists()


def test_overwrite_replaces_only_a_marker_owned_analysis(tmp_path: Path) -> None:
    manifest, staged = _stage_fixture(tmp_path)
    artifacts = collect_workflow_outputs(manifest)
    analysis_root = artifacts.config_path.parent
    sentinel = analysis_root / "old-managed-file"
    sentinel.write_text("old", encoding="utf-8")

    with pytest.raises(FileExistsError, match="overwrite=True"):
        collect_workflow_outputs(manifest)
    collect_workflow_outputs(manifest, overwrite=True)
    assert not sentinel.exists()

    marker = analysis_root / COLLECTION_RECORD
    marker.unlink()
    protected = analysis_root / "user-file"
    protected.write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="unmarked"):
        collect_workflow_outputs(manifest, overwrite=True)
    assert protected.read_text(encoding="utf-8") == "preserve"
    assert staged.record_path.is_file()


def test_failed_overwrite_preflight_preserves_prior_collection(tmp_path: Path) -> None:
    manifest, staged = _stage_fixture(tmp_path)
    artifacts = collect_workflow_outputs(manifest)
    config_hash = artifacts.config_path.read_bytes()
    _first_descriptor(staged).unlink()

    with pytest.raises(FileNotFoundError, match="missing"):
        collect_workflow_outputs(manifest, overwrite=True)

    assert artifacts.config_path.read_bytes() == config_hash
    assert (artifacts.config_path.parent / COLLECTION_RECORD).is_file()


def test_collect_rejects_symlinked_selected_structure(tmp_path: Path) -> None:
    manifest, staged = _stage_fixture(tmp_path)
    structure = staged.frames[0].structure
    external = tmp_path / "external-structure"
    external.write_text(POSCAR, encoding="utf-8")
    structure.unlink()
    structure.symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        collect_workflow_outputs(manifest)

    assert external.read_text(encoding="utf-8") == POSCAR
    assert not (staged.workspace / "analysis").exists()


def test_collect_rejects_tampered_output_path_outside_job_directory(tmp_path: Path) -> None:
    manifest, staged = _stage_fixture(tmp_path)
    external = tmp_path / "external" / "ACF.dat"
    external.parent.mkdir()
    external.write_text(ACF, encoding="utf-8")
    record = json.loads(staged.record_path.read_text(encoding="utf-8"))
    descriptor_id = staged.descriptor_job_ids[0]
    job = next(item for item in record["jobs"] if item["id"] == descriptor_id)
    job["expected_outputs"] = [str(external)]
    staged.record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes its managed root"):
        collect_workflow_outputs(manifest)

    assert external.read_text(encoding="utf-8") == ACF


def test_collect_then_analyze_reuses_valid_collection_without_force(tmp_path: Path) -> None:
    manifest, _ = _stage_fixture(tmp_path)
    collected = collect_workflow_outputs(manifest)

    analysis = analyze_collected_workflow(manifest)

    assert analysis.output_directory == collected.config_path.parent / "results"
    assert (analysis.output_directory / "analysis.json").is_file()
    assert (analysis.output_directory / "manifest.json").is_file()
    with pytest.raises(FileExistsError, match="overwrite=True"):
        analyze_collected_workflow(manifest)

    replaced = analyze_collected_workflow(manifest, overwrite=True)
    assert (replaced.output_directory / "analysis.json").is_file()
