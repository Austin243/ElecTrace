"""Read-only workflow planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from electrace.plugins import resolve_executable

from . import staging as _staging
from .execution import JobSpec, validate_jobs
from .manifest import load_workflow_manifest
from .models import SchedulerSpec, WorkflowManifest
from .staging import StagedFrame

WORKFLOW_VALIDATION_SCHEMA_VERSION = "electrace.workflow-validation.v1"


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
            "schema_version": WORKFLOW_VALIDATION_SCHEMA_VERSION,
            "manifest": str(self.manifest),
            "workspace": str(self.workspace),
            "selected_frames": [frame.to_dict() for frame in self.selected_frames],
            "resolved_jobs": [job.to_dict() for job in self.resolved_jobs],
            "scheduler": self.scheduler.to_dict(),
            "warnings": list(self.warnings),
        }


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

    source_manifest = Path(manifest_path).expanduser().resolve()
    manifest = load_workflow_manifest(source_manifest)
    source_root, workspace = _staging._workspace_paths(source_manifest, manifest)
    if not source_root.is_dir():
        raise FileNotFoundError(f"workflow project root is not a directory: {source_root}")
    for step in manifest.steps:
        _staging._template_files(step, source_manifest.parent)

    selected = _staging._selected_sources(manifest, source_root)
    frames = _staging._staged_frames(selected, workspace)
    jobs: list[JobSpec] = []
    for source, frame in zip(selected, frames, strict=True):
        frame_directory = workspace / "frames" / _staging._safe_label(source.index, source.label)
        for step in manifest.steps:
            source_job = _staging._build_job(
                manifest, source_manifest.parent, frame, step, source.directory
            )
            jobs.append(
                _staging._rebase_job(source_job, source.directory, frame_directory / step.id)
            )
    warnings = (
        *_workspace_warnings(workspace),
        *_executable_warnings(manifest, source_manifest.parent),
    )
    return WorkflowValidation(
        manifest=source_manifest,
        workspace=workspace,
        selected_frames=frames,
        resolved_jobs=validate_jobs(jobs),
        scheduler=manifest.scheduler,
        warnings=warnings,
    )


__all__ = [
    "WORKFLOW_VALIDATION_SCHEMA_VERSION",
    "WorkflowValidation",
    "validate_workflow",
]
