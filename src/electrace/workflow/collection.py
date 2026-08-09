"""Collect completed workflow outputs into a canonical analysis tree."""

from __future__ import annotations

import json
import math
import shutil
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
from electrace.pipeline import AnalysisArtifacts, run_project

from .manifest import load_workflow_manifest
from .models import WorkflowManifest
from .staging import StagedWorkflow, descriptor_outputs, load_staged_workflow

COLLECTION_SCHEMA_VERSION = "electrace.collection.v1"
COLLECTION_DIRECTORY = "analysis"
COLLECTION_RECORD = ".electrace-collection.json"


@dataclass(frozen=True, slots=True)
class CollectionArtifacts:
    config_path: Path
    input_root: Path
    descriptor_root: Path
    files: tuple[Path, ...]


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported generated TOML value: {type(value).__name__}")


def _collection_config(staged: StagedWorkflow, final_root: Path) -> ElecTraceConfig:
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
        metadata={
            "collection_schema_version": COLLECTION_SCHEMA_VERSION,
            "collection_record": str(final_root / COLLECTION_RECORD),
            "workflow_manifest": str(staged.manifest_path),
            "workflow_manifest_sha256": staged.manifest_sha256,
            "workflow_workspace_record": str(staged.record_path),
            "workflow_source_root": str(staged.source_root),
            "workflow_frame_indices": [frame.index for frame in staged.frames],
            "workflow_frame_labels": [frame.label for frame in staged.frames],
            "workflow_analysis_labels": [frame.analysis_label for frame in staged.frames],
        },
    )


def _render_config(config: ElecTraceConfig) -> str:
    inputs, matching, events, output = (
        config.inputs,
        config.matching,
        config.events,
        config.outputs,
    )
    lines = [
        "[project]",
        f"name = {_toml_value(config.project_name)}",
        f"series_kind = {_toml_value(config.series_kind)}",
        "",
        "[inputs]",
        f"root = {_toml_value(inputs.root)}",
        f"descriptor_dir = {_toml_value(inputs.descriptor_dir or '')}",
        f"structure_name = {_toml_value(inputs.structure_name)}",
        f"fallback_structure_name = {_toml_value(inputs.fallback_structure_name)}",
        f"descriptor_name = {_toml_value(inputs.descriptor_name)}",
        f"descriptor_source = {_toml_value(inputs.descriptor_source)}",
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
        "conservation_tolerance",
        "split_merge_threshold",
    ):
        lines.append(f"{name} = {_toml_value(getattr(matching, name))}")
    lines.extend(("", "[events]"))
    for name in (
        "minimum_persistence",
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
            f"directory = {_toml_value(output.directory)}",
            f"html_report = {_toml_value(output.html_report)}",
            f"json = {_toml_value(output.json)}",
            f"csv = {_toml_value(output.csv)}",
            "",
            "[metadata]",
        )
    )
    lines.extend(f"{key} = {_toml_value(value)}" for key, value in config.metadata.items())
    return "\n".join(lines) + "\n"


def _expected_descriptor_ids(staged: StagedWorkflow, manifest: WorkflowManifest) -> tuple[str, ...]:
    steps = tuple(step for step in manifest.steps if step.kind != "vasp")
    if not steps:
        raise ValueError("workflow has no descriptor steps to collect")
    return tuple(f"f{frame.index:06d}.{step.id}" for frame in staged.frames for step in steps)


def _managed(path: Path, staged: StagedWorkflow) -> dict[str, Any] | None:
    marker = path / COLLECTION_RECORD
    if path.is_symlink() or marker.is_symlink() or not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
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


def _publish(temporary: Path, target: Path, staged: StagedWorkflow, overwrite: bool) -> None:
    if not target.exists():
        temporary.replace(target)
        return
    if not overwrite:
        raise FileExistsError(f"collected analysis already exists: {target}")
    if _managed(target, staged) is None:
        raise ValueError(f"refusing to replace unmarked collection: {target}")
    backup = Path(tempfile.mkdtemp(prefix=".analysis.previous-", dir=target.parent))
    backup.rmdir()
    target.replace(backup)
    try:
        temporary.replace(target)
    except BaseException:
        backup.replace(target)
        raise
    shutil.rmtree(backup)


def _existing(path: Path, staged: StagedWorkflow) -> CollectionArtifacts:
    payload = _managed(path, staged)
    if payload is None:
        raise ValueError(f"existing collection is not owned by this workflow: {path}")
    required = (path / "electrace.toml", path / "input", path / "descriptors", path / "results")
    if not required[0].is_file() or any(not item.is_dir() for item in required[1:]):
        raise ValueError(f"existing collection is incomplete: {path}")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or any(not isinstance(item, str) for item in raw_files):
        raise ValueError("collection marker has invalid files")
    files = tuple(path / item for item in raw_files)
    if any(not item.is_file() for item in files):
        raise ValueError("collection marker names a missing file")
    return CollectionArtifacts(required[0], required[1], required[2], files)


def collect_workflow_outputs(
    manifest_path: str | Path,
    *,
    overwrite: bool = False,
) -> CollectionArtifacts:
    """Collect selected structures and parser-supported descriptor outputs."""

    staged = load_staged_workflow(manifest_path)
    final_root = staged.workspace / COLLECTION_DIRECTORY
    if final_root.exists() or final_root.is_symlink():
        if not overwrite:
            raise FileExistsError(f"collected analysis already exists: {final_root}")
        if _managed(final_root, staged) is None:
            raise ValueError(f"refusing to replace unmarked collection: {final_root}")
    manifest = load_workflow_manifest(staged.manifest_path)
    expected_ids = _expected_descriptor_ids(staged, manifest)
    if staged.descriptor_job_ids != expected_ids:
        raise ValueError("staged descriptor jobs do not match the manifest")
    jobs = {job.id: job for job in staged.jobs}
    recognized = descriptor_outputs(staged)
    temporary = Path(tempfile.mkdtemp(prefix=".analysis.collect-", dir=staged.workspace))
    written: list[Path] = []
    try:
        for name in ("input", "descriptors", "results"):
            (temporary / name).mkdir()
        frames = {}
        for frame in staged.frames:
            if frame.structure.is_symlink() or not frame.structure.is_file():
                raise FileNotFoundError(
                    f"selected structure is not a regular file: {frame.structure}"
                )
            destination = Path("input") / frame.analysis_label / "CONTCAR"
            (temporary / destination).parent.mkdir()
            shutil.copy2(frame.structure, temporary / destination)
            written.append(destination)
            frames[frame.index] = read_poscar(
                frame.structure,
                index=frame.index,
                label=frame.label,
                energy=frame.energy_ev,
            )
            if frame.energy_ev is not None:
                energy = Path("input") / frame.analysis_label / "OSZICAR"
                (temporary / energy).write_text(
                    f" 1 F= {frame.energy_ev:.16g} E0= {frame.energy_ev:.16g} d E = 0.0\n",
                    encoding="utf-8",
                )
                written.append(energy)

        destinations: set[Path] = set()
        for job_id in expected_ids:
            job = jobs.get(job_id)
            outputs = recognized.get(job_id, ())
            if job is None or not outputs:
                raise ValueError(f"descriptor job {job_id!r} has no supported output")
            for source in outputs:
                if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
                    raise FileNotFoundError(f"descriptor output is missing or invalid: {source}")
                load_descriptor(source, frame=frames[job.frame_index])
                label = next(
                    frame.analysis_label
                    for frame in staged.frames
                    if frame.index == job.frame_index
                )
                destination = Path("descriptors") / label / source.name
                if destination in destinations:
                    raise ValueError(f"descriptor output collision at {destination}")
                destinations.add(destination)
                (temporary / destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, temporary / destination)
                written.append(destination)

        config_path = temporary / "electrace.toml"
        config_path.write_text(
            _render_config(_collection_config(staged, final_root)), encoding="utf-8", newline="\n"
        )
        load_config(config_path)
        written.append(Path("electrace.toml"))
        marker = {
            "schema_version": COLLECTION_SCHEMA_VERSION,
            "analysis_root": str(final_root),
            "workflow_workspace_record": str(staged.record_path),
            "workflow_manifest_sha256": staged.manifest_sha256,
            "files": [item.as_posix() for item in sorted(written, key=Path.as_posix)],
        }
        (temporary / COLLECTION_RECORD).write_text(
            json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written.append(Path(COLLECTION_RECORD))
        _publish(temporary, final_root, staged, overwrite)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    return CollectionArtifacts(
        final_root / "electrace.toml",
        final_root / "input",
        final_root / "descriptors",
        tuple(final_root / item for item in written),
    )


def analyze_collected_workflow(
    manifest_path: str | Path,
    *,
    strict: bool = True,
    overwrite: bool = False,
) -> AnalysisArtifacts:
    if overwrite:
        collected = collect_workflow_outputs(manifest_path, overwrite=True)
    else:
        staged = load_staged_workflow(manifest_path)
        path = staged.workspace / COLLECTION_DIRECTORY
        collected = (
            _existing(path, staged) if path.exists() else collect_workflow_outputs(manifest_path)
        )
    return run_project(collected.config_path, strict=strict, overwrite=overwrite)


__all__ = [
    "COLLECTION_DIRECTORY",
    "COLLECTION_RECORD",
    "COLLECTION_SCHEMA_VERSION",
    "CollectionArtifacts",
    "analyze_collected_workflow",
    "collect_workflow_outputs",
]
