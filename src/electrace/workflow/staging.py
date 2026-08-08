"""Safe staging of executable ElecTrace workflows.

The source VASP path is always read-only.  Every executable runs in a fresh,
marker-protected workspace populated from reviewed template directories and
explicit dependency links.  The resulting :class:`JobSpec` records are the
only objects accepted by the separate execution boundary.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from electrace.io.discovery import FrameSource, discover_path
from electrace.plugins import plan_plugin
from electrace.provenance import sha256_file

from ._files import (
    absolute_lexical,
    atomic_write_json,
    read_limited_text,
    symlink_component,
)
from .execution import JobSpec, validate_jobs
from .manifest import load_workflow_manifest
from .models import ExecutableSpec, JSONValue, WorkflowManifest, WorkflowStep
from .selection import resolve_selected_frames

WORKSPACE_SCHEMA_VERSION = "electrace.workspace.v1"
WORKSPACE_RECORD = ".electrace-workspace.json"
MAX_WORKSPACE_RECORD_BYTES = 16_000_000
_INCAR_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_.-]+")
_LOBSTER_BOND_REQUEST = re.compile(r"(?im)^\s*(?:cohpgenerator|cohpbetween)\b")
_LOBSTER_SKIP_COBI = re.compile(r"(?im)^\s*skipcobi\b")
_DESCRIPTOR_FILENAMES = frozenset(
    {
        "ACF.dat",
        "DDEC6_even_tempered_net_atomic_charges.xyz",
        "ICOBILIST.lobster",
        "ICOHPLIST.lobster",
        "ICOOPLIST.lobster",
        "badelf.json",
        "bader.json",
        "critic2.features.json",
        "electrace.features.json",
        "features.json",
    }
)


def _resolve_from(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return absolute_lexical(candidate if candidate.is_absolute() else base / candidate)


def _resolve_command(base: Path, value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or "/" in value or "\\" in value:
        return str(_resolve_from(base, value))
    return value


def _basename(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    name = value.strip()
    if (
        not name
        or name in {".", ".."}
        or name != value
        or "/" in name
        or "\\" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ValueError(f"{path} must be a safe basename, not a path: {value!r}")
    return name


def _string_list(value: JSONValue | None, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"{path} must be a list")
    return tuple(_basename(item, f"{path}[{index}]") for index, item in enumerate(value))


def _string_mapping(value: JSONValue | None, path: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError(f"{path} must contain string keys and values")
        result[key] = item
    return result


def _safe_label(index: int, label: str) -> str:
    suffix = _SAFE_LABEL.sub("-", label).strip(".-") or "frame"
    return f"{index:06d}-{suffix[:80]}"


def _record_string(data: dict[str, object], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{context} {key} must be a string")
    return value


def _record_list(data: dict[str, object], key: str, context: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{context} {key} must be an array")
    return cast(list[object], value)


def _record_string_list(data: dict[str, object], key: str, context: str) -> tuple[str, ...]:
    values = _record_list(data, key, context)
    if any(not isinstance(value, str) for value in values):
        raise TypeError(f"{context} {key} must contain only strings")
    return tuple(cast(list[str], values))


@dataclass(frozen=True, slots=True)
class StagedFrame:
    """One selected source frame and its dedicated workspace directory."""

    index: int
    label: str
    analysis_label: str
    source_directory: Path
    structure: Path
    workspace_directory: Path
    energy_ev: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("staged frame index must be an integer")
        if self.index < 0:
            raise ValueError("staged frame index must be non-negative")
        if self.energy_ev is not None:
            if isinstance(self.energy_ev, bool) or not isinstance(self.energy_ev, (int, float)):
                raise TypeError("staged frame energy must be numeric or null")
            if not math.isfinite(self.energy_ev):
                raise ValueError("staged frame energy must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "analysis_label": self.analysis_label,
            "source_directory": str(self.source_directory),
            "structure": str(self.structure),
            "workspace_directory": str(self.workspace_directory),
            "energy_ev": self.energy_ev,
        }

    @classmethod
    def from_dict(cls, payload: object) -> StagedFrame:
        if not isinstance(payload, dict):
            raise TypeError("workspace frame record must be an object")
        data = cast(dict[str, object], payload)
        raw_index = data.get("index")
        raw_energy = data.get("energy_ev")
        if type(raw_index) is not int:
            raise TypeError("workspace frame index must be an integer")
        if raw_energy is not None and (
            isinstance(raw_energy, bool) or not isinstance(raw_energy, (int, float))
        ):
            raise TypeError("workspace frame energy must be numeric or null")
        return cls(
            index=raw_index,
            label=_record_string(data, "label", "workspace frame"),
            analysis_label=_record_string(data, "analysis_label", "workspace frame"),
            source_directory=Path(_record_string(data, "source_directory", "workspace frame")),
            structure=Path(_record_string(data, "structure", "workspace frame")),
            workspace_directory=Path(
                _record_string(data, "workspace_directory", "workspace frame")
            ),
            energy_ev=None if raw_energy is None else float(raw_energy),
        )


@dataclass(frozen=True, slots=True)
class StagedWorkflow:
    """Auditable output of workflow staging."""

    manifest_path: Path
    manifest_sha256: str
    source_root: Path
    workspace: Path
    frames: tuple[StagedFrame, ...]
    jobs: tuple[JobSpec, ...]
    descriptor_job_ids: tuple[str, ...]

    @property
    def record_path(self) -> Path:
        return self.workspace / WORKSPACE_RECORD

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "source_root": str(self.source_root),
            "workspace": str(self.workspace),
            "frames": [frame.to_dict() for frame in self.frames],
            "jobs": [job.to_dict() for job in self.jobs],
            "descriptor_job_ids": list(self.descriptor_job_ids),
        }


def _copy_template(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"workflow template directory does not exist: {source}")
    if symlink_component(source) is not None:
        raise ValueError(f"workflow template directory must not traverse a symlink: {source}")
    for item in sorted(source.iterdir(), key=lambda path: path.name):
        if item.is_symlink():
            raise ValueError(f"workflow templates must not contain symlinks: {item}")
        if not item.is_file():
            raise ValueError(f"workflow templates must contain only regular files: {item}")
        shutil.copy2(item, destination / _basename(item.name, "template filename"))


def _replace_file(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    shutil.copy2(source, destination)


def _apply_incar_overrides(path: Path, raw: JSONValue | None) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise TypeError("vasp options.incar_overrides must be a mapping")
    if not path.is_file():
        raise FileNotFoundError("vasp options.incar_overrides requires an INCAR template")
    overrides: dict[str, str] = {}
    for raw_key, value in raw.items():
        if _INCAR_KEY.fullmatch(raw_key) is None:
            raise ValueError(f"invalid INCAR tag in workflow: {raw_key!r}")
        if isinstance(value, bool):
            encoded = ".TRUE." if value else ".FALSE."
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            encoded = str(value)
        else:
            raise TypeError(f"INCAR override {raw_key!r} must be a scalar")
        overrides[raw_key.upper()] = encoded

    lines = path.read_text(encoding="utf-8").splitlines()
    written: set[str] = set()
    result: list[str] = []
    for line in lines:
        content = line.split("!", 1)[0].split("#", 1)[0]
        candidate = content.split("=", 1)[0].strip().upper() if "=" in content else ""
        if candidate in overrides:
            if candidate not in written:
                result.append(f"{candidate} = {overrides[candidate]}")
                written.add(candidate)
            continue
        result.append(line)
    for key, value in overrides.items():
        if key not in written:
            result.append(f"{key} = {value}")
    path.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")


def _step_outputs(step: WorkflowStep) -> tuple[str, ...]:
    if step.kind == "vasp":
        raw_outputs = step.options.get("outputs", step.options.get("expected_outputs"))
        outputs = _string_list(raw_outputs, f"step {step.id}.options.expected_outputs")
        if not outputs:
            raise ValueError(f"vasp step {step.id!r} must declare options.expected_outputs")
        return outputs
    capability = step.capability or ""
    if step.kind == "bader":
        return ("ACF.dat",)
    if step.kind == "baderkit":
        return (
            ("badelf.json",)
            if capability in {"elf_basins", "non_nuclear_attractors"}
            else ("bader.json",)
        )
    if step.kind == "critic2":
        report = str(_plugin_options(step).get("report_file", "critic2.features.json"))
        report = _basename(report, f"step {step.id}.report_file")
        if report != "critic2.features.json":
            raise ValueError(
                "workflow critic2 report_file must be 'critic2.features.json' so collection "
                "can discover it deterministically"
            )
        return ("critic2.cro", report)
    if step.kind == "lobster":
        return ("ICOBILIST.lobster",) if capability == "cobi" else ("ICOHPLIST.lobster",)
    raise ValueError(f"unsupported workflow step kind: {step.kind}")


def _dependency_source(
    target: str,
    dependencies: tuple[WorkflowStep, ...],
    directories: dict[str, Path],
) -> Path | None:
    for dependency in reversed(dependencies):
        directory = directories[dependency.id]
        candidate = directory / target
        if candidate.exists() or target in _step_outputs(dependency):
            return candidate
    return None


def _relative_link(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"workflow input target already exists: {destination}")
    destination.symlink_to(os.path.relpath(source, start=destination.parent))


def _explicit_input_links(
    step: WorkflowStep,
    source: FrameSource,
    destination: Path,
    directories: dict[str, Path],
) -> None:
    raw = step.options.get("input_links")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise TypeError(f"step {step.id}.options.input_links must be a mapping")
    for raw_target, specification in raw.items():
        target = _basename(raw_target, f"step {step.id}.options.input_links key")
        if specification == "source:structure":
            _replace_file(source.structure, destination / target)
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
        source_name = _basename(filename, f"step {step.id}.options.input_links.{target}.file")
        _relative_link(directories[dependency] / source_name, destination / target)


def _plugin_required_names(step: WorkflowStep) -> tuple[str, ...]:
    options = _plugin_options(step)
    if step.kind == "bader":
        result = [str(options.get("charge_file", "CHGCAR"))]
        if options.get("reference_file") is not None:
            result.append(str(options["reference_file"]))
        return tuple(result)
    if step.kind == "baderkit":
        result = [str(options.get("charge_file", "CHGCAR"))]
        if step.capability in {"elf_basins", "non_nuclear_attractors"}:
            result.append(str(options.get("elf_file", "ELFCAR")))
        if options.get("reference_file") is not None:
            result.append(str(options["reference_file"]))
        return tuple(result)
    if step.kind == "critic2":
        structure = str(options.get("structure_file", "CONTCAR"))
        default_field = (
            "ELFCAR" if step.capability in {"elf_basins", "non_nuclear_attractors"} else "CHGCAR"
        )
        result = [structure, str(options.get("field_file", default_field))]
        if options.get("input_file") is not None:
            result.append(str(options["input_file"]))
        return tuple(result)
    if step.kind == "lobster":
        return tuple(
            str(options.get(key, default))
            for key, default in (
                ("input_file", "lobsterin"),
                ("wavecar", "WAVECAR"),
                ("structure_file", "POSCAR"),
                ("potcar", "POTCAR"),
            )
        )
    return ()


def _auto_plugin_inputs(
    step: WorkflowStep,
    source: FrameSource,
    destination: Path,
    dependencies: tuple[WorkflowStep, ...],
    directories: dict[str, Path],
) -> None:
    options = _plugin_options(step)
    structure_name = str(options.get("structure_file", "CONTCAR"))
    for raw_name in _plugin_required_names(step):
        name = _basename(raw_name, f"step {step.id} plugin input")
        target = destination / name
        if target.exists() or target.is_symlink():
            continue
        if step.kind in {"critic2", "lobster"} and name == structure_name:
            _replace_file(source.structure, target)
            continue
        candidate = _dependency_source(name, dependencies, directories)
        if candidate is not None:
            _relative_link(candidate, target)


def _validate_lobster_input(step: WorkflowStep, directory: Path) -> None:
    options = _plugin_options(step)
    input_name = _basename(
        options.get("input_file", "lobsterin"), f"step {step.id} lobster input_file"
    )
    path = directory / input_name
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"LOBSTER workflow requires a regular reviewed {input_name}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if _LOBSTER_BOND_REQUEST.search(text) is None:
        raise ValueError(
            f"LOBSTER step {step.id!r} must request bonds with cohpGenerator or cohpBetween"
        )
    if step.capability == "cobi" and _LOBSTER_SKIP_COBI.search(text) is not None:
        raise ValueError(f"LOBSTER COBI step {step.id!r} cannot use skipcobi")


def _prepare_step_directory(
    step: WorkflowStep,
    source: FrameSource,
    destination: Path,
    manifest_base: Path,
    prior_steps: dict[str, WorkflowStep],
    directories: dict[str, Path],
) -> None:
    destination.mkdir(parents=True)
    if step.template_dir is not None:
        _copy_template(_resolve_from(manifest_base, step.template_dir), destination)
    dependencies = tuple(prior_steps[identifier] for identifier in step.depends_on)
    if step.kind == "vasp":
        _replace_file(source.structure, destination / "POSCAR")
        _apply_incar_overrides(destination / "INCAR", step.options.get("incar_overrides"))
    else:
        _explicit_input_links(step, source, destination, directories)
        _auto_plugin_inputs(step, source, destination, dependencies, directories)
        if step.kind == "lobster":
            _validate_lobster_input(step, destination)


def _environment(step: WorkflowStep) -> dict[str, str]:
    return _string_mapping(step.options.get("environment"), f"step {step.id}.environment")


def _plugin_options(step: WorkflowStep) -> dict[str, JSONValue]:
    """Return planner options, accepting a flat public form or nested extension."""

    reserved = {"environment", "input_links", "plugin_options"}
    result = {key: value for key, value in step.options.items() if key not in reserved}
    nested = step.options.get("plugin_options")
    if nested is None:
        return result
    if not isinstance(nested, dict):
        raise TypeError(f"step {step.id}.options.plugin_options must be a mapping")
    duplicates = sorted(set(result) & set(nested))
    if duplicates:
        raise ValueError(
            f"step {step.id} repeats plugin options in flat and nested forms: "
            + ", ".join(duplicates)
        )
    result.update(nested)
    return result


def _executable_argv(base: Path, specification: ExecutableSpec) -> tuple[str, ...]:
    return (*specification.argv_prefix, _resolve_command(base, specification.path))


def _build_job(
    manifest: WorkflowManifest,
    manifest_base: Path,
    frame: StagedFrame,
    step: WorkflowStep,
    directory: Path,
) -> JobSpec:
    executable = manifest.executables[step.executable]
    command = _executable_argv(manifest_base, executable)
    dependencies = tuple(f"f{frame.index:06d}.{identifier}" for identifier in step.depends_on)
    job_id = f"f{frame.index:06d}.{step.id}"
    if step.kind == "vasp":
        raw_arguments = step.options.get("arguments", [])
        if not isinstance(raw_arguments, list) or any(
            not isinstance(token, str) or not token for token in raw_arguments
        ):
            raise TypeError(f"step {step.id}.options.arguments must be a list of strings")
        expected = tuple(directory / name for name in _step_outputs(step))
        required_names = _string_list(
            step.options.get("required_inputs"), f"step {step.id}.options.required_inputs"
        ) or ("INCAR", "KPOINTS", "POSCAR", "POTCAR")
        return JobSpec(
            id=job_id,
            frame_index=frame.index,
            frame_label=frame.label,
            task=step.id,
            cwd=directory,
            argv=(*command, *cast(list[str], raw_arguments)),
            stdin=None,
            stdout_path=directory / "vasp.stdout.log",
            required_inputs=tuple(directory / name for name in required_names),
            expected_outputs=expected,
            dependencies=dependencies,
            environment=_environment(step),
        )

    plugin_options = _plugin_options(step)
    planned = plan_plugin(
        step.kind,
        directory,
        capability=step.capability,
        configuration={"executable": command[-1]},
        **plugin_options,
    )
    stdout = planned.stdout_path or directory / f"{step.kind}.stdout.log"
    return JobSpec(
        id=job_id,
        frame_index=frame.index,
        frame_label=frame.label,
        task=step.id,
        cwd=directory,
        argv=(*command[:-1], *planned.argv),
        stdin=planned.stdin,
        stdout_path=stdout,
        required_inputs=planned.required_inputs,
        expected_outputs=planned.expected_outputs,
        dependencies=dependencies,
        environment={**dict(planned.environment), **_environment(step)},
    )


def _workspace_paths(manifest_path: Path, manifest: WorkflowManifest) -> tuple[Path, Path]:
    base = manifest_path.parent
    # Canonicalize the read-only source boundary before comparing it with the
    # lexical workspace.  Otherwise a manifest can name the source through a
    # symlink while placing the workspace inside that symlink's real target.
    source_root = _resolve_from(base, manifest.project.root).resolve()
    workspace = _resolve_from(base, manifest.project.workspace)
    if workspace == source_root or source_root in workspace.parents:
        raise ValueError(
            "project.workspace must be outside project.root so source calculations remain read-only"
        )
    if workspace == Path(workspace.anchor) or workspace == Path.home():
        raise ValueError(f"refusing unsafe workflow workspace: {workspace}")
    if symlink_component(workspace.parent) is not None:
        raise ValueError(
            f"workflow workspace parent must not traverse a symlink: {workspace.parent}"
        )
    return source_root, workspace


def _selected_sources(
    manifest: WorkflowManifest,
    source_root: Path,
) -> tuple[FrameSource, ...]:
    discovered = discover_path(
        source_root,
        mode=manifest.project.series_kind,
        structure_name=manifest.project.structure,
        fallback_structure_name=manifest.project.fallback,
        strict=True,
    )
    return resolve_selected_frames(discovered.frames, manifest.frames)


def _staged_frames(
    selected: tuple[FrameSource, ...],
    workspace: Path,
) -> tuple[StagedFrame, ...]:
    return tuple(
        StagedFrame(
            index=source.index,
            label=source.label,
            analysis_label=_safe_label(order, source.label),
            source_directory=source.directory,
            structure=source.structure,
            workspace_directory=workspace / "frames" / _safe_label(source.index, source.label),
            energy_ev=source.energy_ev,
        )
        for order, source in enumerate(selected)
    )


def _workspace_is_managed(path: Path) -> bool:
    marker = path / WORKSPACE_RECORD
    if marker.is_symlink() or not marker.is_file():
        return False
    try:
        payload = json.loads(
            read_limited_text(
                marker,
                limit=MAX_WORKSPACE_RECORD_BYTES,
                context="staged workflow record",
            )
        )
    except (OSError, RecursionError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == WORKSPACE_SCHEMA_VERSION
        and payload.get("workspace") == str(path)
        and isinstance(payload.get("manifest_sha256"), str)
    )


def _rebase_job(job: JobSpec, source_root: Path, destination_root: Path) -> JobSpec:
    """Move workspace-owned paths in a staged job from a temporary to its final root."""

    def rebase(path: Path) -> Path:
        try:
            relative = path.relative_to(source_root)
        except ValueError as exc:  # pragma: no cover - internal planner invariant
            raise RuntimeError(f"staged job path escapes temporary workspace: {path}") from exc
        return destination_root / relative

    return replace(
        job,
        cwd=rebase(job.cwd),
        stdout_path=rebase(job.stdout_path),
        required_inputs=tuple(rebase(path) for path in job.required_inputs),
        expected_outputs=tuple(rebase(path) for path in job.expected_outputs),
    )


def _publish_workspace(temporary: Path, workspace: Path, *, overwrite: bool) -> None:
    """Publish a complete workspace, rolling back a managed replacement on failure."""

    if not (workspace.exists() or workspace.is_symlink()):
        temporary.replace(workspace)
        return
    if not overwrite:
        raise FileExistsError(
            f"workflow workspace already exists: {workspace}; use --force to replace it"
        )
    if workspace.is_symlink() or not _workspace_is_managed(workspace):
        raise ValueError(
            f"refusing to replace unmarked or symlinked workflow workspace: {workspace}"
        )

    backup = Path(tempfile.mkdtemp(prefix=f".{workspace.name}.previous-", dir=workspace.parent))
    backup.rmdir()
    workspace.replace(backup)
    try:
        temporary.replace(workspace)
    except BaseException:
        backup.replace(workspace)
        raise
    shutil.rmtree(backup)


def _manifest_jobs(
    manifest: WorkflowManifest,
    manifest_path: Path,
    workspace: Path,
    frames: tuple[StagedFrame, ...],
) -> tuple[tuple[JobSpec, ...], tuple[str, ...]]:
    """Rebuild the executable plan instead of trusting serialized commands."""

    if not frames:
        raise ValueError("staged workflow contains no frames")
    if len({frame.index for frame in frames}) != len(frames):
        raise ValueError("staged workflow contains duplicate frame indices")

    jobs: list[JobSpec] = []
    descriptor_ids: list[str] = []
    for order, frame in enumerate(frames):
        if frame.analysis_label != _safe_label(order, frame.label):
            raise ValueError(
                f"staged frame {frame.index} has an invalid or out-of-order analysis label"
            )
        frame_directory = workspace / "frames" / _safe_label(frame.index, frame.label)
        if frame.workspace_directory != frame_directory:
            raise ValueError(
                f"staged frame {frame.index} workspace directory does not match its label"
            )
        for step in manifest.steps:
            job = _build_job(
                manifest,
                manifest_path.parent,
                frame,
                step,
                frame_directory / step.id,
            )
            jobs.append(job)
            if step.kind != "vasp":
                descriptor_ids.append(job.id)
    return validate_jobs(jobs), tuple(descriptor_ids)


def stage_workflow(
    manifest_path: str | Path,
    *,
    overwrite: bool = False,
) -> StagedWorkflow:
    """Stage selected frames and return a validated executable job DAG.

    ``overwrite=True`` may replace only a workspace carrying ElecTrace's own
    marker.  An unrelated nonempty directory is never removed.
    """

    source_manifest = Path(manifest_path).expanduser().resolve()
    manifest_sha256 = sha256_file(source_manifest)
    manifest = load_workflow_manifest(source_manifest)
    source_root, workspace = _workspace_paths(source_manifest, manifest)
    if not source_root.is_dir():
        raise FileNotFoundError(f"workflow project root is not a directory: {source_root}")
    if workspace.exists() or workspace.is_symlink():
        if not overwrite:
            raise FileExistsError(
                f"workflow workspace already exists: {workspace}; use --force to replace it"
            )
        if workspace.is_symlink() or not _workspace_is_managed(workspace):
            raise ValueError(
                f"refusing to replace unmarked or symlinked workflow workspace: {workspace}"
            )

    selected = _selected_sources(manifest, source_root)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{workspace.name}.stage-", dir=workspace.parent))
    staged_frames = _staged_frames(selected, workspace)
    jobs: list[JobSpec] = []
    descriptor_job_ids: list[str] = []
    try:
        for source, staged in zip(selected, staged_frames, strict=True):
            frame_directory = temporary / "frames" / _safe_label(source.index, source.label)
            prior_steps: dict[str, WorkflowStep] = {}
            directories: dict[str, Path] = {}
            for step in manifest.steps:
                directories[step.id] = frame_directory / step.id
                _prepare_step_directory(
                    step,
                    source,
                    directories[step.id],
                    source_manifest.parent,
                    prior_steps,
                    directories,
                )
                temporary_job = _build_job(
                    manifest,
                    source_manifest.parent,
                    staged,
                    step,
                    directories[step.id],
                )
                job = _rebase_job(temporary_job, temporary, workspace)
                jobs.append(job)
                if step.kind != "vasp":
                    descriptor_job_ids.append(job.id)
                prior_steps[step.id] = step
        ordered_jobs = validate_jobs(jobs)
        result = StagedWorkflow(
            manifest_path=source_manifest,
            manifest_sha256=manifest_sha256,
            source_root=source_root,
            workspace=workspace,
            frames=staged_frames,
            jobs=ordered_jobs,
            descriptor_job_ids=tuple(descriptor_job_ids),
        )
        atomic_write_json(temporary / WORKSPACE_RECORD, result.to_dict())
        if sha256_file(source_manifest) != manifest_sha256:
            raise ValueError("workflow manifest changed while staging; run workflow stage again")
        _publish_workspace(temporary, workspace, overwrite=overwrite)
    except BaseException:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result


def load_staged_workflow(manifest_path: str | Path) -> StagedWorkflow:
    """Load a staged plan and reject changes to its source manifest."""

    source_manifest = Path(manifest_path).expanduser().resolve()
    manifest = load_workflow_manifest(source_manifest)
    source_root, workspace = _workspace_paths(source_manifest, manifest)
    record = workspace / WORKSPACE_RECORD
    if record.is_symlink() or not record.is_file():
        raise FileNotFoundError(f"workflow is not staged: {record}")
    try:
        payload = json.loads(
            read_limited_text(
                record,
                limit=MAX_WORKSPACE_RECORD_BYTES,
                context="staged workflow record",
            )
        )
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid staged workflow record: {record}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != WORKSPACE_SCHEMA_VERSION:
        raise ValueError(f"unsupported staged workflow record: {record}")
    data = cast(dict[str, object], payload)
    recorded_hash = _record_string(data, "manifest_sha256", "staged workflow")
    current_hash = sha256_file(source_manifest)
    if recorded_hash != current_hash:
        raise ValueError("workflow manifest changed after staging; run workflow stage again")
    if Path(_record_string(data, "manifest_path", "staged workflow")) != source_manifest:
        raise ValueError("staged manifest path does not match the requested workflow manifest")
    if Path(_record_string(data, "source_root", "staged workflow")) != source_root:
        raise ValueError("staged source root does not match the workflow manifest")
    if Path(_record_string(data, "workspace", "staged workflow")) != workspace:
        raise ValueError("staged workspace does not match the workflow manifest")
    recorded_frames = tuple(
        StagedFrame.from_dict(item) for item in _record_list(data, "frames", "staged workflow")
    )
    frames = _staged_frames(_selected_sources(manifest, source_root), workspace)
    if recorded_frames != frames:
        raise ValueError(
            "staged frame provenance does not match current manifest-derived discovery; "
            "run workflow stage again"
        )
    jobs, descriptor_ids = _manifest_jobs(
        manifest,
        source_manifest,
        workspace,
        frames,
    )
    if _record_list(data, "jobs", "staged workflow") != [job.to_dict() for job in jobs]:
        raise ValueError(
            "staged job record does not match the manifest-derived plan or escapes its "
            "managed root; run workflow stage again"
        )
    if _record_string_list(data, "descriptor_job_ids", "staged workflow") != descriptor_ids:
        raise ValueError("staged descriptor job ids do not match the workflow manifest")
    return StagedWorkflow(
        manifest_path=source_manifest,
        manifest_sha256=current_hash,
        source_root=source_root,
        workspace=workspace,
        frames=frames,
        jobs=jobs,
        descriptor_job_ids=descriptor_ids,
    )


def descriptor_outputs(staged: StagedWorkflow) -> dict[str, tuple[Path, ...]]:
    """Return parser-supported outputs for each staged descriptor job."""

    selected = set(staged.descriptor_job_ids)
    return {
        job.id: tuple(path for path in job.expected_outputs if path.name in _DESCRIPTOR_FILENAMES)
        for job in staged.jobs
        if job.id in selected
    }


__all__ = [
    "WORKSPACE_RECORD",
    "WORKSPACE_SCHEMA_VERSION",
    "StagedFrame",
    "StagedWorkflow",
    "descriptor_outputs",
    "load_staged_workflow",
    "stage_workflow",
]
