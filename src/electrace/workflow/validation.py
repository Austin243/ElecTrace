"""Read-only workflow planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from electrace.plugins.base import resolve_executable

from . import staging as _staging
from .execution import JobSpec
from .models import SchedulerSpec, WorkflowManifest
from .staging import StagedFrame


@dataclass(frozen=True, slots=True)
class WorkflowValidation:
    """The resolved job plan for one workflow manifest."""

    manifest: Path
    workspace: Path
    selected_frames: tuple[StagedFrame, ...]
    resolved_jobs: tuple[JobSpec, ...]
    scheduler: SchedulerSpec
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "electrace.workflow-validation.v1",
            "manifest": str(self.manifest),
            "workspace": str(self.workspace),
            "selected_frames": [frame.to_dict() for frame in self.selected_frames],
            "resolved_jobs": [job.to_dict() for job in self.resolved_jobs],
            "scheduler": asdict(self.scheduler),
            "warnings": list(self.warnings),
        }


def _workspace_warnings(workspace: Path) -> tuple[str, ...]:
    if not workspace.exists():
        return ()
    if not workspace.is_dir():
        raise ValueError(f"workflow workspace path is not a directory: {workspace}")
    if not _staging._workspace_is_managed(workspace):
        raise ValueError(
            f"workflow workspace already exists and is not ElecTrace-managed: {workspace}"
        )
    return (
        "workflow workspace already exists; staging it again requires --force, and "
        f"--discard-results if it holds results: {workspace}",
    )


def _executable_warnings(
    manifest: WorkflowManifest,
    manifest_base: Path,
) -> tuple[str, ...]:
    warnings: list[str] = []
    for name, specification in sorted(manifest.executables.items()):
        command = _staging._resolve_command(manifest_base, specification.path)
        if resolve_executable(command) is None:
            warnings.append(f"executable {name!r} is not available on this host: {command}")
        if specification.argv_prefix and resolve_executable(specification.argv_prefix[0]) is None:
            warnings.append(
                f"argv_prefix command for executable {name!r} is not available "
                f"on this host: {specification.argv_prefix[0]}"
            )
    return tuple(warnings)


def validate_workflow(manifest_path: str | Path) -> WorkflowValidation:
    """Resolve frames and jobs without creating or modifying a workspace."""

    source_manifest, manifest, source_root, workspace = _staging.load_project(manifest_path)
    if not source_root.is_dir():
        raise FileNotFoundError(f"workflow project root is not a directory: {source_root}")
    templates = {
        step.id: _staging._template_files(step, source_manifest.parent) for step in manifest.steps
    }

    frames, frame_warnings = _staging._selected_frames(manifest, source_root, workspace)
    species_warnings = tuple(
        warning
        for step in manifest.steps
        for warning in _staging._check_vasp_species(step, templates[step.id], frames)
    )
    _staging._check_spin_steps(manifest)
    jobs: list[JobSpec] = []
    for frame in frames:
        for step in manifest.steps:
            source_job = _staging._build_job(
                manifest, source_manifest.parent, frame, step, frame.source_directory
            )
            destination = frame.workspace_directory / step.id
            jobs.append(_staging._rebase_job(source_job, frame.source_directory, destination))
            if _staging._is_periodic_critic2(step):
                # Staging writes this input; building it here reports a structure that
                # cannot be seeded before anything is written.
                _staging._periodic_input(step, frame.structure)
    warnings = (
        *frame_warnings,
        *_workspace_warnings(workspace),
        *_executable_warnings(manifest, source_manifest.parent),
        *species_warnings,
    )
    return WorkflowValidation(
        manifest=source_manifest,
        workspace=workspace,
        selected_frames=frames,
        resolved_jobs=tuple(jobs),
        scheduler=manifest.scheduler,
        warnings=warnings,
    )
