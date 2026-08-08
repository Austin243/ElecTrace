"""Read-only validation and planning for ElecTrace workflows.

Validation resolves the same frames, commands, and dependency graph as staging,
but represents staged files virtually.  It never creates a workspace, copies a
template, or executes an external command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from electrace.io.discovery import FrameSource
from electrace.plugins import resolve_executable

from . import staging as _staging
from ._files import symlink_component
from .execution import JobSpec, validate_jobs
from .manifest import load_workflow_manifest
from .models import SchedulerSpec, WorkflowManifest, WorkflowStep
from .staging import StagedFrame

WORKFLOW_VALIDATION_SCHEMA_VERSION = "electrace.workflow-validation.v1"


@dataclass(frozen=True, slots=True)
class WorkflowValidation:
    """A deterministic, JSON-ready workflow plan produced without filesystem writes."""

    manifest: Path
    workspace: Path
    selected_frames: tuple[StagedFrame, ...]
    resolved_jobs: tuple[JobSpec, ...]
    scheduler: SchedulerSpec
    warnings: tuple[str, ...] = ()
    schema_version: str = WORKFLOW_VALIDATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WORKFLOW_VALIDATION_SCHEMA_VERSION:
            raise ValueError(
                f"workflow validation schema_version must be {WORKFLOW_VALIDATION_SCHEMA_VERSION!r}"
            )
        for name in ("manifest", "workspace"):
            path = getattr(self, name)
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError(f"workflow validation {name} must be an absolute Path")
        if type(self.selected_frames) is not tuple or any(
            not isinstance(frame, StagedFrame) for frame in self.selected_frames
        ):
            raise TypeError("selected_frames must be a tuple of StagedFrame records")
        if type(self.resolved_jobs) is not tuple or any(
            not isinstance(job, JobSpec) for job in self.resolved_jobs
        ):
            raise TypeError("resolved_jobs must be a tuple of JobSpec records")
        if not isinstance(self.scheduler, SchedulerSpec):
            raise TypeError("scheduler must be a SchedulerSpec")
        if type(self.warnings) is not tuple or any(
            not isinstance(warning, str) or not warning for warning in self.warnings
        ):
            raise TypeError("warnings must be a tuple of non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        """Return the stable public JSON representation of this validation plan."""

        return {
            "schema_version": self.schema_version,
            "manifest": str(self.manifest),
            "workspace": str(self.workspace),
            "selected_frames": [frame.to_dict() for frame in self.selected_frames],
            "resolved_jobs": [job.to_dict() for job in self.resolved_jobs],
            "scheduler": self.scheduler.to_dict(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _VirtualFile:
    """One file that staging can provide before a job is launched.

    ``source`` names a regular file that exists while validation runs.  A null
    source represents an output promised by an earlier dependency; the staged
    link is initially dangling but becomes available after that job succeeds.
    """

    source: Path | None
    is_symlink: bool = False


def _template_files(manifest_base: Path, step: WorkflowStep) -> dict[str, _VirtualFile]:
    if step.template_dir is None:
        return {}
    template = _staging._resolve_from(manifest_base, step.template_dir)
    if not template.is_dir():
        raise FileNotFoundError(f"workflow template directory does not exist: {template}")
    if symlink_component(template) is not None:
        raise ValueError(f"workflow template directory must not traverse a symlink: {template}")

    files: dict[str, _VirtualFile] = {}
    for item in sorted(template.iterdir(), key=lambda path: path.name):
        if item.is_symlink():
            raise ValueError(f"workflow templates must not contain symlinks: {item}")
        if not item.is_file():
            raise ValueError(f"workflow templates must contain only regular files: {item}")
        # Staging copies the complete file.  Stream it here as well so the
        # read-only preflight cannot approve a template that staging cannot
        # actually read, without retaining large inputs such as POTCAR in RAM.
        with item.open("rb") as handle:
            while handle.read(1024 * 1024):
                pass
        name = _staging._basename(item.name, "template filename")
        files[name] = _VirtualFile(item)
    return files


def _validate_incar_overrides(step: WorkflowStep, files: dict[str, _VirtualFile]) -> None:
    raw = step.options.get("incar_overrides")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise TypeError("vasp options.incar_overrides must be a mapping")
    incar = files.get("INCAR")
    if incar is None or incar.source is None:
        raise FileNotFoundError("vasp options.incar_overrides requires an INCAR template")
    # Staging decodes INCAR before applying overrides.  Read it here as well so
    # validation cannot approve a template that staging would reject.
    incar.source.read_text(encoding="utf-8")
    for raw_key, value in raw.items():
        if _staging._INCAR_KEY.fullmatch(raw_key) is None:
            raise ValueError(f"invalid INCAR tag in workflow: {raw_key!r}")
        if isinstance(value, bool):
            continue
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise TypeError(f"INCAR override {raw_key!r} must be a scalar")


def _dependency_file(
    name: str,
    step: WorkflowStep,
    files_by_step: dict[str, dict[str, _VirtualFile]],
    outputs_by_step: dict[str, frozenset[str]],
) -> _VirtualFile | None:
    """Mirror staging's reverse dependency lookup without touching a workspace."""

    for dependency in reversed(step.depends_on):
        existing = files_by_step[dependency].get(name)
        if existing is not None and existing.source is not None:
            return existing
        if name in outputs_by_step[dependency]:
            return _VirtualFile(None)
    return None


def _apply_explicit_input_links(
    step: WorkflowStep,
    source_structure: Path,
    files: dict[str, _VirtualFile],
    files_by_step: dict[str, dict[str, _VirtualFile]],
    outputs_by_step: dict[str, frozenset[str]],
) -> None:
    raw = step.options.get("input_links")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise TypeError(f"step {step.id}.options.input_links must be a mapping")
    for raw_target, specification in raw.items():
        target = _staging._basename(raw_target, f"step {step.id}.options.input_links key")
        if specification == "source:structure":
            files[target] = _VirtualFile(source_structure)
            continue
        if not isinstance(specification, dict):
            raise TypeError(
                f"step {step.id}.options.input_links.{target} must be an object or source:structure"
            )
        dependency = specification.get("step")
        filename = specification.get("file", target)
        if not isinstance(dependency, str) or dependency not in step.depends_on:
            raise ValueError(
                f"step {step.id}.options.input_links.{target}.step must be a declared dependency"
            )
        source_name = _staging._basename(
            filename, f"step {step.id}.options.input_links.{target}.file"
        )
        if target in files:
            raise FileExistsError(f"workflow input target already exists: {target}")
        available = files_by_step[dependency].get(source_name)
        if available is None and source_name in outputs_by_step[dependency]:
            available = _VirtualFile(None)
        if available is None:
            raise FileNotFoundError(
                f"step {step.id!r} input link {target!r} cannot be provided by "
                f"dependency {dependency!r} as {source_name!r}"
            )
        files[target] = _VirtualFile(available.source, is_symlink=True)


def _apply_automatic_plugin_inputs(
    step: WorkflowStep,
    source_structure: Path,
    files: dict[str, _VirtualFile],
    files_by_step: dict[str, dict[str, _VirtualFile]],
    outputs_by_step: dict[str, frozenset[str]],
) -> None:
    options = _staging._plugin_options(step)
    structure_name = str(options.get("structure_file", "CONTCAR"))
    for raw_name in _staging._plugin_required_names(step):
        name = _staging._basename(raw_name, f"step {step.id} plugin input")
        if name in files:
            continue
        if step.kind in {"critic2", "lobster"} and name == structure_name:
            files[name] = _VirtualFile(source_structure)
            continue
        available = _dependency_file(name, step, files_by_step, outputs_by_step)
        if available is not None:
            files[name] = _VirtualFile(available.source, is_symlink=True)


def _validate_lobster_input(step: WorkflowStep, files: dict[str, _VirtualFile]) -> None:
    options = _staging._plugin_options(step)
    input_name = _staging._basename(
        options.get("input_file", "lobsterin"), f"step {step.id} lobster input_file"
    )
    entry = files.get(input_name)
    if entry is None or entry.source is None or entry.is_symlink or not entry.source.is_file():
        raise FileNotFoundError(f"LOBSTER workflow requires a regular reviewed {input_name}")
    text = entry.source.read_text(encoding="utf-8", errors="replace")
    if _staging._LOBSTER_BOND_REQUEST.search(text) is None:
        raise ValueError(
            f"LOBSTER step {step.id!r} must request bonds with cohpGenerator or cohpBetween"
        )
    if step.capability == "cobi" and _staging._LOBSTER_SKIP_COBI.search(text) is not None:
        raise ValueError(f"LOBSTER COBI step {step.id!r} cannot use skipcobi")


def _validate_job_inputs_and_collisions(
    step: WorkflowStep,
    job: JobSpec,
    files: dict[str, _VirtualFile],
) -> None:
    missing = [path.name for path in job.required_inputs if path.name not in files]
    if missing:
        names = ", ".join(sorted(missing))
        raise FileNotFoundError(f"step {step.id!r} cannot provide required staged inputs: {names}")

    required = set(job.required_inputs)
    captures = {job.stdout_path, job.stderr_path}
    products = {job.stdout_path, job.stderr_path, *job.expected_outputs}
    # VASP restart inputs such as CHGCAR and WAVECAR are intentionally updated
    # in place.  Descriptor templates, by contrast, must never pre-populate a
    # result that collection could mistake for newly generated evidence.
    protected_products = captures if step.kind == "vasp" else products
    overlap = sorted(required & protected_products, key=str)
    if overlap:
        raise ValueError(
            f"step {step.id!r} declares paths as both input and output: "
            + ", ".join(str(path) for path in overlap)
        )

    staged_names = set(files)
    product_names = {path.name for path in protected_products}
    collisions = sorted(staged_names & product_names)
    if collisions:
        raise ValueError(
            f"step {step.id!r} stages files that collide with declared outputs: "
            + ", ".join(collisions)
        )


def _plan_jobs(
    manifest: WorkflowManifest,
    manifest_path: Path,
    workspace: Path,
    selected: tuple[FrameSource, ...],
    frames: tuple[StagedFrame, ...],
) -> tuple[JobSpec, ...]:
    templates = {step.id: _template_files(manifest_path.parent, step) for step in manifest.steps}
    jobs: list[JobSpec] = []
    for source, frame in zip(selected, frames, strict=True):
        files_by_step: dict[str, dict[str, _VirtualFile]] = {}
        outputs_by_step: dict[str, frozenset[str]] = {}
        for step in manifest.steps:
            files = dict(templates[step.id])
            if step.kind == "vasp":
                files["POSCAR"] = _VirtualFile(source.structure)
                _validate_incar_overrides(step, files)
            else:
                _apply_explicit_input_links(
                    step,
                    source.structure,
                    files,
                    files_by_step,
                    outputs_by_step,
                )
                _apply_automatic_plugin_inputs(
                    step,
                    source.structure,
                    files,
                    files_by_step,
                    outputs_by_step,
                )
                if step.kind == "lobster":
                    _validate_lobster_input(step, files)

            frame_directory = (
                workspace / "frames" / _staging._safe_label(source.index, source.label)
            )
            destination = frame_directory / step.id
            source_job = _staging._build_job(
                manifest,
                manifest_path.parent,
                frame,
                step,
                source.directory,
            )
            job = _staging._rebase_job(source_job, source.directory, destination)
            _validate_job_inputs_and_collisions(step, job, files)
            jobs.append(job)
            files_by_step[step.id] = files
            outputs_by_step[step.id] = frozenset(_staging._step_outputs(step))
    ordered = validate_jobs(jobs)
    _validate_descriptor_collisions(ordered, manifest, frames)
    return ordered


def _validate_descriptor_collisions(
    jobs: tuple[JobSpec, ...],
    manifest: WorkflowManifest,
    frames: tuple[StagedFrame, ...],
) -> None:
    """Reject descriptor basenames that collection would flatten to one path."""

    descriptor_tasks = {step.id for step in manifest.steps if step.kind != "vasp"}
    labels = {frame.index: frame.analysis_label for frame in frames}
    destinations: dict[tuple[int, str], str] = {}
    for job in jobs:
        if job.task not in descriptor_tasks:
            continue
        for output in job.expected_outputs:
            if output.name not in _staging._DESCRIPTOR_FILENAMES:
                continue
            key = (job.frame_index, output.name)
            previous = destinations.get(key)
            if previous is not None:
                destination = Path("descriptors") / labels[job.frame_index] / output.name
                raise ValueError(
                    f"descriptor output collision at {destination}: {previous} and {job.id}"
                )
            destinations[key] = job.id


def _workspace_warnings(workspace: Path) -> tuple[str, ...]:
    if workspace.is_symlink():
        raise ValueError(f"workflow workspace must not be a symlink: {workspace}")
    if not workspace.exists():
        return ()
    if not workspace.is_dir():
        raise ValueError(f"workflow workspace path is not a directory: {workspace}")
    if not _staging._workspace_is_managed(workspace):
        raise ValueError(
            f"workflow workspace already exists and is not ElecTrace-managed: {workspace}"
        )
    return (f"workflow workspace already exists; staging it again requires --force: {workspace}",)


def _executable_warnings(
    manifest: WorkflowManifest,
    manifest_base: Path,
) -> tuple[str, ...]:
    warnings: list[str] = []
    for name in sorted(manifest.executables):
        specification = manifest.executables[name]
        command = _staging._resolve_command(manifest_base, specification.path)
        if resolve_executable(command) is None:
            warnings.append(f"executable {name!r} is not available on this host: {command}")
        if specification.argv_prefix:
            wrapper = specification.argv_prefix[0]
            if resolve_executable(wrapper) is None:
                warnings.append(
                    f"argv_prefix command for executable {name!r} is not available "
                    f"on this host: {wrapper}"
                )
    return tuple(warnings)


def validate_workflow(manifest_path: str | Path) -> WorkflowValidation:
    """Resolve and validate a workflow without creating or modifying any path.

    The function loads the strict manifest, discovers and selects source frames,
    validates templates and virtual input links, resolves executable argument
    vectors, and validates the complete job DAG and output ownership.  Missing
    host executables are warnings because Slurm modules or runtime environments
    may provide them later; malformed plans and unavailable staged inputs fail.
    """

    source_manifest = Path(manifest_path).expanduser().resolve()
    manifest = load_workflow_manifest(source_manifest)
    source_root, workspace = _staging._workspace_paths(source_manifest, manifest)
    if not source_root.is_dir():
        raise FileNotFoundError(f"workflow project root is not a directory: {source_root}")

    workspace_warnings = _workspace_warnings(workspace)
    selected = _staging._selected_sources(manifest, source_root)
    frames = _staging._staged_frames(selected, workspace)
    jobs = _plan_jobs(
        manifest,
        source_manifest,
        workspace,
        selected,
        frames,
    )
    warnings = (
        *workspace_warnings,
        *_executable_warnings(manifest, source_manifest.parent),
    )
    return WorkflowValidation(
        manifest=source_manifest,
        workspace=workspace,
        selected_frames=frames,
        resolved_jobs=jobs,
        scheduler=manifest.scheduler,
        warnings=warnings,
    )


__all__ = [
    "WORKFLOW_VALIDATION_SCHEMA_VERSION",
    "WorkflowValidation",
    "validate_workflow",
]
