"""Stage workflow inputs in an isolated, marker-owned workspace."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from electrace.io.discovery import FrameSource, discover_path
from electrace.io.vasp import parse_poscar
from electrace.plugins import plan_plugin
from electrace.plugins.critic2_input import (
    ALL_ELECTRON_FILES,
    PERIODIC_INPUT_NAME,
    periodic_critic2_input,
    seed_settings,
)
from electrace.provenance import sha256_file

from ._files import absolute_lexical, atomic_write_json, atomic_write_text, symlink_component
from .execution import JobSpec, validate_jobs
from .manifest import load_workflow_manifest
from .models import ExecutableSpec, JSONValue, WorkflowManifest, WorkflowStep
from .selection import resolve_selected_frames

WORKSPACE_SCHEMA_VERSION = "electrace.workspace.v1"
WORKSPACE_RECORD = ".electrace-workspace.json"
_INCAR_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SAFE_LABEL = re.compile(r"[^A-Za-z0-9_.-]+")
_LOBSTER_BOND_REQUEST = re.compile(r"(?im)^\s*(?:cohpgenerator|cohpbetween)\b")
_LOBSTER_SKIP_COBI = re.compile(r"(?im)^\s*skipcobi\b")
_POTCAR_TITEL = re.compile(r"(?m)^\s*TITEL\s*=\s*\S+\s+(\S+)")
_ELEMENT_SYMBOL = re.compile(r"[A-Z][a-z]?")
_DESCRIPTOR_FILENAMES = frozenset(
    """ACF.dat DDEC6_even_tempered_net_atomic_charges.xyz ICOBILIST.lobster
    ICOHPLIST.lobster badelf.json bader.json critic2.features.json
    electrace.features.json features.json""".split()  # noqa: SIM905
)
# Steps whose outputs are grids for later steps rather than descriptors to collect.
NON_DESCRIPTOR_KINDS = frozenset({"vasp", "volume"})
# `electrace volume` operations as (inputs, outputs, arguments). The outputs go to the
# step directory even though the inputs link into a dependency's directory, and a rerun
# replaces the step's own outputs.
_VOLUME_OPERATIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "aeccar_sum": (
        ALL_ELECTRON_FILES,
        ("CHGCAR_sum",),
        ("volume", "AECCAR0", "--add", "AECCAR2", "--output-dir", ".", "--force", "--json"),
    ),
    "split_spin": (
        ("CHGCAR",),
        ("CHGCAR_tot", "CHGCAR_mag"),
        ("volume", "CHGCAR", "--split-spin", "--output-dir", ".", "--force", "--json"),
    ),
}


def _resolve_from(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return absolute_lexical(path if path.is_absolute() else base / path)


def _resolve_command(base: Path, value: str) -> str:
    return str(_resolve_from(base, value)) if Path(value).is_absolute() or "/" in value else value


def _basename(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    if (
        not value
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{path} must be a safe basename: {value!r}")
    return value


def _string_list(value: JSONValue | None, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"{path} must be a list")
    return tuple(_basename(item, path) for item in value)


def _string_mapping(value: JSONValue | None, path: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise TypeError(f"{path} must contain string keys and values")
    return cast(dict[str, str], value)


def _safe_label(index: int, label: str) -> str:
    suffix = _SAFE_LABEL.sub("-", label).strip(".-") or "frame"
    return f"{index:06d}-{suffix[:80]}"


@dataclass(frozen=True, slots=True)
class StagedFrame:
    index: int
    label: str
    analysis_label: str
    source_directory: Path
    structure: Path
    workspace_directory: Path
    energy_ev: float | None = None

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


@dataclass(frozen=True, slots=True)
class StagedWorkflow:
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


def _copy_template(files: tuple[Path, ...], destination: Path) -> None:
    for item in files:
        shutil.copy2(item, destination / _basename(item.name, "template filename"))


def _replace_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise FileNotFoundError(f"workflow source is not a regular file: {source}")
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    shutil.copy2(source, destination)


def _apply_incar_overrides(path: Path, raw: JSONValue | None) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict) or not path.is_file():
        raise ValueError("vasp incar_overrides requires a mapping and an INCAR template")
    overrides: dict[str, str] = {}
    for key, value in raw.items():
        if _INCAR_KEY.fullmatch(key) is None or not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"invalid INCAR override: {key!r}")
        overrides[key.upper()] = (
            ".TRUE." if value is True else ".FALSE." if value is False else str(value)
        )
    result: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        content = line.split("!", 1)[0].split("#", 1)[0]
        key = content.split("=", 1)[0].strip().upper() if "=" in content else ""
        result.append(f"{key} = {overrides.pop(key)}" if key in overrides else line)
    result.extend(f"{key} = {value}" for key, value in overrides.items())
    atomic_write_text(path, "\n".join(result).rstrip() + "\n")


def _plugin_options(step: WorkflowStep) -> dict[str, JSONValue]:
    reserved = {"environment", "input_links", "plugin_options"}
    result = {key: value for key, value in step.options.items() if key not in reserved}
    nested = step.options.get("plugin_options")
    if nested is not None:
        if not isinstance(nested, dict) or set(result) & set(nested):
            raise ValueError(f"step {step.id} has invalid or duplicate plugin_options")
        result.update(nested)
    return result


def _volume_operation(
    step: WorkflowStep,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    operation = _VOLUME_OPERATIONS.get(step.capability or "")
    if operation is None:
        raise ValueError(
            f"volume step {step.id!r} needs capability " + " or ".join(sorted(_VOLUME_OPERATIONS))
        )
    if _plugin_options(step):
        raise ValueError(
            f"volume step {step.id!r} takes no options besides environment and input_links"
        )
    return operation


def _is_periodic_critic2(step: WorkflowStep) -> bool:
    return step.kind == "critic2" and _plugin_options(step).get("seeds") == "periodic"


def _is_spin_bader(step: WorkflowStep) -> bool:
    return step.kind == "bader" and step.capability == "spin_populations"


def _step_outputs(step: WorkflowStep) -> tuple[str, ...]:
    if step.kind == "volume":
        return _volume_operation(step)[1]
    if step.kind == "vasp":
        outputs = _string_list(
            step.options.get("outputs", step.options.get("expected_outputs")),
            f"step {step.id}.options.expected_outputs",
        )
        if not outputs:
            raise ValueError(f"vasp step {step.id!r} must declare expected outputs")
        return outputs
    if step.kind == "bader":
        return ("ACF.dat",)
    if step.kind == "baderkit":
        return (
            ("badelf.json",)
            if step.capability in {"elf_basins", "non_nuclear_attractors"}
            else ("bader.json",)
        )
    if step.kind == "critic2":
        report = _basename(
            _plugin_options(step).get("report_file", "critic2.features.json"), "report_file"
        )
        if report != "critic2.features.json":
            raise ValueError("critic2 report_file must be critic2.features.json")
        return ("critic2.cro", report)
    if step.kind == "lobster":
        return ("ICOBILIST.lobster",) if step.capability == "cobi" else ("ICOHPLIST.lobster",)
    raise ValueError(f"unsupported workflow step kind: {step.kind}")


def _vasp_required_inputs(step: WorkflowStep) -> tuple[str, ...]:
    return _string_list(step.options.get("required_inputs"), "required_inputs") or (
        "INCAR",
        "KPOINTS",
        "POSCAR",
        "POTCAR",
    )


def _template_files(step: WorkflowStep, manifest_base: Path) -> tuple[Path, ...]:
    if step.template_dir is None:
        files: tuple[Path, ...] = ()
    else:
        source = _resolve_from(manifest_base, step.template_dir)
        if source.is_symlink() or not source.is_dir():
            raise FileNotFoundError(f"workflow template directory is invalid: {source}")
        files = tuple(source.iterdir())
        if any(item.is_symlink() or not item.is_file() for item in files):
            raise ValueError(f"workflow templates must contain regular files: {source}")
    names = {_basename(item.name, "template filename") for item in files}
    if step.kind != "vasp" and (collision := names.intersection(_step_outputs(step))):
        raise ValueError(
            f"step {step.id!r} template files collide with expected outputs: "
            + ", ".join(sorted(collision))
        )
    if _is_periodic_critic2(step) and PERIODIC_INPUT_NAME in names:
        raise ValueError(
            f"step {step.id!r} writes {PERIODIC_INPUT_NAME} for periodic seeds; remove it "
            "from the template directory"
        )
    if step.kind == "vasp" and (
        missing := set(_vasp_required_inputs(step)).difference(names, {"POSCAR"})
    ):
        raise FileNotFoundError(
            f"vasp step {step.id!r} required inputs are missing from its template: "
            + ", ".join(sorted(missing))
        )
    return files


def _element_symbol(label: str) -> str:
    match = _ELEMENT_SYMBOL.match(label)
    return match.group(0) if match else label


def _check_vasp_species(
    step: WorkflowStep,
    template_files: tuple[Path, ...],
    sources: tuple[FrameSource, ...],
) -> tuple[str, ...]:
    """Reject frames whose POSCAR species order differs from the template POTCAR.

    VASP pairs POSCAR species with POTCAR entries by position, so another order runs
    with the wrong pseudopotentials.  Labels are compared by element symbol, so
    ``Fe_pv``, ``H.75``, or a VASP 6 ``/hash`` suffix still matches ``Fe`` or ``H``.
    Returns warnings for what cannot be checked.
    """

    potcar = next((item for item in template_files if item.name == "POTCAR"), None)
    if step.kind != "vasp" or potcar is None:
        return ()
    text = potcar.read_text(encoding="utf-8", errors="replace")
    expected = tuple(_element_symbol(label) for label in _POTCAR_TITEL.findall(text))
    if not expected:
        return (
            f"vasp step {step.id!r}: the template POTCAR has no TITEL lines, so POSCAR "
            "species order was not checked",
        )
    unnamed: list[str] = []
    for source in sources:
        data = parse_poscar(source.structure)
        species = tuple(_element_symbol(label) for label in data.species)
        if data.symbols_inferred and len(species) == len(expected):
            # A VASP 4 file lists counts without names; only the count is checkable.
            unnamed.append(source.label)
        elif data.symbols_inferred:
            raise ValueError(
                f"vasp step {step.id!r}: frame {source.label!r} {source.structure.name} "
                f"lists {len(species)} species but the template POTCAR has "
                f"{len(expected)} ({' '.join(expected)})"
            )
        elif species != expected:
            raise ValueError(
                f"vasp step {step.id!r}: frame {source.label!r} {source.structure.name} "
                f"species order {' '.join(species)} does not match the template POTCAR "
                f"order {' '.join(expected)}; VASP pairs them by position"
            )
    if not unnamed:
        return ()
    return (
        f"vasp step {step.id!r}: species order was not checked for frame(s) "
        f"{', '.join(unnamed)} because their structure files have no species names",
    )


def _plugin_required_names(step: WorkflowStep) -> tuple[str, ...]:
    options = _plugin_options(step)
    if step.kind == "volume":
        return _volume_operation(step)[0]
    if step.kind in {"bader", "baderkit"}:
        default_charge = "CHGCAR_mag" if _is_spin_bader(step) else "CHGCAR"
        names = [str(options.get("charge_file", default_charge))]
        if step.kind == "baderkit" and step.capability in {"elf_basins", "non_nuclear_attractors"}:
            names.append(str(options.get("elf_file", "ELFCAR")))
        if options.get("reference_file") is not None:
            names.append(str(options["reference_file"]))
        return tuple(names)
    if _is_periodic_critic2(step):
        return (str(options.get("structure_file", "CONTCAR")), *ALL_ELECTRON_FILES)
    if step.kind == "critic2":
        default = (
            "ELFCAR" if step.capability in {"elf_basins", "non_nuclear_attractors"} else "CHGCAR"
        )
        names = [
            str(options.get("structure_file", "CONTCAR")),
            str(options.get("field_file", default)),
        ]
        if options.get("input_file") is not None:
            names.append(str(options["input_file"]))
        return tuple(names)
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


def _dependency_source(
    name: str, dependencies: tuple[WorkflowStep, ...], directories: dict[str, Path]
) -> Path | None:
    for dependency in reversed(dependencies):
        candidate = directories[dependency.id] / name
        if candidate.exists() or name in _step_outputs(dependency):
            return candidate
    return None


def _relative_link(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"workflow input already exists: {destination}")
    destination.symlink_to(os.path.relpath(source, destination.parent))


def _explicit_input_links(
    step: WorkflowStep, source: FrameSource, destination: Path, directories: dict[str, Path]
) -> None:
    raw = step.options.get("input_links")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise TypeError(f"step {step.id}.options.input_links must be a mapping")
    for raw_target, specification in raw.items():
        target = _basename(raw_target, "input link target")
        if specification == "source:structure":
            _replace_file(source.structure, destination / target)
            continue
        if not isinstance(specification, dict):
            raise TypeError(f"invalid input link for {target}")
        dependency = specification.get("step")
        if not isinstance(dependency, str) or dependency not in step.depends_on:
            raise ValueError(f"input link {target} must name a declared dependency")
        name = _basename(specification.get("file", target), "input link file")
        _relative_link(directories[dependency] / name, destination / target)


def _auto_plugin_inputs(
    step: WorkflowStep,
    source: FrameSource,
    destination: Path,
    dependencies: tuple[WorkflowStep, ...],
    directories: dict[str, Path],
) -> None:
    structure_name = str(_plugin_options(step).get("structure_file", "CONTCAR"))
    for raw_name in _plugin_required_names(step):
        name = _basename(raw_name, f"step {step.id} plugin input")
        target = destination / name
        if target.exists() or target.is_symlink():
            continue
        if step.kind in {"critic2", "lobster"} and name == structure_name:
            _replace_file(source.structure, target)
        elif (candidate := _dependency_source(name, dependencies, directories)) is not None:
            _relative_link(candidate, target)


def _check_step_inputs(
    step: WorkflowStep, destination: Path, prior_steps: dict[str, WorkflowStep]
) -> None:
    """Stop staging when an input of a volume, spin, or periodic critic2 step has no source.

    Such a step finds its inputs only in its template, its input links, the frame
    structure, or a dependency's declared outputs. A file that an earlier volume step
    writes must also come through ``depends_on``, whichever step reads it. Otherwise the
    job would fail at run time, after the VASP step it waits for.
    """

    strict = step.kind == "volume" or _is_spin_bader(step) or _is_periodic_critic2(step)
    for name in _plugin_required_names(step):
        target = destination / name
        if target.exists() or target.is_symlink():
            continue
        writers = [item for item in prior_steps.values() if name in _step_outputs(item)]
        if writers and (strict or any(item.kind == "volume" for item in writers)):
            raise ValueError(
                f"step {step.id!r} needs {name}, which step {writers[-1].id!r} writes; "
                "add that step to depends_on"
            )
        if strict:
            raise ValueError(
                f"step {step.id!r} needs {name}, but no dependency declares it as an output "
                "and no template or input link provides it"
            )


def _check_spin_steps(manifest: WorkflowManifest) -> None:
    """Require Bader charges, in the same basins, for every spin-population step.

    Spin populations attach to the atoms of a ``bader`` charge step. A spin step
    integrates the magnetization in the basins of its ``reference_file``; the charge
    step's basins come from its own ``reference_file`` or, without one, from the total
    density of its charge file, which ``split_spin`` also writes as ``CHGCAR_tot``.
    """

    spins = [step for step in manifest.steps if _is_spin_bader(step)]
    if not spins:
        return
    charges = [
        step
        for step in manifest.steps
        if step.kind == "bader" and step.capability in {None, "bader_charges"}
    ]
    if not charges:
        raise ValueError(
            f"spin step {spins[0].id!r} needs a bader step with capability bader_charges "
            "whose atoms the spin populations attach to"
        )
    for spin in spins:
        reference = _plugin_options(spin).get("reference_file")
        if reference is None:
            continue  # the Bader plan reports the missing reference
        for charge in charges:
            options = _plugin_options(charge)
            basins = options.get("reference_file") or options.get("charge_file", "CHGCAR")
            if reference != basins and (
                options.get("reference_file") is not None or reference != "CHGCAR_tot"
            ):
                raise ValueError(
                    f"spin step {spin.id!r} integrates the magnetization in the basins of "
                    f"{reference}, but charge step {charge.id!r} uses {basins}; give both "
                    "the same reference_file so that charge and spin share their basins"
                )


def _periodic_input(step: WorkflowStep, structure: Path) -> str:
    options = _plugin_options(step)
    distance, points, triplets = seed_settings(
        options.get("seed_distance"), options.get("seed_points"), options.get("seed_triplets")
    )
    text, _ = periodic_critic2_input(
        structure,
        distance=distance,
        points=points,
        triplets=triplets,
        report_file=str(options.get("report_file", "critic2.features.json")),
        graph=bool(options.get("graph", True)),
    )
    return text


def _prepare_step_directory(
    step: WorkflowStep,
    source: FrameSource,
    destination: Path,
    template_files: tuple[Path, ...],
    prior_steps: dict[str, WorkflowStep],
    directories: dict[str, Path],
) -> None:
    destination.mkdir(parents=True)
    _copy_template(template_files, destination)
    if step.kind == "vasp":
        _replace_file(source.structure, destination / "POSCAR")
        _apply_incar_overrides(destination / "INCAR", step.options.get("incar_overrides"))
        return
    dependencies = tuple(prior_steps[item] for item in step.depends_on)
    _explicit_input_links(step, source, destination, directories)
    _auto_plugin_inputs(step, source, destination, dependencies, directories)
    _check_step_inputs(step, destination, prior_steps)
    if _is_periodic_critic2(step):
        structure = destination / str(_plugin_options(step).get("structure_file", "CONTCAR"))
        atomic_write_text(destination / PERIODIC_INPUT_NAME, _periodic_input(step, structure))
    if step.kind == "lobster":
        options = _plugin_options(step)
        name = _basename(options.get("input_file", "lobsterin"), "lobster input_file")
        path = destination / name
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        if path.is_symlink() or _LOBSTER_BOND_REQUEST.search(text) is None:
            raise ValueError(f"LOBSTER requires a reviewed bond-requesting {name}")
        if step.capability == "cobi" and _LOBSTER_SKIP_COBI.search(text):
            raise ValueError("LOBSTER COBI input cannot use skipcobi")


def _environment(step: WorkflowStep) -> dict[str, str]:
    return _string_mapping(step.options.get("environment"), f"step {step.id}.environment")


def _build_job(
    manifest: WorkflowManifest,
    manifest_base: Path,
    frame: StagedFrame,
    step: WorkflowStep,
    directory: Path,
) -> JobSpec:
    specification: ExecutableSpec = manifest.executables[step.executable]
    command = (
        *specification.argv_prefix,
        _resolve_command(manifest_base, specification.path),
    )
    job_id = f"f{frame.index:06d}.{step.id}"
    dependencies = tuple(f"f{frame.index:06d}.{item}" for item in step.depends_on)
    if step.kind == "volume":
        inputs, outputs, operation = _volume_operation(step)
        return JobSpec(
            id=job_id,
            frame_index=frame.index,
            frame_label=frame.label,
            task=step.id,
            cwd=directory,
            argv=(*command, *operation),
            stdin=None,
            stdout_path=directory / "volume.stdout.log",
            required_inputs=tuple(directory / name for name in inputs),
            expected_outputs=tuple(directory / name for name in outputs),
            dependencies=dependencies,
            environment=_environment(step),
        )
    if step.kind == "vasp":
        arguments = step.options.get("arguments", [])
        if not isinstance(arguments, list) or any(
            not isinstance(item, str) or not item for item in arguments
        ):
            raise TypeError(f"step {step.id}.options.arguments must be strings")
        required = _vasp_required_inputs(step)
        return JobSpec(
            id=job_id,
            frame_index=frame.index,
            frame_label=frame.label,
            task=step.id,
            cwd=directory,
            argv=(*command, *cast(list[str], arguments)),
            stdin=None,
            stdout_path=directory / "vasp.stdout.log",
            required_inputs=tuple(directory / name for name in required),
            expected_outputs=tuple(directory / name for name in _step_outputs(step)),
            dependencies=dependencies,
            environment=_environment(step),
        )
    planned = plan_plugin(
        step.kind,
        directory,
        capability=step.capability,
        configuration={"executable": command[-1]},
        **_plugin_options(step),
    )
    return JobSpec(
        id=job_id,
        frame_index=frame.index,
        frame_label=frame.label,
        task=step.id,
        cwd=directory,
        argv=(*command[:-1], *planned.argv),
        stdin=planned.stdin,
        stdout_path=planned.stdout_path or directory / f"{step.kind}.stdout.log",
        required_inputs=planned.required_inputs,
        expected_outputs=planned.expected_outputs,
        dependencies=dependencies,
        environment={**dict(planned.environment), **_environment(step)},
    )


def _workspace_paths(manifest_path: Path, manifest: WorkflowManifest) -> tuple[Path, Path]:
    source = _resolve_from(manifest_path.parent, manifest.project.root).resolve()
    workspace = _resolve_from(manifest_path.parent, manifest.project.workspace)
    if workspace == source or source in workspace.parents or workspace in source.parents:
        raise ValueError("project.workspace and project.root must not overlap")
    if workspace.is_symlink() or symlink_component(workspace.parent) is not None:
        raise ValueError(f"workflow workspace must not traverse a symlink: {workspace}")
    return source, workspace


def _selected_sources(manifest: WorkflowManifest, source_root: Path) -> tuple[FrameSource, ...]:
    discovered = discover_path(
        source_root,
        mode=manifest.project.series_kind,
        structure_name=manifest.project.structure,
        fallback_structure_name=manifest.project.fallback,
        strict=True,
    )
    return resolve_selected_frames(discovered.frames, manifest.frames)


def _staged_frames(selected: tuple[FrameSource, ...], workspace: Path) -> tuple[StagedFrame, ...]:
    return tuple(
        StagedFrame(
            source.index,
            source.label,
            _safe_label(order, source.label),
            source.directory,
            source.structure,
            workspace / "frames" / _safe_label(source.index, source.label),
            source.energy_ev,
        )
        for order, source in enumerate(selected)
    )


def _workspace_is_managed(path: Path) -> bool:
    marker = path / WORKSPACE_RECORD
    if path.is_symlink() or marker.is_symlink() or not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == WORKSPACE_SCHEMA_VERSION
        and payload.get("workspace") == str(path)
        and isinstance(payload.get("manifest_sha256"), str)
    )


def _rebase_job(job: JobSpec, source_root: Path, destination_root: Path) -> JobSpec:
    def rebase(path: Path) -> Path:
        return destination_root / path.relative_to(source_root)

    return replace(
        job,
        cwd=rebase(job.cwd),
        stdout_path=rebase(job.stdout_path),
        required_inputs=tuple(rebase(path) for path in job.required_inputs),
        expected_outputs=tuple(rebase(path) for path in job.expected_outputs),
    )


def _publish_workspace(temporary: Path, workspace: Path, overwrite: bool) -> None:
    if not workspace.exists():
        temporary.replace(workspace)
        return
    if not overwrite or not _workspace_is_managed(workspace):
        raise ValueError(f"refusing to replace unmarked workflow workspace: {workspace}")
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
    jobs = tuple(
        _build_job(
            manifest,
            manifest_path.parent,
            frame,
            step,
            frame.workspace_directory / step.id,
        )
        for frame in frames
        for step in manifest.steps
    )
    descriptors = tuple(
        job.id
        for job in jobs
        if next(step for step in manifest.steps if step.id == job.task).kind
        not in NON_DESCRIPTOR_KINDS
    )
    return validate_jobs(jobs), descriptors


def stage_workflow(manifest_path: str | Path, *, overwrite: bool = False) -> StagedWorkflow:
    source_manifest = Path(manifest_path).expanduser().resolve()
    manifest = load_workflow_manifest(source_manifest)
    source_root, workspace = _workspace_paths(source_manifest, manifest)
    manifest_hash = sha256_file(source_manifest)
    if not source_root.is_dir():
        raise FileNotFoundError(f"workflow project root is not a directory: {source_root}")
    if workspace.exists() and (not overwrite or not _workspace_is_managed(workspace)):
        raise ValueError(f"refusing existing or unmarked workflow workspace: {workspace}")
    selected = _selected_sources(manifest, source_root)
    frames = _staged_frames(selected, workspace)
    templates = {step.id: _template_files(step, source_manifest.parent) for step in manifest.steps}
    for step in manifest.steps:
        _check_vasp_species(step, templates[step.id], selected)
    _check_spin_steps(manifest)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{workspace.name}.stage-", dir=workspace.parent))
    jobs: list[JobSpec] = []
    descriptor_ids: list[str] = []
    try:
        for source, frame in zip(selected, frames, strict=True):
            directories: dict[str, Path] = {}
            prior: dict[str, WorkflowStep] = {}
            for step in manifest.steps:
                directory = temporary / "frames" / _safe_label(source.index, source.label) / step.id
                directories[step.id] = directory
                _prepare_step_directory(
                    step, source, directory, templates[step.id], prior, directories
                )
                job = _rebase_job(
                    _build_job(manifest, source_manifest.parent, frame, step, directory),
                    temporary,
                    workspace,
                )
                jobs.append(job)
                if step.kind not in NON_DESCRIPTOR_KINDS:
                    descriptor_ids.append(job.id)
                prior[step.id] = step
        result = StagedWorkflow(
            source_manifest,
            manifest_hash,
            source_root,
            workspace,
            frames,
            validate_jobs(jobs),
            tuple(descriptor_ids),
        )
        atomic_write_json(
            temporary / WORKSPACE_RECORD,
            {
                "schema_version": WORKSPACE_SCHEMA_VERSION,
                "manifest_path": str(source_manifest),
                "manifest_sha256": manifest_hash,
                "source_root": str(source_root),
                "workspace": str(workspace),
            },
        )
        if sha256_file(source_manifest) != manifest_hash:
            raise ValueError("workflow manifest changed while staging")
        _publish_workspace(temporary, workspace, overwrite)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result


def load_staged_workflow(manifest_path: str | Path) -> StagedWorkflow:
    source_manifest = Path(manifest_path).expanduser().resolve()
    manifest = load_workflow_manifest(source_manifest)
    source_root, workspace = _workspace_paths(source_manifest, manifest)
    marker = workspace / WORKSPACE_RECORD
    if not _workspace_is_managed(workspace):
        raise FileNotFoundError(f"workflow is not staged: {marker}")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    manifest_hash = sha256_file(source_manifest)
    expected = (str(source_manifest), manifest_hash, str(source_root), str(workspace))
    recorded = tuple(
        payload.get(key) for key in ("manifest_path", "manifest_sha256", "source_root", "workspace")
    )
    if recorded != expected:
        raise ValueError("staged workflow no longer matches its manifest")
    frames = _staged_frames(_selected_sources(manifest, source_root), workspace)
    jobs, descriptor_ids = _manifest_jobs(manifest, source_manifest, workspace, frames)
    return StagedWorkflow(
        source_manifest, manifest_hash, source_root, workspace, frames, jobs, descriptor_ids
    )


def descriptor_outputs(staged: StagedWorkflow) -> dict[str, tuple[Path, ...]]:
    selected = set(staged.descriptor_job_ids)
    return {
        job.id: tuple(path for path in job.expected_outputs if path.name in _DESCRIPTOR_FILENAMES)
        for job in staged.jobs
        if job.id in selected
    }
