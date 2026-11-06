"""Local and Slurm operations over a staged workflow."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from electrace._files import atomic_write_json, atomic_write_text

from .execution import JobResult, render_slurm_script, run_jobs, submit_slurm_script
from .manifest import load_workflow_manifest
from .staging import load_staged_workflow


class SlurmSubmissionError(RuntimeError):
    """A failed submission with every scheduler id known to this process."""

    def __init__(
        self,
        phase: Literal["submission", "recording"],
        failed_script: Path | None,
        partial_submissions: Mapping[str, str],
        record_path: Path,
        *,
        submission_complete: bool = False,
    ) -> None:
        self.phase = phase
        self.failed_script = failed_script
        self.partial_submissions = partial_submissions
        self.record_path = record_path
        self.submission_complete = submission_complete
        detail = f" for {failed_script}" if failed_script is not None else ""
        super().__init__(
            f"Slurm {phase} failed{detail} after {len(partial_submissions)} accepted job(s); "
            f"inspect {record_path} before retrying"
        )


def run_staged_workflow(
    manifest_path: str | Path,
    *,
    confirmed: bool = False,
    max_parallel: int = 1,
    stop_on_error: bool = True,
) -> tuple[JobResult, ...]:
    manifest = load_workflow_manifest(manifest_path)
    if manifest.scheduler.kind != "local":
        raise ValueError("workflow run requires scheduler.kind: local")
    staged = load_staged_workflow(manifest_path)
    results = run_jobs(
        staged.jobs,
        confirmed=confirmed,
        max_parallel=max_parallel,
        stop_on_error=stop_on_error,
    )
    atomic_write_json(
        staged.workspace / "run-results.json",
        {
            "schema_version": "electrace.run-results.v1",
            "manifest_sha256": staged.manifest_sha256,
            "results": [result.to_dict() for result in results],
        },
    )
    return results


def prepare_slurm_scripts(
    manifest_path: str | Path,
    *,
    job_name: str = "electrace",
    overwrite: bool = False,
) -> tuple[Path, ...]:
    manifest = load_workflow_manifest(manifest_path)
    if manifest.scheduler.kind != "slurm":
        raise ValueError("workflow submit requires scheduler.kind: slurm")
    staged = load_staged_workflow(manifest_path)
    script_directory = staged.workspace / "slurm"
    planned = tuple(
        (
            render_slurm_script(
                [job for job in staged.jobs if job.frame_index == frame.index],
                manifest.scheduler.directives,
                job_name,
            ),
            script_directory / f"frame-{frame.index:06d}.slurm",
        )
        for frame in staged.frames
    )
    if not overwrite:
        existing = next((path for _, path in planned if path.exists() or path.is_symlink()), None)
        if existing is not None:
            raise FileExistsError(f"refusing to replace existing Slurm script: {existing}")
    for document, path in planned:
        if path.is_symlink() or (path.exists() and not overwrite):
            raise FileExistsError(f"refusing to replace existing Slurm script: {path}")
        atomic_write_text(path, document, mode=0o700)
    atomic_write_json(
        script_directory / "scripts.json",
        {
            "schema_version": "electrace.slurm-scripts.v1",
            "job_name": job_name,
            "manifest_sha256": staged.manifest_sha256,
            "scripts": [str(path) for _, path in planned],
        },
    )
    return tuple(path for _, path in planned)


def submit_staged_workflow(
    manifest_path: str | Path,
    *,
    job_name: str = "electrace",
    submit_command: str | Sequence[str] = "sbatch",
    confirmed: bool = False,
    overwrite_scripts: bool = False,
) -> dict[str, str]:
    """Submit one script per frame and persist each accepted scheduler id."""

    if not confirmed:
        raise PermissionError("Slurm submission requires confirmed=True")
    staged = load_staged_workflow(manifest_path)
    record_path = staged.workspace / "slurm" / "submissions.json"
    if record_path.exists() or record_path.is_symlink():
        raise FileExistsError(
            f"refusing to submit while a prior Slurm submission record exists: {record_path}"
        )
    scripts = prepare_slurm_scripts(
        manifest_path,
        job_name=job_name,
        overwrite=overwrite_scripts,
    )
    submissions: dict[str, str] = {}

    def record(status: str, failed_script: Path | None = None) -> dict[str, Any]:
        return {
            "schema_version": "electrace.slurm-submissions.v1",
            "job_name": job_name,
            "manifest_sha256": staged.manifest_sha256,
            "status": status,
            "failed_script": str(failed_script) if failed_script else None,
            "submissions": submissions,
        }

    initial = json.dumps(record("submitting"), indent=2, sort_keys=True, allow_nan=False) + "\n"
    with record_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(initial)

    for script in scripts:
        try:
            submissions[str(script)] = submit_slurm_script(script, submit_command)
        except Exception as exc:
            try:
                atomic_write_json(record_path, record("failed", script))
            finally:
                raise SlurmSubmissionError("submission", script, submissions, record_path) from exc
        try:
            atomic_write_json(record_path, record("submitting"))
        except Exception as exc:
            raise SlurmSubmissionError("recording", None, submissions, record_path) from exc
    try:
        atomic_write_json(record_path, record("complete"))
    except Exception as exc:
        raise SlurmSubmissionError(
            "recording", None, submissions, record_path, submission_complete=True
        ) from exc
    return submissions


def workflow_status(manifest_path: str | Path) -> dict[str, Any]:
    staged = load_staged_workflow(manifest_path)
    jobs = []
    for job in staged.jobs:
        missing_inputs = [str(path) for path in job.required_inputs if not path.is_file()]
        missing_outputs = [str(path) for path in job.expected_outputs if not path.is_file()]
        jobs.append(
            {
                "id": job.id,
                "frame_index": job.frame_index,
                "frame_label": job.frame_label,
                "task": job.task,
                "ready": not missing_inputs,
                "complete": not missing_outputs,
                "missing_inputs": missing_inputs,
                "missing_outputs": missing_outputs,
                "stdout_path": str(job.stdout_path),
                "stderr_path": str(job.stderr_path),
            }
        )
    return {
        "schema_version": "electrace.workflow-status.v1",
        "manifest": str(staged.manifest_path),
        "workspace": str(staged.workspace),
        "frames": len(staged.frames),
        "complete": all(bool(job["complete"]) for job in jobs),
        "jobs": jobs,
    }
