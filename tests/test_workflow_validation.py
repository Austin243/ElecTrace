import json
from pathlib import Path

import pytest
import yaml

from electrace.workflow import stage_workflow, validate_workflow


@pytest.fixture
def valid_manifest(workflow_manifest):
    return workflow_manifest(
        templates={
            "template": {
                "INCAR": "LELF = .TRUE.\n",
                "KPOINTS": "Gamma\n",
                "POTCAR": "test fixture\n",
            }
        },
        steps=[
            {
                "id": "elf",
                "kind": "vasp",
                "executable": "vasp",
                "template_dir": "template",
                "options": {"expected_outputs": ["ELFCAR", "CHGCAR"]},
            }
        ],
    )


def test_validate_is_read_only_and_returns_resolved_plan(tmp_path, valid_manifest):
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    plan = validate_workflow(valid_manifest)

    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before
    assert not plan.workspace.exists()
    assert [frame.label for frame in plan.selected_frames] == ["00", "01"]
    assert [job.id for job in plan.resolved_jobs] == ["f000000.elf", "f000001.elf"]
    assert plan.resolved_jobs[0].argv == ("/bin/true",)


def test_validate_and_stage_share_job_plan(valid_manifest):
    plan = validate_workflow(valid_manifest)
    staged = stage_workflow(valid_manifest)

    assert [frame.to_dict() for frame in plan.selected_frames] == [
        frame.to_dict() for frame in staged.frames
    ]
    assert [job.to_dict() for job in plan.resolved_jobs] == [job.to_dict() for job in staged.jobs]


def test_validate_and_stage_reject_missing_vasp_input(tmp_path, valid_manifest):
    (tmp_path / "template/KPOINTS").unlink()
    for operation in (validate_workflow, stage_workflow):
        with pytest.raises(FileNotFoundError, match=r"required inputs.*KPOINTS"):
            operation(valid_manifest)
    assert not (tmp_path / "workspace").exists()


def test_descriptor_template_cannot_prepopulate_output(workflow_manifest):
    manifest = workflow_manifest(
        executables={"baderkit": "/bin/true"},
        templates={"template": {"bader.json": "stale\n"}},
        steps=[
            {
                "id": "charges",
                "kind": "baderkit",
                "executable": "baderkit",
                "capability": "bader_charges",
                "template_dir": "template",
            }
        ],
    )
    for operation in (validate_workflow, stage_workflow):
        with pytest.raises(ValueError, match=r"collide.*bader.json"):
            operation(manifest)


def test_vasp_restart_input_may_also_be_an_output(tmp_path, valid_manifest):
    payload = yaml.safe_load(valid_manifest.read_text(encoding="utf-8"))
    payload["steps"][0]["options"]["required_inputs"] = [
        "INCAR",
        "KPOINTS",
        "POSCAR",
        "POTCAR",
        "CHGCAR",
    ]
    valid_manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    (tmp_path / "template/CHGCAR").write_text("restart\n", encoding="utf-8")

    validate_workflow(valid_manifest)
    staged = stage_workflow(valid_manifest)
    assert all(
        (job.cwd / "CHGCAR").read_text(encoding="utf-8") == "restart\n" for job in staged.jobs
    )


def test_validation_payload_matches_public_schema(valid_manifest):
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(__file__).parents[1] / "schemas/electrace.workflow-validation.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    payload = validate_workflow(valid_manifest).to_dict()
    jsonschema.Draft202012Validator(schema).validate(payload)
    del payload["resolved_jobs"][0]["argv"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(payload)


def test_validate_rejects_malformed_manifest_without_writing(tmp_path):
    manifest = tmp_path / "workflow.yaml"
    manifest.write_text("schema_version: electrace.workflow.v1\n", encoding="utf-8")

    with pytest.raises(ValueError):
        validate_workflow(manifest)
    assert list(tmp_path.iterdir()) == [manifest]
