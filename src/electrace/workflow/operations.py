"""Local and Slurm operations over a staged ElecTrace workflow."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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

    scripts = prepare_slurm_scripts(
        manifest_path,
        job_name=job_name,
        overwrite=overwrite_scripts,
    )
    submissions = {
        str(script): submit_slurm_script(
            script,
            submit_command=submit_command,
            confirmed=confirmed,
        )
        for script in scripts
    }
    staged = load_staged_workflow(manifest_path)
    atomic_write_json(
        staged.workspace / "slurm" / "submissions.json",
        {
            "schema_version": "electrace.slurm-submissions.v1",
            "job_name": job_name,
            "manifest_sha256": staged.manifest_sha256,
            "submissions": submissions,
        },
    )
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
    "prepare_slurm_scripts",
    "run_staged_workflow",
    "submit_staged_workflow",
    "workflow_status",
]
