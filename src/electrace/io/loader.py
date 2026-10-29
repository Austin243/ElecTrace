"""High-level ElecTrace path loading composed from discovery and parsers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from electrace.models import Frame, Sequence

from .descriptors import (
    SPIN_DESCRIPTOR,
    DescriptorDocument,
    DescriptorError,
    attach_descriptors,
    attach_net_charges,
    attach_spin_populations,
    load_descriptor,
)
from .discovery import DiscoveredPath, FrameSource, SeriesKind, discover_path
from .vasp import read_poscar

_CANONICAL_DESCRIPTOR_ALIASES = ("electrace.features.json", "features.json")
_NATIVE_DESCRIPTOR_FILENAMES = (
    "bader.json",
    "badelf.json",
    "critic2.features.json",
    "ICOHPLIST.lobster",
    "ICOBILIST.lobster",
    "DDEC6_even_tempered_net_atomic_charges.xyz",
    "ACF.dat",
)


_DESCRIPTOR_SOURCES = {"auto", "local", "external", "merge"}


def _descriptor_filename(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("descriptor_name must be a string")
    name = value.strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise DescriptorError(f"descriptor_name must be a basename, not a path: {value!r}")
    return name


def _descriptor_filenames(descriptor_name: str) -> tuple[str, ...]:
    name = _descriptor_filename(descriptor_name)
    candidates = [name]
    if name in _CANONICAL_DESCRIPTOR_ALIASES:
        candidates.extend(_CANONICAL_DESCRIPTOR_ALIASES)
    candidates.extend(_NATIVE_DESCRIPTOR_FILENAMES)
    return tuple(dict.fromkeys(candidates))


def _descriptor_source(
    value: str,
    descriptor_root: Path | None,
) -> str:
    selected = str(value).strip().lower()
    if selected not in _DESCRIPTOR_SOURCES:
        choices = ", ".join(sorted(_DESCRIPTOR_SOURCES))
        raise DescriptorError(f"unknown descriptor_source {value!r}; expected one of: {choices}")
    if selected == "auto":
        return "local" if descriptor_root is None else "external"
    if selected in {"external", "merge"} and descriptor_root is None:
        raise DescriptorError(f"descriptor_dir is required for descriptor_source={selected!r}")
    return selected


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return tuple(result)


def _descriptor_candidates(
    source: FrameSource,
    descriptor_root: Path | None,
    *,
    frame_count: int,
    descriptor_name: str,
    descriptor_source: str,
) -> tuple[Path, ...]:
    filenames = _descriptor_filenames(descriptor_name)
    selected = _descriptor_source(descriptor_source, descriptor_root)
    local = (
        [source.directory / name for name in filenames] if selected in {"local", "merge"} else []
    )
    if selected == "local":
        return _unique_paths(local)
    assert descriptor_root is not None
    if descriptor_root.is_file():
        if frame_count != 1:
            raise DescriptorError(
                "a single descriptor file can only be used with a one-frame static series"
            )
        external = [descriptor_root.resolve()]
        return _unique_paths((*local, *external))
    if not descriptor_root.is_dir():
        raise DescriptorError(f"descriptor_dir is not a directory: {descriptor_root}")

    candidates: list[Path] = []
    candidates.append(descriptor_root / f"{source.label}.json")
    directory = descriptor_root / source.label
    candidates.extend(directory / name for name in filenames)
    return _unique_paths((*local, *candidates))


def _spin_candidates(
    source: FrameSource, descriptor_root: Path | None, descriptor_source: str
) -> tuple[Path, ...]:
    """Return spin populations beside a frame or in its mirrored descriptor directory."""

    candidates: list[Path] = []
    if descriptor_source in {"local", "merge"}:
        candidates.append(source.directory / SPIN_DESCRIPTOR)
    if descriptor_source in {"external", "merge"} and descriptor_root is not None:
        candidates.append(descriptor_root / source.label / SPIN_DESCRIPTOR)
    return _unique_paths(candidates)


def _load_discovered(
    discovered: DiscoveredPath,
    *,
    descriptor_dir: str | Path | None,
    descriptor_name: str,
    descriptor_source: str,
) -> list[Frame]:
    descriptor_root = (
        Path(descriptor_dir).expanduser().resolve() if descriptor_dir is not None else None
    )
    selected_source = _descriptor_source(descriptor_source, descriptor_root)
    frames: list[Frame] = []
    for source in discovered.frames:
        metadata = {
            "path_root": str(discovered.root),
            "frame_directory": str(source.directory),
            "series_kind": discovered.kind.value,
            "descriptor_source_policy": selected_source,
            "descriptor_sources": [],
            **source.metadata,
        }
        if source.pressure_gpa is not None:
            metadata["pressure_gpa"] = source.pressure_gpa
        frame = read_poscar(
            source.structure,
            index=source.index,
            label=source.label,
            energy=source.energy_ev,
            metadata=metadata,
        )
        descriptor_paths = _descriptor_candidates(
            source,
            descriptor_root,
            frame_count=len(discovered.frames),
            descriptor_name=descriptor_name,
            descriptor_source=selected_source,
        )
        documents: list[DescriptorDocument] = []
        for descriptor_path in descriptor_paths:
            documents.append(load_descriptor(descriptor_path, frame=frame))
        if documents:
            frame = attach_descriptors(frame, documents)
            updated_metadata = dict(frame.metadata)
            updated_metadata["descriptor_sources"] = [str(path) for path in descriptor_paths]
            frame = replace(frame, metadata=updated_metadata)
        spin_paths = _spin_candidates(source, descriptor_root, selected_source)
        if len(spin_paths) > 1:
            raise DescriptorError(
                f"frame {source.label} has more than one spin population file: "
                + ", ".join(map(str, spin_paths))
            )
        if spin_paths:
            frame = attach_spin_populations(frame, spin_paths[0])
        frames.append(attach_net_charges(frame, source.directory))
    return frames


def load_path(
    path: str | Path,
    descriptor_dir: str | Path | None = None,
    prefer_contcar: bool = True,
    *,
    mode: str | SeriesKind = "auto",
    strict: bool = True,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
    descriptor_name: str = "features.json",
    descriptor_source: str = "auto",
) -> list[Frame]:
    """Discover and load a VASP path into ordered core ``Frame`` objects.

    ``path`` may contain numeric VASP NEB images or named static/pressure
    calculation directories. ``structure_name`` and
    ``fallback_structure_name`` must be basenames; ``prefer_contcar`` chooses
    whether the former or latter is tried first. An external descriptor
    directory may be flat (``00.json``) or mirror frame directories
    (``00/features.json``).

    ``descriptor_source`` is ``local``, ``external``, ``merge``, or ``auto``.
    ``auto`` searches local frame directories when ``descriptor_dir`` is absent
    and external descriptors only when it is present. ``merge`` combines both
    sources without precedence: duplicate feature IDs are rejected. Plugin
    enablement does not affect ingestion of already-produced descriptors, and
    external science tools are never run while loading.
    """

    discovered = discover_path(
        path,
        mode=mode,
        prefer_contcar=prefer_contcar,
        structure_name=structure_name,
        fallback_structure_name=fallback_structure_name,
        strict=strict,
    )
    return _load_discovered(
        discovered,
        descriptor_dir=descriptor_dir,
        descriptor_name=descriptor_name,
        descriptor_source=descriptor_source,
    )


def load_sequence(
    path: str | Path,
    descriptor_dir: str | Path | None = None,
    prefer_contcar: bool = True,
    *,
    mode: str | SeriesKind = "auto",
    strict: bool = True,
    label: str | None = None,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
    descriptor_name: str = "features.json",
    descriptor_source: str = "auto",
) -> Sequence:
    """Load a path and wrap it in ElecTrace's core ``Sequence`` model."""

    root = Path(path).expanduser().resolve()
    frames = load_path(
        root,
        descriptor_dir=descriptor_dir,
        prefer_contcar=prefer_contcar,
        mode=mode,
        strict=strict,
        structure_name=structure_name,
        fallback_structure_name=fallback_structure_name,
        descriptor_name=descriptor_name,
        descriptor_source=descriptor_source,
    )
    series_kind = frames[0].metadata.get("series_kind", "unknown") if frames else "unknown"
    return Sequence(
        frames=tuple(frames),
        label=label or root.name,
        metadata={"source_root": str(root), "series_kind": series_kind},
    )
