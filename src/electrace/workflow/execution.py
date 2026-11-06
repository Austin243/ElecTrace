"""Execution primitives for local and Slurm workflow jobs."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

JobStatus = Literal["success", "failed", "blocked"]
DirectiveValue = str | int | float | bool | None

_ENVIRONMENT_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SLURM_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise ValueError(f"{field_name} must be non-empty text without control characters")


@dataclass(frozen=True, slots=True)
class JobSpec:
    """One external command and its declared inputs, outputs, and dependencies."""

    id: str
    frame_index: int
    frame_label: str
    task: str
    cwd: Path
    argv: tuple[str, ...]
    stdin: str | None
    stdout_path: Path
    required_inputs: tuple[Path, ...] = ()
    expected_outputs: tuple[Path, ...] = ()
    dependencies: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.frame_label, "frame_label")
        for index, token in enumerate(self.argv):
            _require_text(token, f"argv[{index}]")
        for name in ("required_inputs", "expected_outputs"):
            paths = getattr(self, name)
            if len(paths) != len(set(paths)):
                raise ValueError(f"{name} must not contain duplicates")
        for key, value in self.environment.items():
            if _ENVIRONMENT_KEY_PATTERN.fullmatch(key) is None:
                raise ValueError(f"invalid environment variable name: {key!r}")
            _require_text(value, f"environment value for {key!r}")

    @property
    def stderr_path(self) -> Path:
        return self.stdout_path.with_name(f"{self.stdout_path.name}.stderr.log")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "frame_index": self.frame_index,
            "frame_label": self.frame_label,
            "task": self.task,
            "cwd": str(self.cwd),
            "argv": list(self.argv),
            "stdin": self.stdin,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "required_inputs": [str(path) for path in self.required_inputs],
            "expected_outputs": [str(path) for path in self.expected_outputs],
            "dependencies": list(self.dependencies),
            "environment": dict(self.environment),
        }


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class JobResult:
    job: JobSpec
    status: JobStatus
    returncode: int | None = None
    started_at: str | None = None
    finished_at: str = field(default_factory=_timestamp)
    missing_expected_outputs: tuple[Path, ...] = ()
    error: str | None = None

    @property
    def id(self) -> str:
        return self.job.id

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.job.id,
            "frame_index": self.job.frame_index,
            "frame_label": self.job.frame_label,
            "task": self.job.task,
            "status": self.status,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stdout_path": str(self.job.stdout_path),
            "stderr_path": str(self.job.stderr_path),
            "expected_outputs": [str(path) for path in self.job.expected_outputs],
            "missing_expected_outputs": [str(path) for path in self.missing_expected_outputs],
            "dependencies": list(self.job.dependencies),
            "error": self.error,
        }


def _dependency_order(jobs: Iterable[JobSpec]) -> tuple[JobSpec, ...]:
    """Return jobs so that each follows its dependencies, otherwise in the given order."""

    pending = list(jobs)
    ordered: list[JobSpec] = []
    finished: set[str] = set()
    while pending:
        job = next(job for job in pending if finished.issuperset(job.dependencies))
        pending.remove(job)
        ordered.append(job)
        finished.add(job.id)
    return tuple(ordered)


def _execute_job(job: JobSpec) -> JobResult:
    started = _timestamp()
    if not job.cwd.is_dir():
        return JobResult(job, "failed", started_at=started, error=f"missing directory: {job.cwd}")
    missing_inputs = tuple(path for path in job.required_inputs if not path.is_file())
    if missing_inputs:
        return JobResult(
            job,
            "failed",
            started_at=started,
            error="required inputs are missing: " + ", ".join(map(str, missing_inputs)),
        )
    for output in (job.stdout_path, job.stderr_path, *job.expected_outputs):
        if output.is_symlink() or (output.exists() and not output.is_file()):
            return JobResult(job, "failed", started_at=started, error=f"unsafe output: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)

    returncode: int | None = None
    errors: list[str] = []
    try:
        with job.stdout_path.open("wb") as stdout, job.stderr_path.open("wb") as stderr:
            returncode = subprocess.run(
                job.argv,
                cwd=job.cwd,
                env={**os.environ, **job.environment},
                stdin=subprocess.DEVNULL if job.stdin is None else None,
                input=job.stdin,
                stdout=stdout,
                stderr=stderr,
                encoding="utf-8",
            ).returncode
    except OSError as exc:
        errors.append(f"could not execute {job.argv[0]!r}: {exc}")

    missing = tuple(path for path in job.expected_outputs if not path.is_file())
    if returncode not in (0, None):
        errors.append(f"command exited with status {returncode}")
    if missing:
        errors.append("expected outputs are missing: " + ", ".join(map(str, missing)))
    status: JobStatus = "success" if returncode == 0 and not errors else "failed"
    return JobResult(
        job,
        status,
        returncode=returncode,
        started_at=started,
        missing_expected_outputs=missing,
        error="; ".join(errors) or None,
    )


def run_jobs(
    jobs: Iterable[JobSpec],
    *,
    confirmed: bool = False,
    max_parallel: int = 1,
    stop_on_error: bool = True,
) -> tuple[JobResult, ...]:
    """Execute a dependency DAG locally with bounded parallelism."""

    if not confirmed:
        raise PermissionError("external job execution requires confirmed=True")
    ordered = _dependency_order(jobs)
    position = {job.id: index for index, job in enumerate(ordered)}
    pending = list(ordered)
    results: dict[str, JobResult] = {}
    futures: dict[Future[JobResult], JobSpec] = {}
    stopped = False

    def block(job: JobSpec, message: str) -> None:
        pending.remove(job)
        results[job.id] = JobResult(job, "blocked", error=message)

    def resolved(job: JobSpec) -> bool:
        return all(item in results for item in job.dependencies)

    with ThreadPoolExecutor(max_workers=min(max_parallel, len(ordered))) as executor:
        while pending or futures:
            # Dependency order lets one pass also block the dependents of a job it just blocked.
            for job in tuple(pending):
                if resolved(job):
                    failed = [item for item in job.dependencies if not results[item].succeeded]
                    if failed:
                        block(job, "dependency did not succeed: " + ", ".join(failed))
            for job in tuple(pending):
                if stopped:
                    block(job, "not started after a failure")
                elif resolved(job) and len(futures) < max_parallel:
                    pending.remove(job)
                    futures[executor.submit(_execute_job, job)] = job
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in sorted(completed, key=lambda item: position[futures[item].id]):
                job = futures.pop(future)
                results[job.id] = future.result()
                stopped = stopped or (stop_on_error and not results[job.id].succeeded)
    return tuple(results[job.id] for job in ordered)


def _slurm_directives(directives: Mapping[str, DirectiveValue], job_name: str) -> list[str]:
    if _SLURM_NAME_PATTERN.fullmatch(job_name) is None:
        raise ValueError("invalid Slurm job name")
    lines = [f"#SBATCH --job-name={job_name}"]
    for key in sorted(directives):
        value = directives[key]
        if value is None or value is True:
            lines.append(f"#SBATCH --{key}")
        elif value is not False:
            lines.append(f"#SBATCH --{key}={value}")
    return lines


def render_slurm_script(
    jobs: Iterable[JobSpec], directives: Mapping[str, DirectiveValue], job_name: str
) -> str:
    """Render a quoted, dependency-ordered Slurm script."""

    lines = ["#!/bin/sh", *_slurm_directives(directives, job_name), "", "set -eu", ""]
    for job in jobs:
        lines.append(f"# ElecTrace job {job.id}: frame {job.frame_index} - {job.task}")
        lines.append(f"cd {shlex.quote(str(job.cwd))}")
        for required in job.required_inputs:
            path = shlex.quote(str(required))
            lines.append(f"[ -f {path} ] || {{ echo 'missing required input' >&2; exit 1; }}")
        lines.append(f"mkdir -p -- {shlex.quote(str(job.stdout_path.parent))}")
        environment = " ".join(
            shlex.quote(f"{key}={value}") for key, value in sorted(job.environment.items())
        )
        command = shlex.join(job.argv)
        if environment:
            command = f"env {environment} {command}"
        command = (
            f"{command} < /dev/null"
            if job.stdin is None
            else f"printf %s {shlex.quote(job.stdin)} | {command}"
        )
        lines.append(
            f"{command} >{shlex.quote(str(job.stdout_path))} 2>{shlex.quote(str(job.stderr_path))}"
        )
        for expected in job.expected_outputs:
            path = shlex.quote(str(expected))
            lines.append(f"[ -f {path} ] || {{ echo 'missing expected output' >&2; exit 1; }}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def submit_slurm_script(script: Path, submit_command: str | Sequence[str]) -> str:
    if script.is_symlink() or not script.is_file():
        raise FileNotFoundError(f"Slurm script is not a regular file: {script}")
    command = (submit_command,) if isinstance(submit_command, str) else tuple(submit_command)
    for token in command:
        _require_text(token, "submit command")
    completed = subprocess.run(
        [*command, str(script)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"Slurm submission failed with status {completed.returncode}: {detail}")
    match = re.fullmatch(
        r"(?:Submitted\s+batch\s+job\s+)?([0-9]+)(?:;[^\s;]+)?", completed.stdout.strip()
    )
    if match is None:
        raise ValueError(f"could not parse Slurm job id from {completed.stdout.strip()!r}")
    return match.group(1)
