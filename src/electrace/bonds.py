"""Per-bond records for features tied to exactly two atoms.

A bond row is keyed by its two atoms and the periodic image joining them, never by
feature track: a critical point that moves to another atom pair ends one bond's rows
and begins another's, and a bond that forms or breaks starts or stops its rows.
Reporting only; no thresholds apply.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

import numpy as np
import numpy.typing as npt

from electrace.events import _as_finite_float
from electrace.geometry import minimum_image_displacement
from electrace.models import Feature, Sequence

# Values at a bond critical point (critic2, atomic units) and LOBSTER integrals.
BOND_QUANTITIES = ("field", "laplacian", "icohp", "icobi")

Translation = tuple[int, int, int]
BondKey = tuple[int, int, Translation]
FloatArray = npt.NDArray[np.float64]


def _explicit_translation(feature: Feature) -> Translation | None:
    """Return the image of the feature's higher-index atom, when the feature names one.

    LOBSTER's translation selects the image of its second listed atom, recorded as
    ``lattice_translation_atom``. That atom need not have the higher index (LOBSTER
    lists ``Cl5 Na1``), and ``neighbor_atom_indices`` is sorted.
    """

    raw = feature.attributes.get("lattice_translation")
    if raw is None:
        return None
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 3
        or any(isinstance(item, bool) or not isinstance(item, int) for item in raw)
    ):
        raise ValueError(f"feature {feature.id!r} lattice_translation must be three integers")
    translation = (raw[0], raw[1], raw[2])
    atoms = feature.neighbor_atom_indices
    if len(atoms) == 1 or not any(translation):
        return translation
    translated = feature.attributes.get("lattice_translation_atom")
    if translated == atoms[1]:
        return translation
    if translated == atoms[0]:
        return (-translation[0], -translation[1], -translation[2])
    raise ValueError(
        f"feature {feature.id!r} has a lattice_translation without a "
        f"lattice_translation_atom naming atom {atoms[0]} or {atoms[1]}"
    )


def _pair(feature: Feature) -> tuple[int, int] | None:
    atoms = feature.neighbor_atom_indices
    if len(atoms) == 2:
        return atoms[0], atoms[1]
    raw = feature.attributes.get("lattice_translation")
    if len(atoms) == 1 and isinstance(raw, (list, tuple)) and any(raw):
        # A bond to the atom's own periodic image lists that atom once.
        return atoms[0], atoms[0]
    return None


def _unwrapping_shifts(sequence: Sequence) -> list[dict[int, Translation]]:
    """Return, per frame, the lattice shift that makes each atom's path continuous.

    Each frame's positions are wrapped into the cell on their own, so an atom near a
    cell face can jump by a lattice vector between frames, which would change the
    minimum-image translation of its bonds. Rounding each step's fractional change
    undoes the jump; atoms are assumed to move less than half a cell vector per step.
    """

    shifts: list[dict[int, Translation]] = [
        {atom.index: (0, 0, 0) for atom in sequence.frames[0].atoms}
    ]
    for before, after in pairwise(sequence.frames):
        previous = {atom.index: atom.fractional_position for atom in before.atoms}
        current: dict[int, Translation] = {}
        for atom in after.atoms:
            shift = shifts[-1].get(atom.index, (0, 0, 0))
            old = previous.get(atom.index, atom.fractional_position)
            new = atom.fractional_position
            current[atom.index] = (
                shift[0] - round(new[0] - old[0]),
                shift[1] - round(new[1] - old[1]),
                shift[2] - round(new[2] - old[2]),
            )
        shifts.append(current)
    return shifts


def _nearest_image(cell: FloatArray, left: FloatArray, right: FloatArray) -> Translation:
    """Return the lattice translation of *right* nearest to *left*."""

    displacement = minimum_image_displacement(left, right, cell)
    fractional = displacement @ np.linalg.inv(cell)
    image = np.rint(fractional - (right - left))
    return (int(image[0]), int(image[1]), int(image[2]))


def bond_records(
    sequence: Sequence, tracks: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return one record per atom pair, periodic image, and frame, plus warnings.

    Features that share a pair and image in a frame, such as a critic2 bond critical
    point and LOBSTER's ICOHP and ICOBI for that bond, are joined into one row.
    Features that would give one row two values of the same quantity keep separate
    rows instead. ``bond_id`` follows a pair and image along the path, through atoms
    that wrap across the cell. Missing values stay ``None``.
    """

    track_of = {(item["frame"], item["feature_id"]): item["track_id"] for item in tracks}
    shifts = _unwrapping_shifts(sequence)
    numbers: dict[BondKey, int] = {}
    rows: list[tuple[int, int, dict[str, Any]]] = []
    warnings: list[str] = []
    for position, frame in enumerate(sequence.frames):
        cell = np.asarray(frame.cell, dtype=float)
        atoms = {atom.index: atom for atom in frame.atoms}
        coordinates = {
            index: np.asarray(atom.fractional_position, dtype=float)
            for index, atom in atoms.items()
        }
        shift = shifts[position]
        groups: dict[BondKey, list[Feature]] = {}
        images: dict[BondKey, Translation] = {}
        for feature in frame.features:
            pair = _pair(feature)
            if pair is None:
                continue
            first, second = pair
            explicit = _explicit_translation(feature)
            image = (
                explicit
                if explicit is not None
                else _nearest_image(cell, coordinates[first], coordinates[second])
            )
            # The same image relative to positions unwrapped along the path.
            path_image = (
                image[0] + shift[first][0] - shift[second][0],
                image[1] + shift[first][1] - shift[second][1],
                image[2] + shift[first][2] - shift[second][2],
            )
            if first == second and (-path_image[0], -path_image[1], -path_image[2]) > path_image:
                # A bond to an atom's own image is the same bond read from either end.
                path_image = (-path_image[0], -path_image[1], -path_image[2])
                image = (-image[0], -image[1], -image[2])
            key = (first, second, path_image)
            groups.setdefault(key, []).append(feature)
            images[key] = image
        for key in sorted(groups):
            first, second, _ = key
            number = numbers.setdefault(key, len(numbers) + 1)
            members = groups[key]
            given = [
                name
                for feature in members
                for name in BOND_QUANTITIES
                if _as_finite_float(feature.attributes.get(name)) is not None
            ]
            parts = [members]
            if len(given) != len(set(given)):
                warnings.append(
                    f"Frame {frame.label}: {len(members)} features tie atoms {first} and "
                    f"{second} through one periodic image and give the same quantity; "
                    "each keeps its own bond row."
                )
                parts = [[feature] for feature in members]
            image = images[key]
            vector = (coordinates[second] - coordinates[first] + image) @ cell
            for features in parts:
                record: dict[str, Any] = {
                    "bond_id": f"B{number:05d}",
                    "frame": frame.index,
                    "frame_label": frame.label,
                    "atom_a": first,
                    "element_a": atoms[first].element,
                    "atom_b": second,
                    "element_b": atoms[second].element,
                    "lattice_translation": list(image),
                    "distance": float(np.linalg.norm(vector)),
                }
                for name in BOND_QUANTITIES:
                    values = (_as_finite_float(item.attributes.get(name)) for item in features)
                    record[name] = next((value for value in values if value is not None), None)
                record["feature_ids"] = [feature.id for feature in features]
                record["track_ids"] = sorted(
                    {
                        track_of[(frame.index, feature.id)]
                        for feature in features
                        if (frame.index, feature.id) in track_of
                    }
                )
                rows.append((number, position, record))
    rows.sort(key=lambda row: (row[0], row[1]))
    return [record for _, _, record in rows], warnings
