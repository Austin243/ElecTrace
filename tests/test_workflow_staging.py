import shutil

import pytest
import yaml

from electrace.workflow import validate_workflow
from electrace.workflow.staging import load_staged_workflow, stage_workflow


def test_stage_and_reload_preserve_source(tmp_path, poscar_text, workflow_manifest):
    manifest = workflow_manifest()
    staged = stage_workflow(manifest)

    assert [job.id for job in staged.jobs] == ["f000000.density", "f000001.density"]
    assert staged.jobs[0].required_inputs == (staged.jobs[0].cwd / "POSCAR",)
    assert (tmp_path / "images/00/POSCAR").read_text(encoding="utf-8") == poscar_text
    assert load_staged_workflow(manifest).to_dict() == staged.to_dict()


def test_stage_refuses_source_overlap_and_unmarked_overwrite(tmp_path, workflow_manifest):
    with pytest.raises(ValueError, match="must not overlap"):
        stage_workflow(workflow_manifest(base=tmp_path / "overlap", workspace="images/work"))

    manifest = workflow_manifest(base=tmp_path / "unmarked")
    workspace = tmp_path / "unmarked/workspace"
    workspace.mkdir()
    sentinel = workspace / "keep"
    sentinel.write_text("user data", encoding="utf-8")
    with pytest.raises(ValueError, match="unmarked"):
        stage_workflow(manifest, overwrite=True)
    assert sentinel.read_text(encoding="utf-8") == "user data"


def test_validate_and_stage_warn_when_names_do_not_set_the_frame_order(tmp_path, workflow_manifest):
    manifest = workflow_manifest()
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["project"]["series_kind"] = "ordered"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    shutil.move(tmp_path / "images/00", tmp_path / "images/IS")
    shutil.move(tmp_path / "images/01", tmp_path / "images/FS")

    expected = "natural order of their names (FS, IS)"
    assert any(expected in warning for warning in validate_workflow(manifest).warnings)
    assert any(expected in warning for warning in stage_workflow(manifest).warnings)


def test_numeric_workflow_frames_get_no_order_warning(workflow_manifest):
    manifest = workflow_manifest()

    assert not any("natural order" in item for item in validate_workflow(manifest).warnings)
    assert stage_workflow(manifest).warnings == ()
