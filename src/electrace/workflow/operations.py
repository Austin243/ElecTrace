"""Local and Slurm operations over a staged ElecTrace workflow."""

from __future__ import annotations

import json
import os
import stat
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from ._files import atomic_write_json
from .execution import (
    JobResult,
    JobSpec,
    render_slurm_script,
    run_jobs,
    submit_slurm_script,
    write_slurm_script,
)
from .manifest import load_workflow_manifest
from .staging import StagedWorkflow, load_staged_workflow

_SubmissionStatus = Literal["submitting", "failed", "complete"]
_SubmissionFailurePhase = Literal["submission", "recording"]


class SlurmSubmissionError(RuntimeError):
    """An interrupted Slurm submission sequence with safe-recovery context.

    ``partial_submissions`` contains every known accepted job id.  For a
    ``submission`` failure those ids have already been journaled at
    ``record_path``.  For a ``recording`` failure the exception is authoritative
    because that journal may lag the accepted ids.  Callers should report both
    before permitting a retry.
    """

    def __init__(
        self,
        *,
        phase: _SubmissionFailurePhase,
        failed_script: Path | None,
        partial_submissions: Mapping[str, str],
        record_path: Path,
        submission_complete: bool = False,
    ) -> None:
        if phase == "submission" and failed_script is None:
            raise ValueError("failed_script is required for a submission failure")
        if phase == "recording" and failed_script is not None:
            raise ValueError("failed_script must be None for a recording failure")
        if phase == "submission" and submission_complete:
            raise ValueError("a failed submission cannot complete the submission sequence")
        self.phase = phase
        self.failed_script = failed_script
        self.partial_submissions = MappingProxyType(dict(partial_submissions))
        self.record_path = record_path
        self.submission_complete = submission_complete
        count = len(self.partial_submissions)
        if phase == "submission":
            message = (
                f"Slurm submission failed for {failed_script} after {count} successful "
                f"submission{'s' if count != 1 else ''}"
            )
        elif submission_complete:
            message = (
                f"all {count} Slurm jobs were accepted, but marking the record complete failed"
            )
        else:
            message = (
                f"{count} Slurm submission{'s were' if count != 1 else ' was'} accepted, "
                "but updating the record failed"
            )
        super().__init__(f"{message}; inspect {record_path} before retrying")


def _write_submission_record(
    path: Path,
    *,
    job_name: str,
    manifest_sha256: str,
    submissions: Mapping[str, str],
    status: _SubmissionStatus,
    failed_script: Path | None = None,
) -> None:
    atomic_write_json(
        path,
        _submission_record(
            job_name=job_name,
            manifest_sha256=manifest_sha256,
            submissions=submissions,
            status=status,
            failed_script=failed_script,
        ),
    )


def _submission_record(
    *,
    job_name: str,
    manifest_sha256: str,
    submissions: Mapping[str, str],
    status: _SubmissionStatus,
    failed_script: Path | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "electrace.slurm-submissions.v1",
        "job_name": job_name,
        "manifest_sha256": manifest_sha256,
        "status": status,
        "failed_script": str(failed_script) if failed_script is not None else None,
        "submissions": dict(submissions),
    }


def _reserve_submission_record(
    path: Path,
    *,
    job_name: str,
    manifest_sha256: str,
) -> tuple[int, int]:
    """Exclusively reserve a journal before any scheduler call is made."""

    payload = _submission_record(
        job_name=job_name,
        manifest_sha256=manifest_sha256,
        submissions={},
        status="submitting",
    )
    document = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise FileExistsError(
            f"refusing to submit while a prior Slurm submission record exists: {path}"
        ) from exc
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _remove_reserved_submission_record(path, (identity.st_dev, identity.st_ino))
        raise
    return identity.st_dev, identity.st_ino


def _remove_reserved_submission_record(path: Path, identity: tuple[int, int]) -> None:
    """Remove only the exact empty-attempt journal reserved by this process."""

    try:
        current = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
        path.unlink()


def run_staged_workflow(
    manifest_path: str | Path,
    *,
    confirmed: bool = False,
    max_parallel: int = 1,
    stop_on_error: bool = True,
) -> tuple[JobResult, ...]:
    """Execute a local staged workflow after explicit confirmation."""

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


def _jobs_by_frame(staged: StagedWorkflow) -> tuple[tuple[JobSpec, ...], ...]:
    grouped: dict[int, list[JobSpec]] = defaultdict(list)
    order: list[int] = []
    for job in staged.jobs:
        if job.frame_index not in grouped:
            order.append(job.frame_index)
        grouped[job.frame_index].append(job)
    return tuple(tuple(grouped[index]) for index in order)


def prepare_slurm_scripts(
    manifest_path: str | Path,
    *,
    job_name: str = "electrace",
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Render one dependency-ordered Slurm script per selected frame."""

    manifest = load_workflow_manifest(manifest_path)
    if manifest.scheduler.kind != "slurm":
        raise ValueError("workflow submit requires scheduler.kind: slurm")
    staged = load_staged_workflow(manifest_path)
    script_directory = staged.workspace / "slurm"
    directives = manifest.scheduler.directives
    planned: list[tuple[str, Path]] = []
    for jobs in _jobs_by_frame(staged):
        first = jobs[0]
        document = render_slurm_script(jobs, directives, job_name)
        path = script_directory / f"frame-{first.frame_index:06d}.slurm"
        planned.append((document, path))
    if not overwrite:
        existing = next(
            (path for _, path in planned if path.exists() or path.is_symlink()),
            None,
        )
        if existing is not None:
            raise FileExistsError(f"refusing to replace existing Slurm script: {existing}")
    paths = [write_slurm_script(document, path, overwrite=overwrite) for document, path in planned]
    atomic_write_json(
        script_directory / "scripts.json",
        {
            "schema_version": "electrace.slurm-scripts.v1",
            "job_name": job_name,
            "manifest_sha256": staged.manifest_sha256,
            "scripts": [str(path) for path in paths],
        },
    )
    return tuple(paths)


def submit_staged_workflow(
    manifest_path: str | Path,
    *,
    job_name: str = "electrace",
    submit_command: str | Sequence[str] = "sbatch",
    confirmed: bool = False,
    overwrite_scripts: bool = False,
) -> dict[str, str]:
    """Render and explicitly submit one Slurm job per selected frame."""

    if not confirmed:
        raise PermissionError("Slurm submission requires confirmed=True")
    staged = load_staged_workflow(manifest_path)
    record_path = staged.workspace / "slurm" / "submissions.json"
    submissions: dict[str, str] = {}
    reservation = _reserve_submission_record(
        record_path,
        job_name=job_name,
        manifest_sha256=staged.manifest_sha256,
    )
    try:
        scripts = prepare_slurm_scripts(
            manifest_path,
            job_name=job_name,
            overwrite=overwrite_scripts,
        )
    except BaseException:
        _remove_reserved_submission_record(record_path, reservation)
        raise
    for script in scripts:
        try:
            job_id = submit_slurm_script(
                script,
                submit_command=submit_command,
                confirmed=True,
            )
        except Exception as exc:
            error = SlurmSubmissionError(
                phase="submission",
                failed_script=script,
                partial_submissions=submissions,
                record_path=record_path,
            )
            try:
                _write_submission_record(
                    record_path,
                    job_name=job_name,
                    manifest_sha256=staged.manifest_sha256,
                    submissions=submissions,
                    status="failed",
                    failed_script=script,
                )
            except Exception as record_error:
                error.add_note(f"could not mark the submission record failed: {record_error}")
            raise error from exc
        submissions[str(script)] = job_id
        try:
            _write_submission_record(
                record_path,
                job_name=job_name,
                manifest_sha256=staged.manifest_sha256,
                submissions=submissions,
                status="submitting",
            )
        except Exception as exc:
            raise SlurmSubmissionError(
                phase="recording",
                failed_script=None,
                partial_submissions=submissions,
                record_path=record_path,
            ) from exc
    try:
        _write_submission_record(
            record_path,
            job_name=job_name,
            manifest_sha256=staged.manifest_sha256,
            submissions=submissions,
            status="complete",
        )
    except Exception as exc:
        raise SlurmSubmissionError(
            phase="recording",
            failed_script=None,
            partial_submissions=submissions,
            record_path=record_path,
            submission_complete=True,
        ) from exc
    return submissions


def workflow_status(manifest_path: str | Path) -> dict[str, Any]:
    """Return a read-only status snapshot for every staged job."""

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


__all__ = [
    "SlurmSubmissionError",
    "prepare_slurm_scripts",
    "run_staged_workflow",
    "submit_staged_workflow",
    "workflow_status",
]
