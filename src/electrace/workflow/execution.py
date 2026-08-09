"""Execution primitives for local and Slurm workflow jobs."""

from __future__ import annotations

import math
import os
import re
import shlex
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from heapq import heapify, heappop, heappush
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from ._files import atomic_write_text

JobStatus = Literal["success", "failed", "blocked"]
DirectiveValue = str | int | float | bool | None

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_ENVIRONMENT_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SLURM_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SLURM_DIRECTIVE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9-]*\Z")
_SLURM_VALUE_PATTERN = re.compile(r"[A-Za-z0-9_./,:@%+=\[\]*?{}~-]+\Z")
_UNSAFE_SLURM_DIRECTIVES = frozenset({"command", "script", "wrap"})


def _text(value: object, field_name: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise ValueError(f"{field_name} must be non-empty text without control characters")
    if identifier and _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return value


def _absolute(value: str | Path, field_name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or "\x00" in str(path):
        raise ValueError(f"{field_name} must be an absolute path: {path}")
    return Path(os.path.normpath(path))


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
        object.__setattr__(self, "id", _text(self.id, "job id", identifier=True))
        if type(self.frame_index) is not int or self.frame_index < 0:
            raise ValueError("frame_index must be a non-negative integer")
        object.__setattr__(self, "frame_label", _text(self.frame_label, "frame_label"))
        object.__setattr__(self, "task", _text(self.task, "task"))
        object.__setattr__(self, "cwd", _absolute(self.cwd, "cwd"))
        argv = tuple(self.argv)
        if not argv:
            raise ValueError("argv must contain at least one token")
        for index, token in enumerate(argv):
            _text(token, f"argv[{index}]")
        object.__setattr__(self, "argv", argv)
        if self.stdin is not None and (not isinstance(self.stdin, str) or "\x00" in self.stdin):
            raise ValueError("stdin must be NUL-free text or None")
        object.__setattr__(self, "stdout_path", _absolute(self.stdout_path, "stdout_path"))
        for name in ("required_inputs", "expected_outputs"):
            paths = tuple(_absolute(path, name) for path in getattr(self, name))
            if len(paths) != len(set(paths)):
                raise ValueError(f"{name} must not contain duplicates")
            object.__setattr__(self, name, paths)
        dependencies = tuple(self.dependencies)
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("dependencies must not contain duplicates")
        for dependency in dependencies:
            _text(dependency, "dependency", identifier=True)
        object.__setattr__(self, "dependencies", dependencies)
        environment = dict(self.environment)
        for key, value in environment.items():
            if _ENVIRONMENT_KEY_PATTERN.fullmatch(key) is None:
                raise ValueError(f"invalid environment variable name: {key!r}")
            _text(value, f"environment value for {key!r}")
        object.__setattr__(self, "environment", MappingProxyType(environment))

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


@dataclass(frozen=True, slots=True)
class JobResult:
    id: str
    frame_index: int
    frame_label: str
    task: str
    status: JobStatus
    returncode: int | None
    started_at: str | None
    finished_at: str
    stdout_path: Path
    stderr_path: Path
    expected_outputs: tuple[Path, ...]
    missing_expected_outputs: tuple[Path, ...]
    dependencies: tuple[str, ...]
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "frame_index": self.frame_index,
            "frame_label": self.frame_label,
            "task": self.task,
            "status": self.status,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "expected_outputs": [str(path) for path in self.expected_outputs],
            "missing_expected_outputs": [str(path) for path in self.missing_expected_outputs],
            "dependencies": list(self.dependencies),
            "error": self.error,
        }


def _outputs(job: JobSpec) -> tuple[Path, ...]:
    return tuple(dict.fromkeys((job.stdout_path, job.stderr_path, *job.expected_outputs)))


def validate_jobs(jobs: Iterable[JobSpec]) -> tuple[JobSpec, ...]:
    """Return jobs in stable dependency order after validating the DAG."""

    specs = tuple(jobs)
    if not specs:
        raise ValueError("at least one job is required")
    by_id: dict[str, JobSpec] = {}
    position: dict[str, int] = {}
    owners: dict[Path, str] = {}
    for index, job in enumerate(specs):
        if not isinstance(job, JobSpec):
            raise TypeError(f"jobs[{index}] must be a JobSpec")
        if job.id in by_id:
            raise ValueError(f"duplicate job id: {job.id!r}")
        by_id[job.id] = job
        position[job.id] = index
        for output in _outputs(job):
            if output in owners:
                raise ValueError(f"jobs {owners[output]!r} and {job.id!r} share output {output}")
            owners[output] = job.id

    dependents: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {}
    for job in specs:
        unknown = [item for item in job.dependencies if item not in by_id]
        if job.id in job.dependencies or unknown:
            detail = "itself" if job.id in job.dependencies else ", ".join(unknown)
            raise ValueError(f"job {job.id!r} has invalid dependencies: {detail}")
        indegree[job.id] = len(job.dependencies)
        for dependency in job.dependencies:
            dependents[dependency].append(job.id)

    ready = [(position[job.id], job.id) for job in specs if indegree[job.id] == 0]
    heapify(ready)
    ordered: list[str] = []
    while ready:
        _, job_id = heappop(ready)
        ordered.append(job_id)
        for dependent in dependents[job_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heappush(ready, (position[dependent], dependent))
    if len(ordered) != len(specs):
        raise ValueError("job dependency graph contains a cycle")
    return tuple(by_id[job_id] for job_id in ordered)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _result(
    job: JobSpec,
    status: JobStatus,
    *,
    returncode: int | None = None,
    started_at: str | None = None,
    error: str | None = None,
    missing: tuple[Path, ...] = (),
) -> JobResult:
    return JobResult(
        id=job.id,
        frame_index=job.frame_index,
        frame_label=job.frame_label,
        task=job.task,
        status=status,
        returncode=returncode,
        started_at=started_at,
        finished_at=_timestamp(),
        stdout_path=job.stdout_path,
        stderr_path=job.stderr_path,
        expected_outputs=job.expected_outputs,
        missing_expected_outputs=missing,
        dependencies=job.dependencies,
        error=error,
    )


def _execute_job(job: JobSpec) -> JobResult:
    started = _timestamp()
    if not job.cwd.is_dir():
        return _result(job, "failed", started_at=started, error=f"missing directory: {job.cwd}")
    missing_inputs = tuple(path for path in job.required_inputs if not path.is_file())
    if missing_inputs:
        return _result(
            job,
            "failed",
            started_at=started,
            error="required inputs are missing: " + ", ".join(map(str, missing_inputs)),
        )
    for output in _outputs(job):
        if output.is_symlink() or (output.exists() and not output.is_file()):
            return _result(job, "failed", started_at=started, error=f"unsafe output: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)

    returncode: int | None = None
    error: str | None = None
    try:
        with job.stdout_path.open("wb") as stdout, job.stderr_path.open("wb") as stderr:
            options: dict[str, Any] = {
                "cwd": job.cwd,
                "env": {**os.environ, **dict(job.environment)},
                "stdout": stdout,
                "stderr": stderr,
                "shell": False,
                "check": False,
            }
            if job.stdin is None:
                options["stdin"] = subprocess.DEVNULL
            else:
                options.update({"input": job.stdin, "text": True, "encoding": "utf-8"})
            completed = subprocess.run(list(job.argv), **options)
            returncode = int(completed.returncode)
    except OSError as exc:
        error = f"could not execute {job.argv[0]!r}: {exc}"

    missing = tuple(path for path in job.expected_outputs if not path.is_file())
    errors = [message for message in (error,) if message]
    if returncode not in (0, None):
        errors.append(f"command exited with status {returncode}")
    if missing:
        errors.append("expected outputs are missing: " + ", ".join(map(str, missing)))
    status: JobStatus = "success" if returncode == 0 and not errors else "failed"
    return _result(
        job,
        status,
        returncode=returncode,
        started_at=started,
        error="; ".join(errors) or None,
        missing=missing,
    )


def _blocked(job: JobSpec, message: str) -> JobResult:
    return _result(job, "blocked", error=message)


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
    if type(max_parallel) is not int or max_parallel < 1:
        raise ValueError("max_parallel must be a positive integer")
    ordered = validate_jobs(jobs)
    by_id = {job.id: job for job in ordered}
    position = {job.id: index for index, job in enumerate(ordered)}
    dependents: dict[str, list[str]] = defaultdict(list)
    remaining = {job.id: len(job.dependencies) for job in ordered}
    for job in ordered:
        for dependency in job.dependencies:
            dependents[dependency].append(job.id)
    ready = [(position[job.id], job.id) for job in ordered if not job.dependencies]
    heapify(ready)
    pending = set(by_id)
    results: dict[str, JobResult] = {}
    futures: dict[Future[JobResult], JobSpec] = {}
    stopped = False

    def release(job_id: str) -> None:
        resolved = [job_id]
        while resolved:
            for dependent_id in dependents[resolved.pop()]:
                if dependent_id not in pending:
                    continue
                remaining[dependent_id] -= 1
                if remaining[dependent_id]:
                    continue
                job = by_id[dependent_id]
                failed = [item for item in job.dependencies if not results[item].succeeded]
                if failed:
                    pending.remove(dependent_id)
                    results[dependent_id] = _blocked(
                        job, "dependency did not succeed: " + ", ".join(failed)
                    )
                    resolved.append(dependent_id)
                else:
                    heappush(ready, (position[dependent_id], dependent_id))

    with ThreadPoolExecutor(max_workers=min(max_parallel, len(ordered))) as executor:
        while pending or futures:
            if stopped:
                for job_id in tuple(pending):
                    results[job_id] = _blocked(by_id[job_id], "not started after a failure")
                    pending.remove(job_id)
                ready.clear()
            else:
                while ready and len(futures) < max_parallel:
                    _, job_id = heappop(ready)
                    if job_id in pending:
                        pending.remove(job_id)
                        job = by_id[job_id]
                        futures[executor.submit(_execute_job, job)] = job
            if not futures:
                continue
            completed, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
            for future in sorted(completed, key=lambda item: position[futures[item].id]):
                job = futures.pop(future)
                outcome = future.result()
                results[job.id] = outcome
                release(job.id)
                stopped = stopped or (stop_on_error and not outcome.succeeded)
    return tuple(results[job.id] for job in ordered)


def _slurm_directives(directives: Mapping[str, DirectiveValue], job_name: str) -> list[str]:
    if _SLURM_NAME_PATTERN.fullmatch(job_name) is None:
        raise ValueError("invalid Slurm job name")
    lines = [f"#SBATCH --job-name={job_name}"]
    normalized: dict[str, DirectiveValue] = {}
    for raw_key, value in directives.items():
        key = raw_key.removeprefix("--")
        if (
            _SLURM_DIRECTIVE_PATTERN.fullmatch(key) is None
            or key in _UNSAFE_SLURM_DIRECTIVES
            or key == "job-name"
            or key in normalized
        ):
            raise ValueError(f"unsafe or duplicate Slurm directive: {raw_key!r}")
        normalized[key] = value
    for key in sorted(normalized):
        value = normalized[key]
        if value is False:
            continue
        if value is None or value is True:
            lines.append(f"#SBATCH --{key}")
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise TypeError(f"invalid Slurm directive value for {key!r}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"invalid Slurm directive value for {key!r}")
        text = str(value)
        if _SLURM_VALUE_PATTERN.fullmatch(text) is None:
            raise ValueError(f"unsafe Slurm directive value for {key!r}: {text!r}")
        lines.append(f"#SBATCH --{key}={text}")
    return lines


def render_slurm_script(
    jobs: Iterable[JobSpec], directives: Mapping[str, DirectiveValue], job_name: str
) -> str:
    """Render a quoted, dependency-ordered Slurm script."""

    lines = ["#!/bin/sh", *_slurm_directives(directives, job_name), "", "set -eu", ""]
    for job in validate_jobs(jobs):
        lines.append(f"# ElecTrace job {job.id}: frame {job.frame_index} - {job.task}")
        lines.append(f"cd {shlex.quote(str(job.cwd))}")
        for required in job.required_inputs:
            path = shlex.quote(str(required))
            lines.append(f"[ -f {path} ] || {{ echo 'missing required input' >&2; exit 1; }}")
        for parent in {job.stdout_path.parent, job.stderr_path.parent}:
            lines.append(f"mkdir -p -- {shlex.quote(str(parent))}")
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


def write_slurm_script(script: str, path: str | Path, *, overwrite: bool = False) -> Path:
    output = _absolute(path, "Slurm script path")
    if output.is_symlink() or (output.exists() and not overwrite):
        raise FileExistsError(f"refusing to replace existing Slurm script: {output}")
    return atomic_write_text(output, script, mode=0o700)


def submit_slurm_script(
    script: str | Path,
    submit_command: str | Sequence[str] = "sbatch",
    *,
    confirmed: bool = False,
) -> str:
    if not confirmed:
        raise PermissionError("Slurm submission requires confirmed=True")
    script_path = _absolute(script, "Slurm script")
    if script_path.is_symlink() or not script_path.is_file():
        raise FileNotFoundError(f"Slurm script is not a regular file: {script_path}")
    command = (submit_command,) if isinstance(submit_command, str) else tuple(submit_command)
    if not command:
        raise ValueError("submit_command must contain at least one token")
    for token in command:
        _text(token, "submit command")
    completed = subprocess.run(
        [*command, str(script_path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=False,
        check=False,
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
