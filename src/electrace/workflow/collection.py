"""Collect completed workflow outputs into a canonical analysis tree."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from electrace._files import publish_directory
from electrace.config import EventSettings, MatchingSettings, OutputConfig, load_config
from electrace.io.descriptors import NET_CHARGE_SOURCES, SPIN_DESCRIPTOR, load_descriptor
from electrace.io.discovery import pressure_from_label, pressure_from_outcar
from electrace.io.vasp import read_poscar
from electrace.pipeline import AnalysisArtifacts, run_project

from .models import WorkflowManifest
from .staging import StagedFrame, StagedWorkflow, descriptor_outputs, load_staged_workflow

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
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return repr(value)


def _numeric_settings(settings: MatchingSettings | EventSettings) -> list[str]:
    """Return a TOML line for each numeric setting, in declaration order."""

    return [
        f"{key} = {_toml_value(value)}"
        for key, value in asdict(settings).items()
        if isinstance(value, (int, float))
    ]


def _series_kind(staged: StagedWorkflow, collected_input: Path) -> str:
    """Return the series kind for collected frames, whose labels number the staged order.

    ``ordered`` keeps that order. A pressure workflow stays a pressure series, which no
    barrier applies to, when its collected frames still show their pressures.
    """

    if staged.manifest.project.series_kind == "pressure" and all(
        pressure_from_label(frame.analysis_label) is not None
        or pressure_from_outcar(collected_input / frame.analysis_label / "OUTCAR") is not None
        for frame in staged.frames
    ):
        return "pressure"
    return "ordered"


def _render_config(staged: StagedWorkflow, final_root: Path, series_kind: str) -> str:
    events, output = EventSettings(), OutputConfig()
    metadata: dict[str, object] = {
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
    if staged.warnings:
        # Collected labels always number the order, so analysis cannot see the source names.
        metadata["workflow_order_warnings"] = list(staged.warnings)
    pressure = staged.manifest.project.pressure_gpa
    lines = [
        "[project]",
        'name = "Collected ElecTrace workflow"',
        f"series_kind = {_toml_value(series_kind)}",
        *(() if pressure is None else (f"pressure_gpa = {_toml_value(pressure)}",)),
        "",
        "[inputs]",
        'root = "input"',
        'descriptor_dir = "descriptors"',
        'structure_name = "CONTCAR"',
        'fallback_structure_name = "POSCAR"',
        'descriptor_name = "features.json"',
        'descriptor_source = "external"',
        "prefer_contcar = true",
        "",
        "[matching]",
        *_numeric_settings(MatchingSettings()),
        "",
        "[events]",
        *_numeric_settings(events),
        "",
        "[events.descriptor_thresholds]",
        *(
            f"{key} = {_toml_value(value)}"
            for key, value in sorted(events.descriptor_thresholds.items())
        ),
        "",
        "[outputs]",
        'directory = "results"',
        f"html_report = {_toml_value(output.html_report)}",
        f"json = {_toml_value(output.json)}",
        f"csv = {_toml_value(output.csv)}",
        "",
        "[metadata]",
        *(f"{key} = {_toml_value(value)}" for key, value in metadata.items()),
    ]
    return "\n".join(lines) + "\n"


def _zval_source(manifest: WorkflowManifest, frame: StagedFrame) -> Path | None:
    """Return the POTCAR or OUTCAR that gives ZVAL for a frame's collected Bader charges.

    A VASP step's POTCAR comes first, since that step ran the density. Without one, the
    source frame's own POTCAR or OUTCAR is used. A VASP step's OUTCAR is not collected:
    its energy would replace the source frame's.
    """

    candidates = [
        frame.workspace_directory / step.id / "POTCAR"
        for step in manifest.steps
        if step.kind == "vasp"
    ]
    candidates.extend(frame.source_directory / name for name in NET_CHARGE_SOURCES)
    return next((path for path in candidates if path.is_file()), None)


def _managed(path: Path, staged: StagedWorkflow) -> dict[str, Any] | None:
    marker = path / COLLECTION_RECORD
    if path.is_symlink() or marker.is_symlink() or not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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


def _existing(path: Path, staged: StagedWorkflow) -> Path:
    """Return the configuration path of a complete collection owned by this workflow."""

    payload = _managed(path, staged)
    if payload is None:
        raise ValueError(f"existing collection is not owned by this workflow: {path}")
    config_path = path / "electrace.toml"
    if not config_path.is_file() or any(
        not (path / name).is_dir() for name in ("input", "descriptors", "results")
    ):
        raise ValueError(f"existing collection is incomplete: {path}")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or any(not isinstance(item, str) for item in raw_files):
        raise ValueError("collection marker has invalid files")
    if any(not (path / item).is_file() for item in raw_files):
        raise ValueError("collection marker names a missing file")
    return config_path


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
    if not staged.descriptor_job_ids:
        raise ValueError("workflow has no descriptor steps to collect")
    jobs = {job.id: job for job in staged.jobs}
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
            zval = _zval_source(staged.manifest, frame)
            if zval is not None:
                valence = Path("input") / frame.analysis_label / zval.name
                shutil.copy2(zval, temporary / valence)
                written.append(valence)

        destinations: set[Path] = set()
        steps = {step.id: step for step in staged.manifest.steps}
        labels = {frame.index: frame.analysis_label for frame in staged.frames}
        for job_id, outputs in descriptor_outputs(staged).items():
            if not outputs:
                raise ValueError(f"descriptor job {job_id!r} has no supported output")
            job = jobs[job_id]
            # A spin step's ACF.dat holds magnetizations, which analysis reads from spin/.
            spin = steps[job.task].capability == "spin_populations"
            for source in outputs:
                if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
                    raise FileNotFoundError(f"descriptor output is missing or invalid: {source}")
                load_descriptor(source, frame=frames[job.frame_index])
                relative = SPIN_DESCRIPTOR if spin else source.name
                destination = Path("descriptors") / labels[job.frame_index] / relative
                if destination in destinations:
                    raise ValueError(f"descriptor output collision at {destination}")
                destinations.add(destination)
                (temporary / destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, temporary / destination)
                written.append(destination)

        config_path = temporary / "electrace.toml"
        series_kind = _series_kind(staged, temporary / "input")
        config_path.write_text(
            _render_config(staged, final_root, series_kind), encoding="utf-8", newline="\n"
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
        if final_root.exists():
            if not overwrite:
                raise FileExistsError(f"collected analysis already exists: {final_root}")
            if _managed(final_root, staged) is None:
                raise ValueError(f"refusing to replace unmarked collection: {final_root}")
        publish_directory(temporary, final_root)
    except BaseException:
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
        config_path = collect_workflow_outputs(manifest_path, overwrite=True).config_path
    else:
        staged = load_staged_workflow(manifest_path)
        path = staged.workspace / COLLECTION_DIRECTORY
        config_path = (
            _existing(path, staged)
            if path.exists()
            else collect_workflow_outputs(manifest_path).config_path
        )
    return run_project(config_path, strict=strict, overwrite=overwrite)
