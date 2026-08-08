"""Dependency-light readers for the small VASP files ElecTrace needs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from electrace.models import Atom, Frame


class VaspParseError(ValueError):
    """Raised when a VASP structure or scalar output is malformed."""


Vector3 = tuple[float, float, float]
Matrix3 = tuple[Vector3, Vector3, Vector3]


@dataclass(frozen=True, slots=True)
class PoscarData:
    """Parsed POSCAR data before it is converted into a core :class:`Frame`."""

    comment: str
    cell: Matrix3
    elements: tuple[str, ...]
    fractional_positions: tuple[Vector3, ...]
    selective_dynamics: tuple[tuple[bool, bool, bool] | None, ...]
    coordinate_mode: str
    symbols_inferred: bool = False

    def __post_init__(self) -> None:
        if not self.elements:
            raise ValueError("POSCAR must contain at least one atom")
        if len(self.elements) != len(self.fractional_positions):
            raise ValueError("element and position counts do not match")
        if len(self.selective_dynamics) != len(self.elements):
            raise ValueError("selective-dynamics and atom counts do not match")


_TOTEN = re.compile(
    r"free\s+energy\s+TOTEN\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)",
    re.IGNORECASE,
)
_OSZICAR_E0 = re.compile(
    r"\bE0=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)",
    re.IGNORECASE,
)
_OSZICAR_F = re.compile(
    r"(?:^|\s)F=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)",
    re.IGNORECASE,
)


def _float(token: str, *, context: str) -> float:
    try:
        value = float(token.replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise VaspParseError(f"invalid number {token!r} in {context}") from exc
    if not math.isfinite(value):
        raise VaspParseError(f"non-finite number {token!r} in {context}")
    return value


def _three_floats(line: str, *, context: str) -> Vector3:
    fields = line.split()
    if len(fields) < 3:
        raise VaspParseError(f"expected three numbers in {context}, got {line!r}")
    return tuple(_float(field, context=context) for field in fields[:3])  # type: ignore[return-value]


def _determinant(matrix: Matrix3) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _inverse(matrix: Matrix3) -> Matrix3:
    a, b, c = matrix
    determinant = _determinant(matrix)
    if abs(determinant) < 1.0e-14:
        raise VaspParseError("POSCAR lattice is singular")
    inverse = (
        (
            (b[1] * c[2] - b[2] * c[1]) / determinant,
            (a[2] * c[1] - a[1] * c[2]) / determinant,
            (a[1] * b[2] - a[2] * b[1]) / determinant,
        ),
        (
            (b[2] * c[0] - b[0] * c[2]) / determinant,
            (a[0] * c[2] - a[2] * c[0]) / determinant,
            (a[2] * b[0] - a[0] * b[2]) / determinant,
        ),
        (
            (b[0] * c[1] - b[1] * c[0]) / determinant,
            (a[1] * c[0] - a[0] * c[1]) / determinant,
            (a[0] * b[1] - a[1] * b[0]) / determinant,
        ),
    )
    return inverse


def _row_vector_times_matrix(vector: Vector3, matrix: Matrix3) -> Vector3:
    return (
        sum(vector[row] * matrix[row][0] for row in range(3)),
        sum(vector[row] * matrix[row][1] for row in range(3)),
        sum(vector[row] * matrix[row][2] for row in range(3)),
    )


def parse_poscar(path: str | Path) -> PoscarData:
    """Parse VASP 4/5 POSCAR or CONTCAR without requiring ASE or pymatgen.

    Direct and Cartesian coordinates, selective-dynamics flags, positive and
    negative volume scale factors, and VASP's three-factor lattice scaling are
    supported.  VASP 4 files without element symbols receive stable ``X1``,
    ``X2``, ... placeholders and are marked with ``symbols_inferred=True``.
    """

    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise VaspParseError(f"cannot read VASP structure {source}: {exc}") from exc
    if len(lines) < 8:
        raise VaspParseError(f"{source} is too short to be a POSCAR")

    comment = lines[0].strip()
    scale_tokens = lines[1].split()
    if len(scale_tokens) not in {1, 3}:
        raise VaspParseError("POSCAR scale line must contain one or three numbers")
    scales = tuple(_float(token, context="POSCAR scale") for token in scale_tokens)
    raw_cell: Matrix3 = tuple(
        _three_floats(lines[index], context=f"lattice vector {index - 1}") for index in range(2, 5)
    )  # type: ignore[assignment]
    raw_volume = abs(_determinant(raw_cell))
    if raw_volume < 1.0e-14:
        raise VaspParseError("POSCAR lattice is singular")

    coordinate_scale: Vector3
    if len(scales) == 1:
        scale = scales[0]
        if scale == 0.0:
            raise VaspParseError("POSCAR scale factor cannot be zero")
        if scale < 0.0:
            scale = (-scale / raw_volume) ** (1.0 / 3.0)
        cell: Matrix3 = tuple(
            tuple(component * scale for component in vector) for vector in raw_cell
        )  # type: ignore[assignment]
        coordinate_scale = (scale, scale, scale)
    else:
        if any(scale <= 0.0 for scale in scales):
            raise VaspParseError("three POSCAR scale factors must all be positive")
        cell = tuple(
            tuple(raw_cell[row][axis] * scales[axis] for axis in range(3)) for row in range(3)
        )  # type: ignore[assignment]
        coordinate_scale = scales  # type: ignore[assignment]

    symbol_or_count = lines[5].split()
    if not symbol_or_count:
        raise VaspParseError("POSCAR symbol/count line is empty")
    symbols_inferred = all(token.lstrip("+-").isdigit() for token in symbol_or_count)
    if symbols_inferred:
        counts_line_index = 5
        symbols = [f"X{index + 1}" for index in range(len(symbol_or_count))]
    else:
        counts_line_index = 6
        symbols = symbol_or_count
    if counts_line_index >= len(lines):
        raise VaspParseError("POSCAR is missing atom counts")
    count_tokens = lines[counts_line_index].split()
    if len(count_tokens) != len(symbols):
        raise VaspParseError("POSCAR element symbol and count lengths differ")
    try:
        counts = [int(token) for token in count_tokens]
    except ValueError as exc:
        raise VaspParseError("POSCAR atom counts must be integers") from exc
    if any(count < 0 for count in counts) or sum(counts) == 0:
        raise VaspParseError("POSCAR atom counts must be non-negative with a positive total")
    elements = tuple(
        symbol for symbol, count in zip(symbols, counts, strict=True) for _ in range(count)
    )

    cursor = counts_line_index + 1
    if cursor >= len(lines):
        raise VaspParseError("POSCAR is missing coordinate mode")
    has_selective = lines[cursor].strip().lower().startswith("s")
    if has_selective:
        cursor += 1
        if cursor >= len(lines):
            raise VaspParseError("POSCAR is missing coordinate mode after Selective dynamics")
    mode_token = lines[cursor].strip().lower()
    if mode_token.startswith("d"):
        coordinate_mode = "direct"
    elif mode_token.startswith(("c", "k")):
        coordinate_mode = "cartesian"
    else:
        raise VaspParseError(f"unknown POSCAR coordinate mode {lines[cursor]!r}")
    cursor += 1
    if len(lines) < cursor + len(elements):
        raise VaspParseError(
            f"POSCAR declares {len(elements)} atoms but has only {max(0, len(lines) - cursor)} coordinate lines"
        )

    fractional: list[Vector3] = []
    selective: list[tuple[bool, bool, bool] | None] = []
    inverse_cell = _inverse(cell)
    for atom_index in range(len(elements)):
        line = lines[cursor + atom_index]
        position = _three_floats(line, context=f"atom {atom_index + 1} coordinates")
        fields = line.split()
        if coordinate_mode == "cartesian":
            cartesian = tuple(position[axis] * coordinate_scale[axis] for axis in range(3))
            position = _row_vector_times_matrix(cartesian, inverse_cell)  # type: ignore[arg-type]
        fractional.append(position)
        if has_selective:
            if len(fields) < 6 or any(flag[:1].lower() not in {"t", "f"} for flag in fields[3:6]):
                raise VaspParseError(f"invalid selective-dynamics flags for atom {atom_index + 1}")
            selective.append(tuple(flag[:1].lower() == "t" for flag in fields[3:6]))  # type: ignore[arg-type]
        else:
            selective.append(None)

    return PoscarData(
        comment=comment,
        cell=cell,
        elements=elements,
        fractional_positions=tuple(fractional),
        selective_dynamics=tuple(selective),
        coordinate_mode=coordinate_mode,
        symbols_inferred=symbols_inferred,
    )


def poscar_to_frame(
    data: PoscarData,
    *,
    index: int = 0,
    label: str = "",
    energy: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> Frame:
    """Convert parsed POSCAR data into ElecTrace's core ``Frame`` model."""

    atoms = []
    for atom_index, (element, position, flags) in enumerate(
        zip(
            data.elements,
            data.fractional_positions,
            data.selective_dynamics,
            strict=True,
        )
    ):
        attributes: dict[str, Any] = {}
        if flags is not None:
            attributes["selective_dynamics"] = flags
        atoms.append(
            Atom(
                index=atom_index,
                element=element,
                fractional_position=position,
                attributes=attributes,
            )
        )
    frame_metadata: dict[str, Any] = {
        "comment": data.comment,
        "coordinate_mode": data.coordinate_mode,
        "symbols_inferred": data.symbols_inferred,
    }
    if metadata:
        frame_metadata.update(metadata)
    return Frame(
        index=index,
        label=label or str(index),
        cell=data.cell,
        atoms=tuple(atoms),
        features=(),
        energy=energy,
        metadata=frame_metadata,
    )


def read_poscar(
    path: str | Path,
    *,
    index: int = 0,
    label: str | None = None,
    energy: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> Frame:
    """Read a POSCAR/CONTCAR directly into an ElecTrace ``Frame``."""

    source = Path(path)
    frame_metadata = {"structure_source": str(source)}
    if metadata:
        frame_metadata.update(metadata)
    return poscar_to_frame(
        parse_poscar(source),
        index=index,
        label=label or source.parent.name or source.name,
        energy=energy,
        metadata=frame_metadata,
    )


def read_vasp_energy(path: str | Path) -> float | None:
    """Extract a final electronic energy in eV from OUTCAR or OSZICAR.

    For OUTCAR the last ``free energy TOTEN`` value is returned.  For OSZICAR
    the last ionic ``E0=`` value is preferred, falling back to ``F=``.  A
    missing or incomplete energy record returns ``None`` rather than making an
    otherwise usable path unreadable.
    """

    source = Path(path)
    if not source.is_file():
        return None
    toten: float | None = None
    e0: float | None = None
    free: float | None = None
    try:
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                for pattern, target in (
                    (_TOTEN, "toten"),
                    (_OSZICAR_E0, "e0"),
                    (_OSZICAR_F, "free"),
                ):
                    match = pattern.search(line)
                    if match is None:
                        continue
                    value = _float(match.group(1), context="VASP energy")
                    if target == "toten":
                        toten = value
                    elif target == "e0":
                        e0 = value
                    else:
                        free = value
    except OSError:
        return None
    if source.name.upper() == "OSZICAR":
        return e0 if e0 is not None else free
    # Be permissive when a caller passes content with a nonstandard filename.
    return toten if toten is not None else (e0 if e0 is not None else free)
