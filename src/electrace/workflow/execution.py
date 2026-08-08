"""Explicit, confirmation-gated execution of external workflow jobs.

The plugin layer remains non-executing.  This module is the separate execution
boundary: callers must construct auditable :class:`JobSpec` records and opt in
with ``confirmed=True`` before a local process or Slurm submission can occur.
Commands are always passed as argument vectors with ``shell=False``.
"""

from __future__ import annotations

import math
import os
import re
import shlex
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from heapq import heapify, heappop, heappush
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from ._files import atomic_write_text, symlink_component

JobStatus = Literal["success", "failed", "blocked"]
DirectiveValue = str | int | float | bool | None

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_ENVIRONMENT_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SLURM_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SLURM_DIRECTIVE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9-]*\Z")
_SLURM_VALUE_PATTERN = re.compile(r"[A-Za-z0-9_./,:@%+=\[\]*?{}~-]+\Z")
_UNSAFE_SLURM_DIRECTIVES = frozenset({"command", "script", "wrap"})


def _safe_text(value: object, field_name: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{field_name} must not contain NUL or newline characters")
    if identifier and _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must start with an alphanumeric character and contain only "
            "letters, digits, '.', '_', ':', or '-'"
        )
    return value


def _absolute_path(value: str | Path, field_name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be an absolute path: {path}")
    if "\x00" in str(path):
        raise ValueError(f"{field_name} must not contain NUL characters")
    return Path(os.path.normpath(path))


@dataclass(frozen=True, slots=True)
class JobSpec:
    """A self-contained external task specification.

    All paths are absolute and commands are tokenized.  ``stdout_path`` is
    captured by the execution layer; stderr is written beside it as
    ``<stdout name>.stderr.log``.
    """

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
        object.__setattr__(self, "id", _safe_text(self.id, "job id", identifier=True))
        if isinstance(self.frame_index, bool) or not isinstance(self.frame_index, int):
            raise TypeError("frame_index must be an integer")
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        object.__setattr__(self, "frame_label", _safe_text(self.frame_label, "frame_label"))
        object.__setattr__(self, "task", _safe_text(self.task, "task"))
        object.__setattr__(self, "cwd", _absolute_path(self.cwd, "cwd"))

        argv = tuple(self.argv)
        if not argv:
            raise ValueError("argv must contain at least one token")
        for index, token in enumerate(argv):
            _safe_text(token, f"argv[{index}]")
        object.__setattr__(self, "argv", argv)

        if self.stdin is not None:
            if not isinstance(self.stdin, str):
                raise TypeError("stdin must be a string or None")
            if "\x00" in self.stdin:
                raise ValueError("stdin must not contain NUL characters")
        object.__setattr__(
            self,
            "stdout_path",
            _absolute_path(self.stdout_path, "stdout_path"),
        )
        for field_name in ("required_inputs", "expected_outputs"):
            paths = tuple(
                _absolute_path(path, f"{field_name}[{index}]")
                for index, path in enumerate(getattr(self, field_name))
            )
            if len(paths) != len(set(paths)):
                raise ValueError(f"{field_name} must not contain duplicate paths")
            object.__setattr__(self, field_name, paths)

        dependencies = tuple(self.dependencies)
        for index, dependency in enumerate(dependencies):
            _safe_text(dependency, f"dependencies[{index}]", identifier=True)
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("dependencies must not contain duplicate job ids")
        object.__setattr__(self, "dependencies", dependencies)

        environment = dict(self.environment)
        for key, value in environment.items():
            if not isinstance(key, str) or _ENVIRONMENT_KEY_PATTERN.fullmatch(key) is None:
                raise ValueError(f"invalid environment variable name: {key!r}")
            if not isinstance(value, str):
                raise TypeError(f"environment value for {key!r} must be a string")
            if any(character in value for character in ("\x00", "\r", "\n")):
                raise ValueError(
                    f"environment value for {key!r} must not contain NUL or newline characters"
                )
        object.__setattr__(self, "environment", MappingProxyType(environment))

    @property
    def stderr_path(self) -> Path:
        """Return the dedicated stderr log beside ``stdout_path``."""

        return self.stdout_path.with_name(f"{self.stdout_path.name}.stderr.log")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready execution specification."""

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
    """JSON-serializable outcome of one local job."""

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
        """Return a JSON-ready result record."""

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


def _declared_outputs(job: JobSpec) -> tuple[Path, ...]:
    """Return every job-owned output once, in deterministic declaration order."""

    return tuple(dict.fromkeys((job.stdout_path, job.stderr_path, *job.expected_outputs)))


def validate_jobs(jobs: Iterable[JobSpec]) -> tuple[JobSpec, ...]:
    """Validate ids, outputs, and dependencies; return stable topological order."""

    specs = tuple(jobs)
    if not specs:
        raise ValueError("at least one job is required")
    by_id: dict[str, JobSpec] = {}
    position: dict[str, int] = {}
    output_owners: dict[Path, str] = {}
    for index, job in enumerate(specs):
        if not isinstance(job, JobSpec):
            raise TypeError(f"jobs[{index}] must be a JobSpec")
        if job.id in by_id:
            raise ValueError(f"duplicate job id: {job.id!r}")
        by_id[job.id] = job
        position[job.id] = index
        for output in _declared_outputs(job):
            owner = output_owners.get(output)
            if owner is not None and owner != job.id:
                raise ValueError(
                    f"jobs {owner!r} and {job.id!r} declare the same output path: {output}"
                )
            output_owners[output] = job.id

    dependents: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {}
    for job in specs:
        if job.id in job.dependencies:
            raise ValueError(f"job {job.id!r} cannot depend on itself")
        unknown = [dependency for dependency in job.dependencies if dependency not in by_id]
        if unknown:
            raise ValueError(
                f"job {job.id!r} has unknown dependencies: {', '.join(sorted(unknown))}"
            )
        indegree[job.id] = len(job.dependencies)
        for dependency in job.dependencies:
            dependents[dependency].append(job.id)

    ready = [(position[job.id], job.id) for job in specs if indegree[job.id] == 0]
    heapify(ready)
    ordered_ids: list[str] = []
    while ready:
        _, job_id = heappop(ready)
        ordered_ids.append(job_id)
        for dependent in dependents[job_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heappush(ready, (position[dependent], dependent))
    if len(ordered_ids) != len(specs):
        cyclic = sorted(
            (job_id for job_id, degree in indegree.items() if degree > 0),
            key=position.__getitem__,
        )
        raise ValueError("job dependency graph contains a cycle involving: " + ", ".join(cyclic))
    return tuple(by_id[job_id] for job_id in ordered_ids)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _allocate_capture(path: Path) -> tuple[int, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    return descriptor, Path(name)


def _allocate_job_captures(job: JobSpec) -> tuple[int, Path, int, Path]:
    """Allocate both log files without leaking the first if the second fails."""

    stdout_descriptor, stdout_temporary = _allocate_capture(job.stdout_path)
    try:
        stderr_descriptor, stderr_temporary = _allocate_capture(job.stderr_path)
    except BaseException:
        os.close(stdout_descriptor)
        stdout_temporary.unlink(missing_ok=True)
        raise
    return stdout_descriptor, stdout_temporary, stderr_descriptor, stderr_temporary


def _unsafe_output(path: Path) -> str | None:
    if (component := symlink_component(path)) is not None:
        return f"refusing to follow output symlink component {component} for {path}"
    if path.exists() and not path.is_file():
        return f"output path exists but is not a regular file: {path}"
    return None


def _diagnostic_log(job: JobSpec, message: str) -> None:
    """Best-effort atomic diagnostic without following a stderr symlink."""

    if _unsafe_output(job.stderr_path) is not None:
        return
    temporary: Path | None = None
    try:
        descriptor, temporary = _allocate_capture(job.stderr_path)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
        os.replace(temporary, job.stderr_path)
    except OSError:
        return
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _result(
    job: JobSpec,
    *,
    status: JobStatus,
    returncode: int | None,
    started_at: str | None,
    error: str | None,
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
    started_at = _timestamp()
    if not job.cwd.is_dir():
        message = f"working directory does not exist: {job.cwd}"
        _diagnostic_log(job, message)
        return _result(
            job,
            status="failed",
            returncode=None,
            started_at=started_at,
            error=message,
        )
    missing_inputs = tuple(path for path in job.required_inputs if not path.is_file())
    if missing_inputs:
        message = "required inputs are missing: " + ", ".join(str(path) for path in missing_inputs)
        _diagnostic_log(job, message)
        return _result(
            job,
            status="failed",
            returncode=None,
            started_at=started_at,
            error=message,
        )
    for output in _declared_outputs(job):
        if (output_error := _unsafe_output(output)) is not None:
            _diagnostic_log(job, output_error)
            return _result(
                job,
                status="failed",
                returncode=None,
                started_at=started_at,
                error=output_error,
            )

    try:
        (
            stdout_descriptor,
            stdout_temporary,
            stderr_descriptor,
            stderr_temporary,
        ) = _allocate_job_captures(job)
    except OSError as exc:
        message = f"could not allocate job logs: {exc}"
        _diagnostic_log(job, message)
        return _result(
            job,
            status="failed",
            returncode=None,
            started_at=started_at,
            error=message,
        )
    returncode: int | None = None
    execution_error: str | None = None
    try:
        with (
            os.fdopen(stdout_descriptor, "wb") as stdout_handle,
            os.fdopen(stderr_descriptor, "wb") as stderr_handle,
        ):
            kwargs: dict[str, Any] = {
                "cwd": job.cwd,
                "env": {**os.environ, **dict(job.environment)},
                "stdout": stdout_handle,
                "stderr": stderr_handle,
                "shell": False,
                "check": False,
            }
            if job.stdin is None:
                kwargs["stdin"] = subprocess.DEVNULL
            else:
                kwargs.update({"input": job.stdin, "text": True, "encoding": "utf-8"})
            try:
                completed = subprocess.run(list(job.argv), **kwargs)
                returncode = int(completed.returncode)
            except OSError as exc:
                execution_error = f"could not execute {job.argv[0]!r}: {exc}"
                stderr_handle.write((execution_error + "\n").encode("utf-8", errors="replace"))
        # os.replace atomically replaces a leaf symlink entry rather than
        # following its target, closing the leaf-level preflight race.
        os.replace(stdout_temporary, job.stdout_path)
        os.replace(stderr_temporary, job.stderr_path)
    except OSError as exc:
        execution_error = execution_error or f"could not publish job logs atomically: {exc}"
    finally:
        stdout_temporary.unlink(missing_ok=True)
        stderr_temporary.unlink(missing_ok=True)

    expected_symlinks = {path: symlink_component(path) for path in job.expected_outputs}
    unsafe_expected = tuple(
        path for path, component in expected_symlinks.items() if component is not None
    )
    missing_expected = tuple(
        path
        for path in job.expected_outputs
        if expected_symlinks[path] is not None or not path.is_file()
    )
    errors: list[str] = []
    if execution_error is not None:
        errors.append(execution_error)
    if returncode not in {0, None}:
        errors.append(f"command exited with status {returncode}")
    if unsafe_expected:
        errors.append(
            "expected outputs are unsafe symlinks: "
            + ", ".join(str(path) for path in unsafe_expected)
        )
    if missing_expected:
        errors.append(
            "expected outputs are missing: " + ", ".join(str(path) for path in missing_expected)
        )
    status: JobStatus = "success" if returncode == 0 and not errors else "failed"
    return _result(
        job,
        status=status,
        returncode=returncode,
        started_at=started_at,
        error="; ".join(errors) or None,
        missing=missing_expected,
    )


def _blocked_result(job: JobSpec, message: str) -> JobResult:
    return _result(
        job,
        status="blocked",
        returncode=None,
        started_at=None,
        error=message,
    )


def run_jobs(
    jobs: Iterable[JobSpec],
    *,
    confirmed: bool = False,
    max_parallel: int = 1,
    stop_on_error: bool = True,
) -> tuple[JobResult, ...]:
    """Execute a dependency DAG locally after explicit confirmation.

    Independent jobs may run concurrently.  A failed dependency always blocks
    its dependents.  With ``stop_on_error=True``, no new jobs are started after
    the first observed failure; already-running jobs are allowed to finish.
    """

    if not confirmed:
        raise PermissionError("external job execution requires confirmed=True")
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or max_parallel < 1:
        raise ValueError("max_parallel must be a positive integer")
    ordered = validate_jobs(jobs)
    by_id = {job.id: job for job in ordered}
    position = {job.id: index for index, job in enumerate(ordered)}
    dependents: dict[str, list[str]] = defaultdict(list)
    remaining_dependencies: dict[str, int] = {}
    for job in ordered:
        remaining_dependencies[job.id] = len(job.dependencies)
        for dependency in job.dependencies:
            dependents[dependency].append(job.id)

    ready = [(position[job.id], job.id) for job in ordered if remaining_dependencies[job.id] == 0]
    heapify(ready)
    pending = set(by_id)
    results: dict[str, JobResult] = {}
    futures: dict[Future[JobResult], JobSpec] = {}
    stop_requested = False

    def release_dependents(job_id: str) -> None:
        """Promote or transitively block jobs whose last dependency completed."""

        resolved = [job_id]
        while resolved:
            for dependent_id in dependents[resolved.pop()]:
                if dependent_id not in pending:
                    continue
                remaining_dependencies[dependent_id] -= 1
                if remaining_dependencies[dependent_id] != 0:
                    continue
                dependent = by_id[dependent_id]
                failed = [
                    dependency
                    for dependency in dependent.dependencies
                    if not results[dependency].succeeded
                ]
                if failed:
                    pending.remove(dependent_id)
                    results[dependent_id] = _blocked_result(
                        dependent,
                        "dependency did not succeed: " + ", ".join(failed),
                    )
                    resolved.append(dependent_id)
                else:
                    heappush(ready, (position[dependent_id], dependent_id))

    worker_count = min(max_parallel, len(ordered))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        while pending or futures:
            if stop_requested:
                for job in ordered:
                    if job.id not in pending:
                        continue
                    results[job.id] = _blocked_result(
                        job,
                        "not started because stop_on_error was triggered",
                    )
                    pending.remove(job.id)
                ready.clear()
            else:
                while ready and len(futures) < worker_count:
                    _, job_id = heappop(ready)
                    if job_id not in pending:
                        continue
                    job = by_id[job_id]
                    futures[executor.submit(_execute_job, job)] = job
                    pending.remove(job_id)

            if not futures:
                if pending:  # pragma: no cover - validate_jobs prevents deadlock
                    raise RuntimeError("validated job graph could not make execution progress")
                continue
            completed_futures, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
            for future in sorted(completed_futures, key=lambda item: position[futures[item].id]):
                job = futures.pop(future)
                try:
                    outcome = future.result()
                except Exception as exc:  # pragma: no cover - defensive worker boundary
                    outcome = _result(
                        job,
                        status="failed",
                        returncode=None,
                        started_at=None,
                        error=f"unexpected execution failure: {exc}",
                    )
                results[job.id] = outcome
                release_dependents(job.id)
                if stop_on_error and not outcome.succeeded:
                    stop_requested = True

    return tuple(results[job.id] for job in ordered)


def _slurm_directives(directives: Mapping[str, DirectiveValue], job_name: str) -> list[str]:
    if not isinstance(job_name, str) or _SLURM_NAME_PATTERN.fullmatch(job_name) is None:
        raise ValueError(
            "job_name must be 1-128 characters using only letters, digits, '.', '_', or '-'"
        )
    lines = [f"#SBATCH --job-name={job_name}"]
    normalized: dict[str, DirectiveValue] = {}
    for raw_key, value in directives.items():
        if not isinstance(raw_key, str):
            raise TypeError("Slurm directive names must be strings")
        key = raw_key.removeprefix("--")
        if _SLURM_DIRECTIVE_PATTERN.fullmatch(key) is None:
            raise ValueError(f"unsafe Slurm directive name: {raw_key!r}")
        if key == "job-name":
            raise ValueError("pass the Slurm job name through job_name, not directives")
        if key in _UNSAFE_SLURM_DIRECTIVES:
            raise ValueError(f"Slurm directive {key!r} may not embed executable content")
        if key in normalized:
            raise ValueError(f"duplicate normalized Slurm directive: {key!r}")
        normalized[key] = value
    for key in sorted(normalized):
        value = normalized[key]
        if value is False:
            continue
        if value is None or value is True:
            lines.append(f"#SBATCH --{key}")
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise TypeError(
                f"Slurm directive {key!r} must be a string, finite number, boolean, or None"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Slurm directive {key!r} must be finite")
        text = str(value)
        if _SLURM_VALUE_PATTERN.fullmatch(text) is None:
            raise ValueError(f"unsafe Slurm directive value for {key!r}: {text!r}")
        lines.append(f"#SBATCH --{key}={text}")
    return lines


def _shell_failure(message: str) -> str:
    return f"printf '%s\\n' {shlex.quote(message)} >&2; exit 1"


def _shell_test_regular(path: Path, message: str) -> str:
    quoted = shlex.quote(str(path))
    return f"[ -f {quoted} ] || {{ {_shell_failure(message)}; }}"


def _shell_test_not_symlink(path: Path) -> str:
    quoted = shlex.quote(str(path))
    return f"[ ! -L {quoted} ] || {{ {_shell_failure(f'refusing output symlink: {path}')}; }}"


def _shell_output_guards(path: Path) -> list[str]:
    """Render guards for the output leaf and every lexical parent."""

    return [_shell_test_not_symlink(component) for component in (path, *path.parents[:-1])]


def render_slurm_script(
    jobs: Iterable[JobSpec],
    directives: Mapping[str, DirectiveValue],
    job_name: str,
) -> str:
    """Render a strict, topologically ordered Slurm batch script."""

    ordered = validate_jobs(jobs)
    lines = ["#!/bin/sh", *_slurm_directives(directives, job_name), "", "set -eu", ""]
    for index, job in enumerate(ordered):
        lines.append(
            f"# ElecTrace job {job.id}: frame {job.frame_index} ({job.frame_label}) - {job.task}"
        )
        lines.append(
            f"[ -d {shlex.quote(str(job.cwd))} ] || "
            f"{{ {_shell_failure(f'working directory does not exist: {job.cwd}')}; }}"
        )
        for required in job.required_inputs:
            lines.append(_shell_test_regular(required, f"required input is missing: {required}"))
        for output in _declared_outputs(job):
            lines.extend(_shell_output_guards(output))
        for parent in sorted({job.stdout_path.parent, job.stderr_path.parent}, key=str):
            lines.append(f"mkdir -p -- {shlex.quote(str(parent))}")

        stdout_variable = f"electrace_stdout_{index}"
        stderr_variable = f"electrace_stderr_{index}"
        returncode_variable = f"electrace_returncode_{index}"
        stdout_template = job.stdout_path.parent / f".{job.stdout_path.name}.XXXXXX.tmp"
        stderr_template = job.stderr_path.parent / f".{job.stderr_path.name}.XXXXXX.tmp"
        lines.extend(
            (
                f"{stdout_variable}=$(mktemp {shlex.quote(str(stdout_template))})",
                f"{stderr_variable}=$(mktemp {shlex.quote(str(stderr_template))})",
                f'trap \'rm -f -- "${{{stdout_variable}}}" "${{{stderr_variable}}}"\' EXIT HUP INT TERM',
            )
        )
        environment = " ".join(
            shlex.quote(f"{key}={value}") for key, value in sorted(job.environment.items())
        )
        command = shlex.join(job.argv)
        if environment:
            command = f"env {environment} {command}"
        if job.stdin is None:
            command = f"{command} < /dev/null"
        else:
            command = f"printf %s {shlex.quote(job.stdin)} | {command}"
        lines.extend(
            (
                f"if (cd {shlex.quote(str(job.cwd))} && {command}) "
                f'>"${{{stdout_variable}}}" 2>"${{{stderr_variable}}}"; then',
                f"  {returncode_variable}=0",
                "else",
                f"  {returncode_variable}=$?",
                "fi",
                *_shell_output_guards(job.stdout_path),
                *_shell_output_guards(job.stderr_path),
                f'mv -f -- "${{{stdout_variable}}}" {shlex.quote(str(job.stdout_path))}',
                f'mv -f -- "${{{stderr_variable}}}" {shlex.quote(str(job.stderr_path))}',
                "trap - EXIT HUP INT TERM",
                f'[ "${{{returncode_variable}}}" -eq 0 ] || '
                f"{{ {_shell_failure(f'job {job.id} failed')}; }}",
            )
        )
        for expected in job.expected_outputs:
            lines.extend(_shell_output_guards(expected))
            lines.append(_shell_test_regular(expected, f"expected output is missing: {expected}"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_slurm_script(
    script: str,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write an executable Slurm script without following a symlink."""

    if not isinstance(script, str) or "\x00" in script:
        raise ValueError("script must be NUL-free text")
    output = _absolute_path(path, "Slurm script path")
    if (output.exists() or output.is_symlink()) and not overwrite:
        raise FileExistsError(f"refusing to replace existing Slurm script: {output}")
    if (component := symlink_component(output.parent)) is not None:
        raise ValueError(f"refusing to follow Slurm script parent symlink: {component}")
    return atomic_write_text(output, script, mode=0o700)


def submit_slurm_script(
    script: str | Path,
    submit_command: str | Sequence[str] = "sbatch",
    *,
    confirmed: bool = False,
) -> str:
    """Submit a batch script with ``shell=False`` and return its Slurm job id."""

    if not confirmed:
        raise PermissionError("Slurm submission requires confirmed=True")
    script_path = _absolute_path(script, "Slurm script")
    if symlink_component(script_path) is not None or not script_path.is_file():
        raise FileNotFoundError(f"Slurm script is not a regular file: {script_path}")
    command: tuple[str, ...] = (
        (submit_command,) if isinstance(submit_command, str) else tuple(submit_command)
    )
    if not command:
        raise ValueError("submit_command must contain at least one token")
    for index, token in enumerate(command):
        _safe_text(token, f"submit_command[{index}]")
    completed = subprocess.run(
        [*command, str(script_path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"Slurm submission failed with status {completed.returncode}: {detail}")
    output = completed.stdout.strip()
    match = re.fullmatch(r"(?:Submitted\s+batch\s+job\s+)?([0-9]+)(?:;[^\s;]+)?", output)
    if match is None:
        raise ValueError(f"could not parse Slurm job id from submission output: {output!r}")
    return match.group(1)
