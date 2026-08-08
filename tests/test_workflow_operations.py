from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import electrace.workflow.operations as workflow_operations
from electrace.cli import app
from electrace.workflow.operations import (
    SlurmSubmissionError,
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
runner = CliRunner()


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
    attempt_count = tmp_path / "attempt-count"
    fake_submit = tmp_path / "submit.py"
    fake_submit.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "counter = Path(sys.argv[1])\n"
        "attempt = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(attempt + 1))\n"
        "assert Path(sys.argv[2]).is_file()\n"
        "print('Submitted batch job 789')\n",
        encoding="utf-8",
    )
    submissions = submit_staged_workflow(
        manifest,
        job_name="inpaint_test",
        submit_command=(sys.executable, str(fake_submit), str(attempt_count)),
        confirmed=True,
        overwrite_scripts=True,
    )

    assert set(submissions.values()) == {"789"}
    record = json.loads((staged.workspace / "slurm/submissions.json").read_text(encoding="utf-8"))
    assert record["status"] == "complete"
    assert record["failed_script"] is None
    assert record["submissions"] == submissions
    record_before_retry = (staged.workspace / "slurm/submissions.json").read_bytes()
    scripts = sorted((staged.workspace / "slurm").glob("*.slurm"))
    scripts_before_retry = {path: path.read_bytes() for path in scripts}

    with pytest.raises(FileExistsError, match="prior Slurm submission record"):
        submit_staged_workflow(
            manifest,
            job_name="must_not_replace_inpaint_test",
            submit_command=(sys.executable, str(fake_submit), str(attempt_count)),
            confirmed=True,
            overwrite_scripts=True,
        )

    assert attempt_count.read_text(encoding="utf-8") == "2"
    assert (staged.workspace / "slurm/submissions.json").read_bytes() == record_before_retry
    assert {path: path.read_bytes() for path in scripts} == scripts_before_retry


def test_failed_script_preparation_releases_empty_submission_reservation(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)

    with pytest.raises(ValueError, match="job_name must be"):
        submit_staged_workflow(
            manifest,
            job_name="invalid job name",
            submit_command="must-not-run",
            confirmed=True,
            overwrite_scripts=True,
        )

    assert not (staged.workspace / "slurm/submissions.json").exists()


def test_slurm_submission_failure_records_accepted_jobs_before_retry(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)
    attempt_count = tmp_path / "attempt-count"
    fake_submit = tmp_path / "submit-once.py"
    fake_submit.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "counter = Path(sys.argv[1])\n"
        "attempt = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(attempt + 1))\n"
        "if attempt == 0:\n"
        "    print('Submitted batch job 789')\n"
        "else:\n"
        "    print('scheduler unavailable', file=sys.stderr)\n"
        "    raise SystemExit(7)\n",
        encoding="utf-8",
    )

    with pytest.raises(SlurmSubmissionError) as raised:
        submit_staged_workflow(
            manifest,
            job_name="inpaint_test",
            submit_command=(sys.executable, str(fake_submit), str(attempt_count)),
            confirmed=True,
            overwrite_scripts=True,
        )

    record_path = staged.workspace / "slurm/submissions.json"
    first_script = staged.workspace / "slurm/frame-000000.slurm"
    second_script = staged.workspace / "slurm/frame-000001.slurm"
    expected_submissions = {str(first_script): "789"}
    error = raised.value
    assert error.phase == "submission"
    assert error.failed_script == second_script
    assert dict(error.partial_submissions) == expected_submissions
    assert error.record_path == record_path
    assert error.submission_complete is False
    assert isinstance(error.__cause__, RuntimeError)
    assert "inspect" in str(error)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record == {
        "failed_script": str(second_script),
        "job_name": "inpaint_test",
        "manifest_sha256": staged.manifest_sha256,
        "schema_version": "electrace.slurm-submissions.v1",
        "status": "failed",
        "submissions": expected_submissions,
    }
    record_before_retry = record_path.read_bytes()

    with pytest.raises(FileExistsError, match="prior Slurm submission record"):
        submit_staged_workflow(
            manifest,
            job_name="inpaint_test",
            submit_command=(sys.executable, str(fake_submit), str(attempt_count)),
            confirmed=True,
            overwrite_scripts=True,
        )

    assert attempt_count.read_text(encoding="utf-8") == "2"
    assert record_path.read_bytes() == record_before_retry


def test_first_slurm_submission_failure_records_no_job_ids(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)
    fake_submit = tmp_path / "submit-never.py"
    fake_submit.write_text(
        "import sys\nprint('rejected', file=sys.stderr)\nraise SystemExit(3)\n",
        encoding="utf-8",
    )

    with pytest.raises(SlurmSubmissionError) as raised:
        submit_staged_workflow(
            manifest,
            submit_command=(sys.executable, str(fake_submit)),
            confirmed=True,
            overwrite_scripts=True,
        )

    first_script = staged.workspace / "slurm/frame-000000.slurm"
    record_path = staged.workspace / "slurm/submissions.json"
    assert raised.value.failed_script == first_script
    assert raised.value.phase == "submission"
    assert dict(raised.value.partial_submissions) == {}
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["failed_script"] == str(first_script)
    assert record["submissions"] == {}


def test_workflow_submit_json_reports_partial_submission_without_blind_retry(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)
    counter = tmp_path / "submit-count"
    fake_submit = tmp_path / "submit-partially.py"
    fake_submit.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"counter = Path({str(counter)!r})\n"
        "attempt = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(attempt + 1))\n"
        "if attempt == 0:\n"
        "    print('Submitted batch job 789')\n"
        "else:\n"
        "    print('scheduler unavailable', file=sys.stderr)\n"
        "    raise SystemExit(7)\n",
        encoding="utf-8",
    )
    fake_submit.chmod(0o700)

    result = runner.invoke(
        app,
        [
            "workflow",
            "submit",
            str(manifest),
            "--job-name",
            "inpaint_test",
            "--submit-command",
            str(fake_submit),
            "--yes",
            "--force",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    assert "\x1b" not in result.stdout
    payload = json.loads(result.stdout)
    first_script = staged.workspace / "slurm/frame-000000.slurm"
    second_script = staged.workspace / "slurm/frame-000001.slurm"
    record_path = staged.workspace / "slurm/submissions.json"
    assert payload["command"] == "workflow.submit"
    assert payload["error"]["code"] == "slurm_submission_failed"
    assert payload["error"]["details"] == {
        "failed_script": str(second_script),
        "partial_submissions": {str(first_script): "789"},
        "phase": "submission",
        "record_path": str(record_path),
        "submission_complete": False,
    }
    assert payload["artifacts"][-1] == {
        "path": str(record_path),
        "role": "slurm_submission_record",
    }


def test_record_write_failure_after_accept_exposes_unrecorded_job_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)
    fake_submit = tmp_path / "submit.py"
    fake_submit.write_text("print('Submitted batch job 789')\n", encoding="utf-8")
    real_write = workflow_operations.atomic_write_json

    def fail_first_accepted_record(path: Path, payload: object) -> Path:
        if (
            path.name == "submissions.json"
            and isinstance(payload, dict)
            and len(payload["submissions"]) == 1
        ):
            raise OSError("simulated disk failure")
        return real_write(path, payload)

    monkeypatch.setattr(
        workflow_operations,
        "atomic_write_json",
        fail_first_accepted_record,
    )

    with pytest.raises(SlurmSubmissionError) as raised:
        submit_staged_workflow(
            manifest,
            submit_command=(sys.executable, str(fake_submit)),
            confirmed=True,
            overwrite_scripts=True,
        )

    first_script = staged.workspace / "slurm/frame-000000.slurm"
    error = raised.value
    assert error.phase == "recording"
    assert error.failed_script is None
    assert dict(error.partial_submissions) == {str(first_script): "789"}
    assert error.submission_complete is False
    assert isinstance(error.__cause__, OSError)
    record = json.loads(error.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "submitting"
    assert record["submissions"] == {}


def test_final_record_write_failure_reports_all_jobs_were_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest(tmp_path, "slurm")
    staged = stage_workflow(manifest)
    attempt_count = tmp_path / "attempt-count"
    fake_submit = tmp_path / "submit.py"
    fake_submit.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "counter = Path(sys.argv[1])\n"
        "attempt = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(attempt + 1))\n"
        "print(f'Submitted batch job {789 + attempt}')\n",
        encoding="utf-8",
    )
    real_write = workflow_operations.atomic_write_json

    def fail_complete_record(path: Path, payload: object) -> Path:
        if (
            path.name == "submissions.json"
            and isinstance(payload, dict)
            and payload["status"] == "complete"
        ):
            raise OSError("simulated disk failure")
        return real_write(path, payload)

    monkeypatch.setattr(workflow_operations, "atomic_write_json", fail_complete_record)

    with pytest.raises(SlurmSubmissionError) as raised:
        submit_staged_workflow(
            manifest,
            submit_command=(sys.executable, str(fake_submit), str(attempt_count)),
            confirmed=True,
            overwrite_scripts=True,
        )

    first_script = staged.workspace / "slurm/frame-000000.slurm"
    second_script = staged.workspace / "slurm/frame-000001.slurm"
    accepted = {str(first_script): "789", str(second_script): "790"}
    error = raised.value
    assert error.phase == "recording"
    assert error.failed_script is None
    assert dict(error.partial_submissions) == accepted
    assert error.submission_complete is True
    assert "all 2 Slurm jobs were accepted" in str(error)
    record = json.loads(error.record_path.read_text(encoding="utf-8"))
    assert record["status"] == "submitting"
    assert record["submissions"] == accepted


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


def test_workflow_submit_json_reports_prepared_scripts(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "slurm")
    stage_workflow(manifest)

    result = runner.invoke(
        app,
        [
            "workflow",
            "submit",
            str(manifest),
            "--job-name",
            "inpaint_test",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "workflow.submit"
    assert payload["data"]["submitted"] is False
    assert payload["data"]["job_name"] == "inpaint_test"
    assert len(payload["data"]["scripts"]) == 2
    assert all(item["role"] == "slurm_script" for item in payload["artifacts"])


def test_workflow_run_json_reports_missing_authorization(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "local")
    stage_workflow(manifest)

    result = runner.invoke(app, ["workflow", "run", str(manifest), "--json"])

    assert result.exit_code == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["command"] == "workflow.run"
    assert payload["error"]["code"] == "authorization_required"
    assert not any((tmp_path / "workspace").rglob("CHGCAR"))
