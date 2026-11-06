"""High-level ElecTrace path loading composed from discovery and parsers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

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
from .discovery import FrameSource, discover_path
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


def _descriptor_filenames(name: str) -> tuple[str, ...]:
    aliases = _CANONICAL_DESCRIPTOR_ALIASES if name in _CANONICAL_DESCRIPTOR_ALIASES else ()
    return tuple(dict.fromkeys((name, *aliases, *_NATIVE_DESCRIPTOR_FILENAMES)))


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(path.resolve() for path in paths if path.is_file()))


def _descriptor_candidates(
    source: FrameSource,
    descriptor_root: Path | None,
    *,
    frame_count: int,
    descriptor_name: str,
    descriptor_source: str,
) -> tuple[Path, ...]:
    filenames = _descriptor_filenames(descriptor_name)
    local: list[Path] = []
    if descriptor_source in {"local", "merge"}:
        local = [source.directory / name for name in filenames]
    if descriptor_source == "local" or descriptor_root is None:
        return _unique_paths(local)
    if descriptor_root.is_file():
        if frame_count != 1:
            raise DescriptorError(
                "a single descriptor file can only be used with a one-frame static series"
            )
        return _unique_paths((*local, descriptor_root))
    if not descriptor_root.is_dir():
        raise DescriptorError(f"descriptor_dir is not a directory: {descriptor_root}")

    flat = descriptor_root / f"{source.label}.json"
    mirrored = [descriptor_root / source.label / name for name in filenames]
    return _unique_paths((*local, flat, *mirrored))


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


def _frame_evidence(
    paths: Iterable[Path], documents: Iterable[DescriptorDocument]
) -> dict[tuple[str, str | None], tuple[str, ...]]:
    """Return the features of one frame by the output that holds them.

    An output is a producer and, for a native file, its name, so LOBSTER's two lists
    count separately. Other names can vary from frame to frame, as ``00.json`` does.
    """

    evidence: dict[tuple[str, str | None], tuple[str, ...]] = {}
    for path, document in zip(paths, documents, strict=True):
        name = path.name if path.name in _NATIVE_DESCRIPTOR_FILENAMES else None
        held = evidence.get((document.producer, name), ())
        evidence[(document.producer, name)] = (*held, *(item.id for item in document.features))
    return evidence


def load_sequence(
    path: str | Path,
    *,
    descriptor_dir: Path | None = None,
    descriptor_name: str = "features.json",
    descriptor_source: str = "auto",
    mode: str = "auto",
    strict: bool = True,
    prefer_contcar: bool = True,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
) -> Sequence:
    """Discover and load a VASP path into an ordered core ``Sequence``.

    ``path`` may contain numeric VASP NEB images or named static/pressure
    calculation directories. ``structure_name`` and ``fallback_structure_name`` must be
    basenames; ``prefer_contcar`` chooses whether the former or latter is tried first.
    An external descriptor directory may be flat (``00.json``) or mirror frame directories
    (``00/features.json``).

    ``descriptor_source`` is ``local``, ``external``, ``merge``, or ``auto``.
    ``auto`` searches local frame directories when ``descriptor_dir`` is absent
    and external descriptors only when it is present. ``merge`` combines both
    sources without precedence: duplicate feature IDs are rejected. Plugin
    enablement does not affect ingestion of already-produced descriptors, and
    external science tools are never run while loading.
    """

    root = Path(path).expanduser().resolve()
    discovered = discover_path(
        root,
        mode=mode,
        strict=strict,
        prefer_contcar=prefer_contcar,
        structure_name=structure_name,
        fallback_structure_name=fallback_structure_name,
    )
    if descriptor_source == "auto":
        descriptor_source = "local" if descriptor_dir is None else "external"
    frames: list[Frame] = []
    evidence: list[dict[tuple[str, str | None], tuple[str, ...]]] = []
    for source in discovered.frames:
        metadata = {
            "path_root": str(discovered.root),
            "frame_directory": str(source.directory),
            "series_kind": discovered.kind,
            "descriptor_source_policy": descriptor_source,
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
            descriptor_dir,
            frame_count=len(discovered.frames),
            descriptor_name=descriptor_name,
            descriptor_source=descriptor_source,
        )
        documents = [load_descriptor(candidate, frame=frame) for candidate in descriptor_paths]
        evidence.append(_frame_evidence(descriptor_paths, documents))
        if documents:
            frame = attach_descriptors(frame, documents)
            frame.metadata["descriptor_sources"] = list(map(str, descriptor_paths))
        spin_paths = _spin_candidates(source, descriptor_dir, descriptor_source)
        if len(spin_paths) > 1:
            raise DescriptorError(
                f"frame {source.label} has more than one spin population file: "
                + ", ".join(map(str, spin_paths))
            )
        if spin_paths:
            frame = attach_spin_populations(frame, spin_paths[0])
        frames.append(attach_net_charges(frame, source.directory))
    return Sequence(
        frames=tuple(frames),
        metadata={
            "source_root": str(root),
            "series_kind": discovered.kind,
            "discovery_warnings": list(discovered.warnings),
            "descriptor_evidence": evidence,
        },
    )


def load_path(path: str | Path, **options: Any) -> list[Frame]:
    """Load a path with :func:`load_sequence` and return its frames."""

    return list(load_sequence(path, **options).frames)
