import json
import sys

import pytest

from electrace.workflow.operations import (
    SlurmSubmissionError,
    prepare_slurm_scripts,
    run_staged_workflow,
    submit_staged_workflow,
    workflow_status,
)
from electrace.workflow.staging import stage_workflow


def _manifest(factory, base, scheduler):
    script = base / "fake.py"
    base.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "from pathlib import Path; Path('CHGCAR').write_text('density')", encoding="utf-8"
    )
    return factory(
        base=base,
        scheduler=scheduler,
        executables={"vasp": sys.executable},
        step_options={"arguments": [str(script)]},
    )


def test_local_run_is_confirmed_and_reports_status(tmp_path, workflow_manifest):
    manifest = _manifest(workflow_manifest, tmp_path, "local")
    stage_workflow(manifest)
    with pytest.raises(PermissionError, match="confirmed=True"):
        run_staged_workflow(manifest)

    results = run_staged_workflow(manifest, confirmed=True, max_parallel=2)

    assert all(result.succeeded for result in results)
    assert workflow_status(manifest)["complete"] is True


def test_slurm_happy_path_and_partial_submission_are_recorded(tmp_path, workflow_manifest):
    happy = _manifest(workflow_manifest, tmp_path / "happy", "slurm")
    stage_workflow(happy)
    scripts = prepare_slurm_scripts(happy, job_name="inpaint_test")
    assert len(scripts) == 2
    assert "#SBATCH --exclude=node[1-8]" in scripts[0].read_text(encoding="utf-8")
    submissions = submit_staged_workflow(
        happy,
        job_name="inpaint_test",
        submit_command=(sys.executable, "-c", "print('Submitted batch job 789')"),
        confirmed=True,
        overwrite_scripts=True,
    )
    assert set(submissions.values()) == {"789"}
    record = scripts[0].parent / "submissions.json"
    assert json.loads(record.read_text(encoding="utf-8"))["status"] == "complete"
    with pytest.raises(FileExistsError, match="prior Slurm submission record"):
        submit_staged_workflow(happy, confirmed=True, overwrite_scripts=True)

    partial = _manifest(workflow_manifest, tmp_path / "partial", "slurm")
    stage_workflow(partial)
    counter = tmp_path / "partial/count"
    code = (
        "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
        "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); "
        "print('Submitted batch job 456') if n == 0 else raise_error()"
    )
    with pytest.raises(SlurmSubmissionError) as raised:
        submit_staged_workflow(
            partial,
            submit_command=(sys.executable, "-c", code, str(counter)),
            confirmed=True,
        )
    error = raised.value
    assert len(error.partial_submissions) == 1
    payload = json.loads(error.record_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["submissions"] == dict(error.partial_submissions)
