"""Collect completed workflow evidence into an isolated ElecTrace analysis.

Collection is deliberately a copy boundary.  Original structures and external
tool outputs remain read-only; analysis consumes only a marker-owned directory
assembled beside the staged workspace and published after complete validation.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from electrace.config import (
    ElecTraceConfig,
    EventSettings,
    InputConfig,
    MatchingSettings,
    OutputConfig,
    load_config,
)
from electrace.io.descriptors import load_descriptor
from electrace.io.vasp import read_poscar
from electrace.models import Frame
from electrace.pipeline import AnalysisArtifacts, run_project
from electrace.provenance import sha256_file

from ._files import absolute_lexical, read_limited_text, symlink_component
from .manifest import load_workflow_manifest
from .models import WorkflowManifest
from .staging import StagedFrame, StagedWorkflow, descriptor_outputs, load_staged_workflow

COLLECTION_SCHEMA_VERSION = "electrace.collection.v1"
COLLECTION_DIRECTORY = "analysis"
COLLECTION_RECORD = ".electrace-collection.json"
MAX_COLLECTION_RECORD_BYTES = 16_000_000
_ANALYSIS_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CollectionArtifacts:
    """Paths published by a successful workflow collection."""

    config_path: Path
    input_root: Path
    descriptor_root: Path
    files: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _FilePlan:
    relative_path: Path
    role: str
    frame: StagedFrame | None = None
    source: Path | None = None
    content: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        if self.relative_path.is_absolute() or ".." in self.relative_path.parts:
            raise ValueError(f"collection destination must be relative: {self.relative_path}")
        if (self.source is None) == (self.content is None):
            raise ValueError("collection file plan must have exactly one source or content")


def _reject_symlink_path(path: Path, context: str) -> None:
    component = symlink_component(path)
    if component is not None:
        raise ValueError(f"{context} must not traverse a symlink: {component}")


def _within(
    path: Path,
    root: Path,
    context: str,
    *,
    allow_root: bool = False,
) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{context} must be an absolute path: {path}")
    normalized = Path(os.path.normpath(path))
    normalized_root = Path(os.path.normpath(root))
    if (normalized == normalized_root and not allow_root) or (
        normalized != normalized_root and normalized_root not in normalized.parents
    ):
        raise ValueError(f"{context} escapes its managed root {normalized_root}: {normalized}")
    return normalized


def _require_directory(path: Path, context: str) -> None:
    _reject_symlink_path(path, context)
    if not path.is_dir():
        raise FileNotFoundError(f"{context} is missing or not a directory: {path}")


def _require_regular_file(path: Path, context: str, *, nonempty: bool = True) -> os.stat_result:
    _reject_symlink_path(path, context)
    try:
        status = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{context} is missing: {path}") from exc
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"{context} is not a regular file: {path}")
    if nonempty and status.st_size == 0:
        raise ValueError(f"{context} is empty: {path}")
    return status


def _safe_analysis_label(value: object, order: int) -> str:
    if not isinstance(value, str) or _ANALYSIS_LABEL.fullmatch(value) is None:
        raise ValueError(f"unsafe staged analysis_label: {value!r}")
    if not value.startswith(f"{order:06d}-"):
        raise ValueError(
            f"staged analysis_label {value!r} does not preserve selected-frame order {order}"
        )
    return value


def _copy_regular_file(source: Path, destination: Path, context: str) -> None:
    """Copy through a no-follow descriptor and reject concurrent replacement."""

    before = _require_regular_file(source, context)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"{context} changed while collection was starting: {source}")
        with (
            os.fdopen(descriptor, "rb", closefd=False) as source_handle,
            destination.open("xb") as destination_handle,
        ):
            shutil.copyfileobj(source_handle, destination_handle)
        after_open = os.fstat(descriptor)
        if (after_open.st_size, after_open.st_mtime_ns) != (
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise ValueError(f"{context} changed while it was being collected: {source}")
    finally:
        os.close(descriptor)
    after_path = _require_regular_file(source, context)
    if (after_path.st_dev, after_path.st_ino) != (before.st_dev, before.st_ino):
        raise ValueError(f"{context} was replaced while it was being collected: {source}")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _write_json_exclusive(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("generated TOML values must be finite")
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported generated TOML value: {type(value).__name__}")


def _collection_config(staged: StagedWorkflow, final_root: Path) -> ElecTraceConfig:
    metadata: dict[str, Any] = {
        "collection_schema_version": COLLECTION_SCHEMA_VERSION,
        "collection_record": str(final_root / COLLECTION_RECORD),
        "workflow_manifest": str(staged.manifest_path),
        "workflow_manifest_sha256": staged.manifest_sha256,
        "workflow_workspace_record": str(staged.record_path),
        "workflow_source_root": str(staged.source_root),
        "workflow_frame_indices": [frame.index for frame in staged.frames],
        "workflow_frame_labels": [frame.label for frame in staged.frames],
        "workflow_analysis_labels": [frame.analysis_label for frame in staged.frames],
    }
    return ElecTraceConfig(
        project_name="Collected ElecTrace workflow",
        series_kind="ordered",
        inputs=InputConfig(
            root="input",
            descriptor_dir="descriptors",
            structure_name="CONTCAR",
            fallback_structure_name="POSCAR",
            descriptor_name="features.json",
            descriptor_source="external",
            prefer_contcar=True,
        ),
        matching=MatchingSettings(),
        events=EventSettings(),
        outputs=OutputConfig(directory="results"),
        metadata=metadata,
    )


def _render_config(config: ElecTraceConfig) -> str:
    matching = config.matching
    events = config.events
    output = config.outputs
    inputs = config.inputs
    lines = [
        "# Generated by ElecTrace workflow collection. Original inputs remain read-only.",
        "[project]",
        f"name = {_toml_string(config.project_name)}",
        f"series_kind = {_toml_string(config.series_kind)}",
        "",
        "[inputs]",
        f"root = {_toml_string(inputs.root)}",
        f"descriptor_dir = {_toml_string(inputs.descriptor_dir or '')}",
        f"structure_name = {_toml_string(inputs.structure_name)}",
        f"fallback_structure_name = {_toml_string(inputs.fallback_structure_name)}",
        f"descriptor_name = {_toml_string(inputs.descriptor_name)}",
        f"descriptor_source = {_toml_string(inputs.descriptor_source)}",
        f"prefer_contcar = {_toml_value(inputs.prefer_contcar)}",
        "",
        "[matching]",
    ]
    for name in (
        "max_atom_displacement",
        "max_feature_displacement",
        "position_weight",
        "charge_weight",
        "volume_weight",
        "neighbor_weight",
        "overlap_weight",
        "descriptor_weight",
        "match_threshold",
        "ambiguity_tolerance",
        "conservation_tolerance",
        "split_merge_threshold",
    ):
        lines.append(f"{name} = {_toml_value(getattr(matching, name))}")
    lines.extend(("", "[events]"))
    for name in (
        "minimum_persistence",
        "hysteresis_frames",
        "minimum_event_confidence",
        "charge_change_threshold",
        "volume_change_fraction",
        "high_confidence_threshold",
        "medium_confidence_threshold",
    ):
        lines.append(f"{name} = {_toml_value(getattr(events, name))}")
    lines.extend(("", "[events.descriptor_thresholds]"))
    lines.extend(
        f"{key} = {_toml_value(value)}"
        for key, value in sorted(events.descriptor_thresholds.items())
    )
    lines.extend(
        (
            "",
            "[outputs]",
            f"directory = {_toml_string(output.directory)}",
            f"html_report = {_toml_value(output.html_report)}",
            f"json = {_toml_value(output.json)}",
            f"csv = {_toml_value(output.csv)}",
            f"hdf5 = {_toml_value(output.hdf5)}",
            "",
            "[metadata]",
        )
    )
    lines.extend(f"{key} = {_toml_value(value)}" for key, value in config.metadata.items())
    return "\n".join(lines) + "\n"


def _expected_descriptor_job_ids(
    staged: StagedWorkflow, manifest: WorkflowManifest
) -> tuple[str, ...]:
    descriptor_steps = tuple(step for step in manifest.steps if step.kind != "vasp")
    if not descriptor_steps:
        raise ValueError("workflow has no descriptor steps to collect")
    return tuple(
        f"f{frame.index:06d}.{step.id}" for frame in staged.frames for step in descriptor_steps
    )


def _collection_plans(staged: StagedWorkflow, manifest: WorkflowManifest) -> tuple[_FilePlan, ...]:
    _require_directory(staged.workspace, "staged workflow workspace")
    _require_directory(staged.source_root, "workflow source root")
    _require_regular_file(staged.record_path, "staged workflow record")
    if not staged.frames:
        raise ValueError("staged workflow has no selected frames")

    labels: set[str] = set()
    frames_by_index: dict[int, StagedFrame] = {}
    parsed_frames: dict[int, Frame] = {}
    plans: list[_FilePlan] = []
    destinations: dict[Path, str] = {}
    for order, frame in enumerate(staged.frames):
        label = _safe_analysis_label(frame.analysis_label, order)
        if label in labels:
            raise ValueError(f"duplicate staged analysis_label: {label!r}")
        labels.add(label)
        if frame.index in frames_by_index:
            raise ValueError(f"duplicate staged frame index: {frame.index}")
        frames_by_index[frame.index] = frame
        source_directory = _within(
            frame.source_directory,
            staged.source_root,
            "staged frame source directory",
            allow_root=True,
        )
        _require_directory(source_directory, "staged frame source directory")
        structure = _within(frame.structure, source_directory, "selected structure")
        _require_regular_file(structure, "selected structure")
        parsed_frames[frame.index] = read_poscar(
            structure,
            index=frame.index,
            label=frame.label,
            energy=frame.energy_ev,
        )
        frame_workspace = _within(
            frame.workspace_directory, staged.workspace, "staged frame workspace"
        )
        _require_directory(frame_workspace, "staged frame workspace")
        structure_destination = Path("input") / label / "CONTCAR"
        destinations[structure_destination] = "selected structure"
        plans.append(
            _FilePlan(
                relative_path=structure_destination,
                role="structure",
                frame=frame,
                source=structure,
            )
        )
        if frame.energy_ev is not None:
            if not math.isfinite(frame.energy_ev):
                raise ValueError(f"recorded energy for frame {frame.label!r} must be finite")
            energy_destination = Path("input") / label / "OSZICAR"
            destinations[energy_destination] = "recorded energy"
            energy = f" 1 F= {frame.energy_ev:.16g} E0= {frame.energy_ev:.16g} d E = 0.0\n"
            plans.append(
                _FilePlan(
                    relative_path=energy_destination,
                    role="synthetic_energy",
                    frame=frame,
                    content=energy,
                )
            )

    expected_ids = _expected_descriptor_job_ids(staged, manifest)
    if staged.descriptor_job_ids != expected_ids:
        raise ValueError("staged descriptor job ids do not match the workflow manifest")
    jobs = {job.id: job for job in staged.jobs}
    recognized = descriptor_outputs(staged)
    for job_id in expected_ids:
        job = jobs.get(job_id)
        if job is None:
            raise ValueError(f"staged workflow is missing descriptor job {job_id!r}")
        descriptor_frame = frames_by_index.get(job.frame_index)
        if descriptor_frame is None:
            raise ValueError(f"descriptor job {job_id!r} refers to an unselected frame")
        if job.frame_label != descriptor_frame.label:
            raise ValueError(f"descriptor job {job_id!r} has inconsistent frame provenance")
        cwd = _within(job.cwd, staged.workspace, f"descriptor job {job_id!r} directory")
        _require_directory(cwd, f"descriptor job {job_id!r} directory")
        outputs = recognized.get(job_id, ())
        if not outputs:
            raise ValueError(
                f"descriptor job {job_id!r} declares no parser-supported output; "
                "use a canonical output filename"
            )
        for source in outputs:
            source = _within(source, cwd, f"parser-supported descriptor output for job {job_id!r}")
            _require_regular_file(source, f"parser-supported descriptor output for job {job_id!r}")
            load_descriptor(source, frame=parsed_frames[job.frame_index])
            destination = Path("descriptors") / descriptor_frame.analysis_label / source.name
            previous = destinations.get(destination)
            if previous is not None:
                raise ValueError(
                    f"descriptor output collision at {destination}: {previous} and {job_id}"
                )
            destinations[destination] = job_id
            plans.append(
                _FilePlan(
                    relative_path=destination,
                    role="descriptor",
                    frame=descriptor_frame,
                    source=source,
                    job_id=job_id,
                )
            )
    return tuple(plans)


def _collection_marker(path: Path, staged: StagedWorkflow) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_dir():
        return None
    marker = path / COLLECTION_RECORD
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        payload = json.loads(
            read_limited_text(
                marker,
                limit=MAX_COLLECTION_RECORD_BYTES,
                context="collection marker",
            )
        )
    except (OSError, RecursionError, ValueError, json.JSONDecodeError):
        return None
    if not (
        isinstance(payload, dict)
        and payload.get("schema_version") == COLLECTION_SCHEMA_VERSION
        and payload.get("analysis_root") == str(path)
        and payload.get("workflow_workspace_record") == str(staged.record_path)
        and payload.get("workflow_manifest_sha256") == staged.manifest_sha256
    ):
        return None
    return payload


def _collection_is_managed(path: Path, staged: StagedWorkflow) -> bool:
    return _collection_marker(path, staged) is not None


def _existing_collection_artifacts(path: Path, staged: StagedWorkflow) -> CollectionArtifacts:
    _require_directory(staged.workspace, "staged workflow workspace")
    payload = _collection_marker(path, staged)
    if payload is None:
        raise ValueError(f"existing collection is not marker-bound to this workflow: {path}")
    input_root = path / "input"
    descriptor_root = path / "descriptors"
    config_path = path / "electrace.toml"
    results = path / "results"
    _require_directory(input_root, "collected input root")
    _require_directory(descriptor_root, "collected descriptor root")
    _require_directory(results, "collected results directory")
    _require_regular_file(config_path, "collected ElecTrace configuration")

    raw_records = payload.get("files")
    if not isinstance(raw_records, list):
        raise ValueError("collection marker files must be an array")
    files: list[Path] = []
    seen: set[Path] = set()
    for index, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, dict):
            raise ValueError(f"collection marker files[{index}] must be an object")
        raw_relative = raw_record.get("path")
        if not isinstance(raw_relative, str):
            raise ValueError(f"collection marker files[{index}].path must be a string")
        relative = Path(raw_relative)
        if relative.is_absolute() or ".." in relative.parts or relative in seen:
            raise ValueError(f"unsafe or duplicate collection marker path: {raw_relative!r}")
        seen.add(relative)
        file_path = _within(path / relative, path, "collected file")
        status = _require_regular_file(file_path, "collected file")
        if raw_record.get("size_bytes") != status.st_size:
            raise ValueError(f"collected file size does not match marker: {file_path}")
        if raw_record.get("sha256") != sha256_file(file_path):
            raise ValueError(f"collected file hash does not match marker: {file_path}")
        files.append(file_path)
    if Path("electrace.toml") not in seen:
        raise ValueError("collection marker does not include electrace.toml")
    files.append(path / COLLECTION_RECORD)
    return CollectionArtifacts(
        config_path=config_path,
        input_root=input_root,
        descriptor_root=descriptor_root,
        files=tuple(sorted(files, key=lambda item: item.relative_to(path).as_posix())),
    )


def _publish_collection(
    temporary: Path, target: Path, staged: StagedWorkflow, *, overwrite: bool
) -> None:
    if not (target.exists() or target.is_symlink()):
        temporary.replace(target)
        return
    if not overwrite:
        raise FileExistsError(
            f"collected analysis already exists: {target}; use overwrite=True to replace it"
        )
    if not _collection_is_managed(target, staged):
        raise ValueError(f"refusing to replace unmarked or symlinked collection: {target}")

    backup = Path(tempfile.mkdtemp(prefix=".analysis.previous-", dir=target.parent))
    backup.rmdir()
    target.replace(backup)
    try:
        temporary.replace(target)
    except BaseException:
        backup.replace(target)
        raise
    shutil.rmtree(backup)


def _marker_payload(
    staged: StagedWorkflow,
    final_root: Path,
    temporary: Path,
    plans: tuple[_FilePlan, ...],
) -> dict[str, Any]:
    provenance = {plan.relative_path: plan for plan in plans}
    records: list[dict[str, Any]] = []
    for path in sorted(
        (item for item in temporary.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(temporary).as_posix(),
    ):
        relative = path.relative_to(temporary)
        if relative == Path(COLLECTION_RECORD):
            continue
        plan = provenance.get(relative)
        if relative == Path("electrace.toml"):
            role = "configuration"
        elif plan is not None:
            role = plan.role
        else:  # pragma: no cover - internal staging invariant
            raise RuntimeError(f"unplanned collection file: {relative}")
        record: dict[str, Any] = {
            "path": relative.as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "role": role,
        }
        if plan is not None and plan.source is not None:
            record["source_path"] = str(plan.source)
            # The collected digest is the exact source snapshot copied through
            # the no-follow descriptor, even if the live source changes later.
            record["source_sha256"] = record["sha256"]
        if plan is not None and plan.frame is not None:
            record["frame"] = {
                "index": plan.frame.index,
                "label": plan.frame.label,
                "analysis_label": plan.frame.analysis_label,
            }
        if plan is not None and plan.job_id is not None:
            record["job_id"] = plan.job_id
        records.append(record)
    return {
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "analysis_root": str(final_root),
        "workflow_manifest": str(staged.manifest_path),
        "workflow_manifest_sha256": staged.manifest_sha256,
        "workflow_workspace_record": str(staged.record_path),
        "input_root": str(final_root / "input"),
        "descriptor_root": str(final_root / "descriptors"),
        "config_path": str(final_root / "electrace.toml"),
        "results_directory": str(final_root / "results"),
        "files": records,
    }


def collect_workflow_outputs(
    manifest_path: str | Path,
    *,
    overwrite: bool = False,
) -> CollectionArtifacts:
    """Validate and atomically collect all staged descriptor outputs."""

    requested_manifest = absolute_lexical(Path(manifest_path))
    _reject_symlink_path(requested_manifest, "workflow manifest")
    staged = load_staged_workflow(requested_manifest)
    final_root = staged.workspace / COLLECTION_DIRECTORY
    if final_root.exists() or final_root.is_symlink():
        if not overwrite:
            raise FileExistsError(
                f"collected analysis already exists: {final_root}; use overwrite=True to replace it"
            )
        if not _collection_is_managed(final_root, staged):
            raise ValueError(f"refusing to replace unmarked or symlinked collection: {final_root}")

    manifest = load_workflow_manifest(staged.manifest_path)
    plans = _collection_plans(staged, manifest)

    temporary = Path(tempfile.mkdtemp(prefix=".analysis.collect-", dir=staged.workspace))
    relative_files: tuple[Path, ...] = ()
    try:
        (temporary / "input").mkdir()
        (temporary / "descriptors").mkdir()
        (temporary / "results").mkdir()
        for plan in plans:
            destination = temporary / plan.relative_path
            if plan.source is not None:
                _copy_regular_file(plan.source, destination, plan.role)
            else:
                assert plan.content is not None
                _write_text(destination, plan.content)

        config = _collection_config(staged, final_root)
        config_path = temporary / "electrace.toml"
        _write_text(config_path, _render_config(config))
        # Defense in depth: the generated text must round-trip through the same
        # strict configuration loader used by analysis.
        load_config(config_path)
        marker = _marker_payload(staged, final_root, temporary, plans)
        _write_json_exclusive(temporary / COLLECTION_RECORD, marker)
        relative_files = tuple(
            sorted(
                (path.relative_to(temporary) for path in temporary.rglob("*") if path.is_file()),
                key=Path.as_posix,
            )
        )
        _publish_collection(temporary, final_root, staged, overwrite=overwrite)
    except BaseException:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary, ignore_errors=True)
        raise

    return CollectionArtifacts(
        config_path=final_root / "electrace.toml",
        input_root=final_root / "input",
        descriptor_root=final_root / "descriptors",
        files=tuple(final_root / relative for relative in relative_files),
    )


def analyze_collected_workflow(
    manifest_path: str | Path,
    *,
    strict: bool = True,
    overwrite: bool = False,
) -> AnalysisArtifacts:
    """Collect a staged workflow and run the normal ElecTrace analysis pipeline."""

    if overwrite:
        collected = collect_workflow_outputs(manifest_path, overwrite=True)
    else:
        requested_manifest = absolute_lexical(Path(manifest_path))
        _reject_symlink_path(requested_manifest, "workflow manifest")
        staged = load_staged_workflow(requested_manifest)
        final_root = staged.workspace / COLLECTION_DIRECTORY
        collected = (
            _existing_collection_artifacts(final_root, staged)
            if final_root.exists() or final_root.is_symlink()
            else collect_workflow_outputs(requested_manifest)
        )
    return run_project(
        collected.config_path,
        strict=strict,
        overwrite=overwrite,
    )


__all__ = [
    "COLLECTION_DIRECTORY",
    "COLLECTION_RECORD",
    "COLLECTION_SCHEMA_VERSION",
    "CollectionArtifacts",
    "analyze_collected_workflow",
    "collect_workflow_outputs",
]
