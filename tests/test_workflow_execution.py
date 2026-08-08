from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from electrace.workflow.execution import (
    JobSpec,
    render_slurm_script,
    run_jobs,
    submit_slurm_script,
    validate_jobs,
    write_slurm_script,
)


def _job(
    root: Path,
    job_id: str,
    *,
    code: str = "print('ok')",
    arguments: tuple[str, ...] = (),
    stdin: str | None = None,
    required_inputs: tuple[Path, ...] = (),
    expected_outputs: tuple[Path, ...] = (),
    dependencies: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
) -> JobSpec:
    return JobSpec(
        id=job_id,
        frame_index=int(job_id.removeprefix("job") or 0),
        frame_label=job_id,
        task=f"test task {job_id}",
        cwd=root.resolve(),
        argv=(sys.executable, "-c", code, *arguments),
        stdin=stdin,
        stdout_path=(root / f"{job_id}.stdout").resolve(),
        required_inputs=required_inputs,
        expected_outputs=expected_outputs,
        dependencies=dependencies,
        environment=environment or {},
    )


def test_job_spec_validates_paths_tokens_and_environment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cwd must be an absolute path"):
        JobSpec(
            id="job0",
            frame_index=0,
            frame_label="00",
            task="test",
            cwd=Path("relative"),
            argv=(sys.executable,),
            stdin=None,
            stdout_path=(tmp_path / "stdout").resolve(),
        )
    with pytest.raises(ValueError, match=r"argv\[2\].*newline"):
        _job(tmp_path, "job0", code="bad\nargument")
    with pytest.raises(ValueError, match="invalid environment variable"):
        _job(tmp_path, "job0", environment={"BAD=NAME": "value"})
    with pytest.raises(ValueError, match=r"environment value.*newline"):
        _job(tmp_path, "job0", environment={"SAFE_NAME": "bad\nvalue"})


def test_validate_jobs_returns_stable_dependency_order_and_rejects_bad_graphs(
    tmp_path: Path,
) -> None:
    first = _job(tmp_path, "job0")
    second = _job(tmp_path, "job1", dependencies=(first.id,))

    assert [job.id for job in validate_jobs((second, first))] == ["job0", "job1"]
    with pytest.raises(ValueError, match="duplicate job id"):
        validate_jobs((first, first))
    with pytest.raises(ValueError, match="unknown dependencies"):
        validate_jobs((replace(second, dependencies=("missing",)),))
    cyclic_first = replace(first, dependencies=(second.id,))
    with pytest.raises(ValueError, match="contains a cycle"):
        validate_jobs((cyclic_first, second))
    with pytest.raises(ValueError, match="at least one job"):
        run_jobs((), confirmed=True, max_parallel=10_000)


def test_run_jobs_requires_confirmation_without_starting_process(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    job = _job(
        tmp_path,
        "job0",
        code="from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
        arguments=(str(marker),),
    )

    with pytest.raises(PermissionError, match="confirmed=True"):
        run_jobs((job,))

    assert not marker.exists()
    assert not job.stdout_path.exists()


def test_run_jobs_executes_dependency_dag_and_emits_json_ready_results(
    tmp_path: Path,
) -> None:
    required = tmp_path / "required.txt"
    required.write_text("input", encoding="utf-8")
    product = tmp_path / "product.txt"
    first = _job(
        tmp_path,
        "job0",
        code=(
            "from pathlib import Path; import os, sys; "
            "Path(sys.argv[1]).write_text(Path(sys.argv[2]).read_text() + '-product'); "
            "print(os.environ['ELECTRACE_TEST_VALUE']); "
            "print('first warning', file=sys.stderr)"
        ),
        arguments=(str(product), str(required)),
        stdin="ignored input\n",
        required_inputs=(required,),
        expected_outputs=(product,),
        environment={"ELECTRACE_TEST_VALUE": "environment value"},
    )
    second = _job(
        tmp_path,
        "job1",
        code="from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())",
        arguments=(str(product),),
        required_inputs=(product,),
        dependencies=(first.id,),
    )

    results = run_jobs((second, first), confirmed=True, max_parallel=2)

    assert [result.id for result in results] == ["job0", "job1"]
    assert all(result.succeeded for result in results)
    assert product.read_text(encoding="utf-8") == "input-product"
    assert first.stdout_path.read_text(encoding="utf-8") == "environment value\n"
    assert first.stderr_path.read_text(encoding="utf-8") == "first warning\n"
    assert second.stdout_path.read_text(encoding="utf-8") == "input-product\n"
    json.dumps([result.to_dict() for result in results], allow_nan=False)


def test_missing_input_fails_job_and_blocks_its_dependency(tmp_path: Path) -> None:
    missing = tmp_path / "missing-input"
    dependent_marker = tmp_path / "dependent-ran"
    first = _job(tmp_path, "job0", required_inputs=(missing,))
    second = _job(
        tmp_path,
        "job1",
        code="from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
        arguments=(str(dependent_marker),),
        dependencies=(first.id,),
    )

    first_result, second_result = run_jobs((first, second), confirmed=True, stop_on_error=False)

    assert first_result.status == "failed"
    assert first_result.returncode is None
    assert "required inputs are missing" in (first_result.error or "")
    assert second_result.status == "blocked"
    assert "dependency did not succeed" in (second_result.error or "")
    assert not dependent_marker.exists()


def test_stop_on_error_prevents_later_independent_job(tmp_path: Path) -> None:
    marker = tmp_path / "later-ran"
    failing = _job(tmp_path, "job0", code="raise SystemExit(7)")
    later = _job(
        tmp_path,
        "job1",
        code="from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
        arguments=(str(marker),),
    )

    failed, blocked = run_jobs((failing, later), confirmed=True, max_parallel=1, stop_on_error=True)

    assert failed.status == "failed"
    assert failed.returncode == 7
    assert blocked.status == "blocked"
    assert not marker.exists()


def test_keep_going_runs_independent_work_and_transitively_blocks_dependents(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "independent-ran"
    failing = _job(tmp_path, "job0", code="raise SystemExit(9)")
    independent = _job(
        tmp_path,
        "job1",
        code="from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
        arguments=(str(marker),),
    )
    dependent = _job(
        tmp_path,
        "job2",
        dependencies=(failing.id, independent.id),
    )
    grandchild = _job(tmp_path, "job3", dependencies=(dependent.id,))

    results = run_jobs(
        (grandchild, dependent, independent, failing),
        confirmed=True,
        max_parallel=2,
        stop_on_error=False,
    )

    by_id = {result.id: result for result in results}
    assert {job_id: result.status for job_id, result in by_id.items()} == {
        "job0": "failed",
        "job1": "success",
        "job2": "blocked",
        "job3": "blocked",
    }
    assert by_id["job2"].error == "dependency did not succeed: job0"
    assert by_id["job3"].error == "dependency did not succeed: job2"
    assert marker.read_text(encoding="utf-8") == "ran"


def test_failed_dependency_blocks_a_long_chain_without_recursion(tmp_path: Path) -> None:
    chain_length = 1_500
    jobs = [_job(tmp_path, "job0", required_inputs=(tmp_path / "missing",))]
    jobs.extend(
        _job(tmp_path, f"job{index}", dependencies=(f"job{index - 1}",))
        for index in range(1, chain_length)
    )

    results = run_jobs(jobs, confirmed=True, stop_on_error=False)

    assert len(results) == chain_length
    assert results[0].status == "failed"
    assert all(result.status == "blocked" for result in results[1:])
    assert results[-1].error == f"dependency did not succeed: job{chain_length - 2}"


@pytest.mark.parametrize("symlink_field", ["stdout", "expected"])
def test_run_jobs_never_follows_output_symlinks(tmp_path: Path, symlink_field: str) -> None:
    external = tmp_path / "external-target"
    external.write_text("unchanged", encoding="utf-8")
    marker = tmp_path / "process-ran"
    expected = tmp_path / "expected-output"
    job = _job(
        tmp_path,
        "job0",
        code="from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
        arguments=(str(marker),),
        expected_outputs=(expected,) if symlink_field == "expected" else (),
    )
    symlink = job.stdout_path if symlink_field == "stdout" else expected
    symlink.symlink_to(external)

    (result,) = run_jobs((job,), confirmed=True)

    assert result.status == "failed"
    assert "symlink" in (result.error or "")
    assert external.read_text(encoding="utf-8") == "unchanged"
    assert not marker.exists()


def test_run_jobs_rejects_symlinked_output_parent(tmp_path: Path) -> None:
    external_directory = tmp_path / "external-directory"
    external_directory.mkdir()
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(external_directory, target_is_directory=True)
    marker = tmp_path / "process-ran"
    job = JobSpec(
        id="job0",
        frame_index=0,
        frame_label="00",
        task="parent symlink regression",
        cwd=tmp_path.resolve(),
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
            str(marker),
        ),
        stdin=None,
        stdout_path=linked_directory / "stdout.log",
    )

    (result,) = run_jobs((job,), confirmed=True)

    assert result.status == "failed"
    assert "symlink component" in (result.error or "")
    assert not marker.exists()
    assert not (external_directory / "stdout.log").exists()


def test_run_jobs_rejects_expected_symlink_created_by_process(tmp_path: Path) -> None:
    external = tmp_path / "external-target"
    external.write_text("unchanged", encoding="utf-8")
    expected = tmp_path / "expected-output"
    job = _job(
        tmp_path,
        "job0",
        code="from pathlib import Path; import sys; Path(sys.argv[1]).symlink_to(sys.argv[2])",
        arguments=(str(expected), str(external)),
        expected_outputs=(expected,),
    )

    (result,) = run_jobs((job,), confirmed=True)

    assert result.status == "failed"
    assert "unsafe symlinks" in (result.error or "")
    assert external.read_text(encoding="utf-8") == "unchanged"


def test_rendered_slurm_script_orders_dependencies_and_quotes_untrusted_tokens(
    tmp_path: Path,
) -> None:
    injection_marker = tmp_path / "injection-marker"
    expected = tmp_path / "expected.txt"
    hostile_argument = f"value; touch {injection_marker}"
    hostile_environment = f"$(touch {injection_marker})"
    first = _job(
        tmp_path,
        "job0",
        code=(
            "from pathlib import Path; import os, sys; "
            "Path(sys.argv[1]).write_text('ready'); "
            "print(sys.argv[2]); print(os.environ['ELECTRACE_PAYLOAD'])"
        ),
        arguments=(str(expected), hostile_argument),
        expected_outputs=(expected,),
        environment={"ELECTRACE_PAYLOAD": hostile_environment},
    )
    second = _job(
        tmp_path,
        "job1",
        code="print('dependent')",
        dependencies=(first.id,),
    )

    document = render_slurm_script(
        (second, first),
        {
            "partition": "gpu",
            "exclude": "node[1-8]",
            "exclusive": True,
            "nice": 1.5,
        },
        "inpaint_test",
    )
    script_path = write_slurm_script(document, (tmp_path / "jobs.slurm").resolve())
    completed = subprocess.run(
        ["/bin/sh", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )

    assert document.startswith("#!/bin/sh\n#SBATCH --job-name=inpaint_test\n")
    assert "#SBATCH --exclude=node[1-8]" in document
    assert "#SBATCH --nice=1.5" in document
    assert document.index("ElecTrace job job0") < document.index("ElecTrace job job1")
    assert completed.returncode == 0, completed.stderr
    assert expected.read_text(encoding="utf-8") == "ready"
    assert first.stdout_path.read_text(encoding="utf-8") == (
        f"{hostile_argument}\n{hostile_environment}\n"
    )
    assert second.stdout_path.read_text(encoding="utf-8") == "dependent\n"
    assert not injection_marker.exists()


@pytest.mark.parametrize(
    ("directives", "job_name"),
    [
        ({"partition\n--exclusive": "gpu"}, "safe"),
        ({"partition": "gpu\n#SBATCH --exclusive"}, "safe"),
        ({"wrap": "python-code"}, "safe"),
        ({"partition": "gpu"}, "unsafe\nname"),
    ],
)
def test_render_slurm_script_rejects_directive_and_name_injection(
    tmp_path: Path,
    directives: dict[str, str],
    job_name: str,
) -> None:
    with pytest.raises(ValueError, match=r"unsafe|job_name|executable content"):
        render_slurm_script((_job(tmp_path, "job0"),), directives, job_name)


def test_write_slurm_script_atomically_replaces_symlink_not_target(tmp_path: Path) -> None:
    external = tmp_path / "external-script"
    external.write_text("external", encoding="utf-8")
    output = (tmp_path / "job.slurm").resolve()
    output.symlink_to(external)

    written = write_slurm_script("#!/bin/sh\nexit 0\n", output, overwrite=True)

    assert written == output
    assert not output.is_symlink()
    assert output.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    assert external.read_text(encoding="utf-8") == "external"
    assert os.access(output, os.X_OK)


def test_submit_slurm_script_requires_confirmation_and_parses_fake_job_id(
    tmp_path: Path,
) -> None:
    script = write_slurm_script("#!/bin/sh\nexit 0\n", (tmp_path / "job.slurm").resolve())
    fake_submit = tmp_path / "fake_submit.py"
    fake_submit.write_text(
        "import pathlib, sys\n"
        "assert pathlib.Path(sys.argv[1]).is_file()\n"
        "print('Submitted batch job 424242')\n",
        encoding="utf-8",
    )

    with pytest.raises(PermissionError, match="confirmed=True"):
        submit_slurm_script(script, (sys.executable, str(fake_submit)))

    assert (
        submit_slurm_script(
            script,
            (sys.executable, str(fake_submit)),
            confirmed=True,
        )
        == "424242"
    )
