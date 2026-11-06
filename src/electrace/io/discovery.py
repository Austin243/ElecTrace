"""Discovery of VASP NEB images and ordered static calculation series.

The discovery layer deliberately does not read large volumetric files.  It only
identifies frame directories, chooses a structure file, and extracts small
scalar metadata (energy and pressure) when those files are present.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from .vasp import read_vasp_energy


class DiscoveryError(ValueError):
    """Raised when a directory cannot be interpreted as an ordered path."""


@dataclass(frozen=True, slots=True)
class FrameSource:
    """Files and lightweight metadata associated with one path frame."""

    index: int
    label: str
    directory: Path
    structure: Path
    energy_ev: float | None
    pressure_gpa: float | None
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("frame label must not be empty")
        if self.pressure_gpa is not None and not math.isfinite(self.pressure_gpa):
            raise ValueError("frame pressure must be finite")


@dataclass(frozen=True, slots=True)
class DiscoveredPath:
    """An ordered collection of VASP frame sources."""

    root: Path
    kind: str  # "neb", "static", or "pressure"
    frames: tuple[FrameSource, ...]
    warnings: tuple[str, ...] = ()


_NUMERIC_DIRECTORY = re.compile(r"^[0-9]+$")
_PRESSURE_PATTERNS = (
    re.compile(
        r"(?i)(?<![a-z0-9])(?:p(?:ressure)?[_=-]?)?"
        r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))[_ -]*(?P<unit>gpa|kbar|kb|bar)(?![a-z])"
    ),
    re.compile(
        r"(?i)(?:^|[_ -])p(?:ressure)?[_=-]?"
        r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:$|[_ -])"
    ),
)
_GPA_PER_UNIT = {"gpa": 1.0, "kbar": 0.1, "kb": 0.1, "bar": 1.0e-4}
_OUTCAR_PRESSURE = re.compile(
    r"external\s+pressure\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s*kB",
    re.IGNORECASE,
)


def _input_filename(value: str, field_name: str) -> str:
    name = value.strip()
    if (
        not name
        or name in {".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or "/" in name
        or "\\" in name
    ):
        raise DiscoveryError(f"{field_name} must be a basename, not a path: {value!r}")
    return name


def _structure_in(
    directory: Path, prefer_contcar: bool, structure_name: str, fallback_structure_name: str
) -> Path | None:
    primary = _input_filename(structure_name, "structure_name")
    fallback = _input_filename(fallback_structure_name, "fallback_structure_name")
    names = (primary, fallback) if prefer_contcar else (fallback, primary)
    for name in names:
        candidate = directory / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _frame_source(
    index: int, directory: Path, structure: Path, pressure: float | None = None, **metadata: Any
) -> FrameSource:
    """Describe one frame, taking its energy from the first of OUTCAR and OSZICAR that has one."""

    energy = None
    for filename in ("OUTCAR", "OSZICAR"):
        candidate = directory / filename
        if candidate.is_file():
            energy = read_vasp_energy(candidate)
            if energy is not None:
                metadata["energy_source"] = filename
                break
    return FrameSource(
        index=index,
        label=directory.name,
        directory=directory,
        structure=structure,
        energy_ev=energy,
        pressure_gpa=pressure,
        metadata=metadata,
    )


def pressure_from_label(label: str) -> float | None:
    """Return pressure in GPa encoded in a directory label, if any.

    Recognized examples include ``20GPa``, ``pressure_7.5_gpa``, ``p-12``,
    and ``100kbar``.  A unitless value is accepted only with a ``p`` or
    ``pressure`` prefix and is interpreted as GPa.
    """

    for pattern in _PRESSURE_PATTERNS:
        match = pattern.search(label)
        if match is not None:
            unit = (match.groupdict().get("unit") or "gpa").lower()
            return float(match.group("value")) * _GPA_PER_UNIT[unit]
    return None


def pressure_from_outcar(source: Path) -> float | None:
    """Extract the last VASP external pressure and convert kbar to GPa."""

    if not source.is_file():
        return None
    last: float | None = None
    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _OUTCAR_PRESSURE.search(line)
            if match is not None:
                last = float(match.group(1)) * 0.1
    return last


def discover_neb(
    root: str | Path,
    *,
    prefer_contcar: bool = True,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
    strict: bool = True,
) -> DiscoveredPath:
    """Discover VASP NEB images in numerically named child directories.

    Directories are ordered by their integer value, never lexicographically.
    Frame indices are canonical zero-based positions in that order; the source
    directory number is retained as ``metadata['directory_index']``.  In strict
    mode, every numeric directory must contain a POSCAR or non-empty CONTCAR,
    normalized numeric indices must be unique (``01`` and ``1`` are an error),
    and image numbers must be contiguous.
    """

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise DiscoveryError(f"path root is not a directory: {base}")

    numeric = [
        child
        for child in base.iterdir()
        if child.is_dir() and _NUMERIC_DIRECTORY.fullmatch(child.name)
    ]
    if not numeric:
        raise DiscoveryError(
            f"no numeric NEB image directories found under {base}; name the images 00, 01, ... "
            'or set series_kind = "ordered" to keep the order of other names'
        )

    by_index: dict[int, tuple[Path, Path]] = {}
    for directory in numeric:
        index = int(directory.name)
        if index in by_index:
            raise DiscoveryError(
                f"ambiguous NEB indices: {by_index[index][0].name!r} and {directory.name!r} both mean {index}"
            )
        structure = _structure_in(
            directory, prefer_contcar, structure_name, fallback_structure_name
        )
        if structure is None:
            if strict:
                raise DiscoveryError(
                    f"numeric image directory {directory} has no usable "
                    f"{structure_name} or {fallback_structure_name}"
                )
            continue
        by_index[index] = (directory, structure)

    if len(by_index) < 2:
        raise DiscoveryError("a NEB path requires at least two usable numeric image directories")
    ordered_indices = sorted(by_index)
    if strict:
        expected = list(range(ordered_indices[0], ordered_indices[-1] + 1))
        if ordered_indices != expected:
            missing = sorted(set(expected) - set(ordered_indices))
            raise DiscoveryError(f"NEB image indices are not contiguous; missing {missing}")

    frames = tuple(
        _frame_source(canonical_index, *by_index[directory_index], directory_index=directory_index)
        for canonical_index, directory_index in enumerate(ordered_indices)
    )
    return DiscoveredPath(root=base, kind="neb", frames=frames)


def _natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    pieces = re.split(r"(\d+)", value.casefold())
    return tuple((0, int(piece)) if piece.isdigit() else (1, piece) for piece in pieces if piece)


def _number_decides(left: str, right: str) -> bool:
    """Return whether the first difference between two names is a plain number.

    ``re.split`` puts digit runs at odd positions. Digits after a decimal point are a
    fraction, which the natural order misreads when their lengths differ (0.5, 0.25).
    """

    first, second = (re.split(r"(\d+)", name.casefold()) for name in (left, right))
    for position, (a, b) in enumerate(zip(first, second, strict=False)):
        if position % 2 == 0:
            if a != b:
                return False
        elif int(a) != int(b):
            return first[position - 1] != "." or len(a) == len(b)
    return False


def discover_static_series(
    root: str | Path,
    *,
    prefer_contcar: bool = True,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
    mode: str = "auto",
) -> DiscoveredPath:
    """Discover an ordered series of VASP static-calculation directories.

    Frames keep the natural order of their directory names, whatever an OUTCAR says:
    every VASP run prints a residual stress, so it does not place a frame on a path.
    Only two things order frames by pressure, and their order is warned about when it
    differs from the names': ``mode="pressure"``, which reads each pressure from the name
    or else the last ``external pressure`` record in OUTCAR and rejects a frame without
    one, and ``mode="auto"`` when every name states a pressure. ``mode="static"`` and
    ``mode="ordered"`` always keep the name order; ``ordered`` still calls a series
    whose names all state a pressure a pressure series, and ``static`` never does.

    A warning also says when numbers in the names do not set their order, as in ``IS``,
    ``TS``, ``FS``, because no rule can then tell the path order from the names.
    """

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise DiscoveryError(f"path root is not a directory: {base}")
    found: list[tuple[Path, Path, float | None]] = []
    names_state_pressure = True
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        structure = _structure_in(
            directory, prefer_contcar, structure_name, fallback_structure_name
        )
        if structure is None:
            continue
        pressure = pressure_from_label(directory.name)
        if pressure is None:
            names_state_pressure = False
            pressure = pressure_from_outcar(directory / "OUTCAR")
        found.append((directory, structure, pressure))
    if not found:
        raise DiscoveryError(
            f"no child directories with {structure_name} or {fallback_structure_name} "
            f"found under {base}"
        )
    missing = sorted(directory.name for directory, _, pressure in found if pressure is None)
    if mode == "pressure" and missing:
        raise DiscoveryError(f"pressure is missing for static frames: {', '.join(missing)}")

    found.sort(key=lambda item: (_natural_key(item[0].name), item[0].name))
    names = [directory.name for directory, _, _ in found]
    warnings: list[str] = []
    if mode == "pressure" or (mode == "auto" and names_state_pressure):
        found.sort(key=lambda item: (item[2], _natural_key(item[0].name)))
        pressures = [pressure for _, _, pressure in found]
        if len(pressures) != len(set(pressures)):
            raise DiscoveryError("pressure series contains duplicate pressure values")
        order = [directory.name for directory, _, _ in found]
        if order != names:
            warnings.append(
                f"Frames are ordered by pressure ({', '.join(order)}), not by name "
                f'({", ".join(names)}). Set series_kind = "ordered" to keep the name order.'
            )
    elif not all(_number_decides(left, right) for left, right in pairwise(names)):
        warnings.append(
            f"Frames are in the natural order of their names ({', '.join(names)}), but numbers "
            "in those names do not set it, so this may not be the path order, and every event "
            "and barrier label depends on it. Name or link the frame directories 00, 01, ... "
            "to set the order."
        )

    frames = tuple(
        _frame_source(index, directory, structure, pressure)
        for index, (directory, structure, pressure) in enumerate(found)
    )
    is_pressure = mode == "pressure" or (names_state_pressure and mode != "static")
    return DiscoveredPath(
        root=base,
        kind="pressure" if is_pressure else "static",
        frames=frames,
        warnings=tuple(warnings),
    )


def discover_path(
    root: str | Path,
    *,
    mode: str = "auto",
    strict: bool = True,
    prefer_contcar: bool = True,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
) -> DiscoveredPath:
    """Discover an ordered VASP path, automatically detecting NEB layouts."""

    if mode == "auto":
        base = Path(root).expanduser().resolve()
        numeric_with_structures = 0
        if base.is_dir():
            numeric_with_structures = sum(
                1
                for child in base.iterdir()
                if child.is_dir()
                and _NUMERIC_DIRECTORY.fullmatch(child.name)
                and _structure_in(child, prefer_contcar, structure_name, fallback_structure_name)
                is not None
            )
        if numeric_with_structures >= 2:
            mode = "neb"
    if mode == "neb":
        return discover_neb(
            root,
            prefer_contcar=prefer_contcar,
            structure_name=structure_name,
            fallback_structure_name=fallback_structure_name,
            strict=strict,
        )
    if mode in {"auto", "static", "ordered", "pressure"}:
        return discover_static_series(
            root,
            prefer_contcar=prefer_contcar,
            structure_name=structure_name,
            fallback_structure_name=fallback_structure_name,
            mode=mode,
        )
    raise DiscoveryError(
        f"unknown discovery mode {mode!r}; expected auto, neb, static/ordered, or pressure"
    )
