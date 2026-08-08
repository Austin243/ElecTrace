from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from electrace.workflow.operations import (
    prepare_slurm_scripts,
    run_staged_workflow,
    submit_staged_workflow,
    workflow_status,
)
from electrace.workflow.staging import stage_workflow

POSCAR = """\
H
1
1 0 0
0 1 0
0 0 1
H
1
Direct
0 0 0
"""


def _manifest(tmp_path: Path, scheduler: str) -> Path:
    images = tmp_path / "images"
    for label in ("00", "01"):
        frame = images / label
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")
    executable = tmp_path / "fake_vasp.py"
    executable.write_text(
        "#!/usr/bin/env python3\nfrom pathlib import Path\nPath('CHGCAR').write_text('density')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    directives = {"partition": "compute", "exclude": "node[1-8]"} if scheduler == "slurm" else {}
    payload = {
        "schema_version": "electrace.workflow.v1",
        "project": {
            "root": "images",
            "series_kind": "neb",
            "workspace": "workspace",
            "structure": "CONTCAR",
            "fallback": "POSCAR",
        },
        "frames": "all",
        "executables": {"vasp": {"path": "./fake_vasp.py"}},
        "scheduler": {"kind": scheduler, "directives": directives},
        "steps": [
            {
                "id": "density",
                "kind": "vasp",
                "executable": "vasp",
                "depends_on": [],
                "options": {
                    "required_inputs": ["POSCAR"],
                    "expected_outputs": ["CHGCAR"],
                },
                "resources": {},
            }
        ],
    }
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_local_run_is_confirmation_gated_and_records_status(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "local")
    staged = stage_workflow(manifest)

    with pytest.raises(PermissionError, match="confirmed=True"):
        run_staged_workflow(manifest)

    results = run_staged_workflow(manifest, confirmed=True, max_parallel=2)

    assert len(results) == 2
    assert all(result.succeeded for result in results)
    assert workflow_status(manifest)["complete"] is True
    record = json.loads((staged.workspace / "run-results.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in record["results"]] == ["success", "success"]


def test_slurm_prepare_uses_exact_name_and_exclusion_then_submits(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)

    scripts = prepare_slurm_scripts(manifest, job_name="inpaint_test")

    assert len(scripts) == 2
    first = scripts[0].read_text(encoding="utf-8")
    assert "#SBATCH --job-name=inpaint_test" in first
    assert "#SBATCH --exclude=node[1-8]" in first
    fake_submit = tmp_path / "submit.py"
    fake_submit.write_text(
        "import pathlib, sys\n"
        "assert pathlib.Path(sys.argv[1]).is_file()\n"
        "print('Submitted batch job 789')\n",
        encoding="utf-8",
    )
    submissions = submit_staged_workflow(
        manifest,
        job_name="inpaint_test",
        submit_command=(sys.executable, str(fake_submit)),
        confirmed=True,
        overwrite_scripts=True,
    )

    assert set(submissions.values()) == {"789"}
    assert (staged.workspace / "slurm/submissions.json").is_file()


def test_slurm_prepare_preflights_all_scripts_before_writing(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)
    script_directory = staged.workspace / "slurm"
    script_directory.mkdir()
    first = script_directory / "frame-000000.slurm"
    second = script_directory / "frame-000001.slurm"
    second.write_text("user-owned sentinel\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match=r"frame-000001\.slurm"):
        prepare_slurm_scripts(manifest)

    assert not first.exists()
    assert second.read_text(encoding="utf-8") == "user-owned sentinel\n"


def test_scheduler_kind_separates_local_execution_and_submission(tmp_path: Path) -> None:
    local = _manifest(tmp_path / "local", "local")
    stage_workflow(local)
    with pytest.raises(ValueError, match=r"scheduler\.kind: slurm"):
        prepare_slurm_scripts(local)

    slurm = _manifest(tmp_path / "slurm", "slurm")
    stage_workflow(slurm)
    with pytest.raises(ValueError, match=r"scheduler\.kind: local"):
        run_staged_workflow(slurm, confirmed=True)
