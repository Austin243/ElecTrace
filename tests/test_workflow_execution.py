from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from electrace.workflow.execution import JobSpec, render_slurm_script, run_jobs


def _job(
    root: Path,
    identifier: str,
    code: str,
    *,
    dependencies: tuple[str, ...] = (),
    required: tuple[Path, ...] = (),
) -> JobSpec:
    directory = root / identifier
    directory.mkdir()
    output = directory / "result"
    return JobSpec(
        id=identifier,
        frame_index=0,
        frame_label="00",
        task=identifier,
        cwd=directory,
        argv=(sys.executable, "-c", code, str(output)),
        stdin=None,
        stdout_path=directory / "stdout.log",
        required_inputs=required,
        expected_outputs=(output,),
        dependencies=dependencies,
    )


def test_local_dag_requires_confirmation_and_runs_in_parallel(tmp_path: Path) -> None:
    write = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ok')"
    first = _job(tmp_path, "first", write)
    independent = _job(tmp_path, "independent", write)
    dependent = _job(
        tmp_path,
        "dependent",
        write,
        dependencies=(first.id,),
        required=(first.expected_outputs[0],),
    )

    with pytest.raises(PermissionError, match="confirmed=True"):
        run_jobs((dependent, independent, first), max_parallel=2)
    results = run_jobs((dependent, independent, first), confirmed=True, max_parallel=2)

    assert [result.id for result in results] == ["independent", "first", "dependent"]
    assert all(result.succeeded for result in results)
    assert all(
        path.is_file() for job in (first, independent, dependent) for path in job.expected_outputs
    )


def test_failure_blocks_dependents_and_slurm_commands_are_quoted(tmp_path: Path) -> None:
    failing = _job(tmp_path, "fail", "raise SystemExit(2)")
    dependent = _job(
        tmp_path,
        "after",
        "raise SystemExit('must not run')",
        dependencies=(failing.id,),
    )

    failed, blocked = run_jobs((failing, dependent), confirmed=True, stop_on_error=False)

    assert failed.status == "failed"
    assert blocked.status == "blocked"
    hostile = replace(
        failing,
        argv=(sys.executable, "value; touch never"),
        expected_outputs=(),
    )
    script = render_slurm_script((hostile,), {"partition": "cpu"}, "inpaint_test")
    assert "'value; touch never'" in script
    assert "#SBATCH --job-name=inpaint_test" in script
