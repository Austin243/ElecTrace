"""Charges and spins summed over groups of atoms, with a spectator-molecule noise floor.

A Bader partition puts a zero-flux surface between every pair of bonded atoms, and a
small shift of a surface through the dense density inside a molecule moves charge
from one atom to its neighbor. A sum over a group of atoms does not depend on the
surfaces inside the group, only on those around it, which between molecules run
through low density. Groups are named lists of 0-based atom indices or the molecules
of the first frame: atoms joined by covalent bonds under the element-pair cutoffs of
``focus_atoms = "auto"``. Molecules that keep every bond through every frame and hold
no focus atom are spectators, and their adjacent-frame changes give the floor.

Reporting only: no assignment, threshold, event, or confidence depends on it.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

import numpy as np

from electrace.events import _as_finite_float
from electrace.focus import _PERCENTILES, _bonds, check_atom_order
from electrace.models import Feature, FeatureKind, Frame, Sequence

GROUP_QUANTITIES = ("charge", "net_charge", "spin")
# A fixed group's net charge and population change by the same amount, so the floor
# of population steps also serves net charges.
FLOOR_QUANTITIES = ("charge", "spin")
_SETTING = "outputs.atom_groups and outputs.molecule_groups need"

Producer = str | None


def formula(elements: Iterable[str]) -> str:
    """Return a Hill formula: C, then H, then the other elements alphabetically."""

    counts = Counter(element.strip().capitalize() for element in elements)
    order = [element for element in ("C", "H") if element in counts] if "C" in counts else []
    order += sorted(element for element in counts if element not in order)
    return "".join(
        f"{element}{counts[element] if counts[element] > 1 else ''}" for element in order
    )


def _atom_charges(frame: Frame) -> dict[tuple[Producer, int], list[Feature]]:
    """Each producer's atom-charge features by atom; BaderKit basins are not atoms."""

    found: dict[tuple[Producer, int], list[Feature]] = {}
    for feature in frame.features:
        if feature.kind != FeatureKind.ATOMIC_CHARGE or len(feature.neighbor_atom_indices) != 1:
            continue
        producer = feature.attributes.get("descriptor_producer")
        key = (None if producer is None else str(producer), feature.neighbor_atom_indices[0])
        found.setdefault(key, []).append(feature)
    return found


def _sums(
    charges: Mapping[tuple[Producer, int], list[Feature]], producer: Producer, atoms: list[int]
) -> tuple[dict[str, float | None], str | None]:
    """Sum each quantity over *atoms*, or give None when any atom lacks a value.

    A partial sum would read as a charge change, so one missing or doubled atom
    leaves the whole frame empty.
    """

    features: list[Feature] = []
    for atom in atoms:
        found = charges.get((producer, atom), [])
        if len(found) != 1:
            empty = dict.fromkeys(GROUP_QUANTITIES)
            return empty, f"atom {atom} has {len(found)} charge features"
        features.append(found[0])
    values: dict[str, float | None] = {}
    for quantity in GROUP_QUANTITIES:
        parts = [
            _as_finite_float(item.charge if quantity == "charge" else item.attributes.get(quantity))
            for item in features
        ]
        present = [part for part in parts if part is not None]
        values[quantity] = math.fsum(present) if len(present) == len(parts) else None
    return values, None


def _components(atoms: Iterable[int], bonds: Iterable[tuple[int, int]]) -> list[list[int]]:
    parent = {atom: atom for atom in atoms}

    def root(atom: int) -> int:
        while parent[atom] != atom:
            parent[atom] = parent[parent[atom]]
            atom = parent[atom]
        return atom

    for first, second in bonds:
        parent[root(first)] = root(second)
    groups: dict[int, list[int]] = {}
    for atom in sorted(parent):
        groups.setdefault(root(atom), []).append(atom)
    return sorted(groups.values(), key=lambda members: members[0])


def _molecules(
    sequence: Sequence, cutoffs: Mapping[str, float], focus: set[int]
) -> tuple[dict[str, Any], list[list[int]], list[list[int]], set[tuple[int, int]] | None]:
    """Find the first frame's molecules and the spectators among them.

    Returns the record, the molecules, the spectators, and the first frame's bonds,
    which are None when bonds could not be found.
    """

    try:
        graphs = [_bonds(frame, cutoffs, "molecule groups") for frame in sequence.frames]
    except ValueError as exc:
        return {"cutoffs": dict(sorted(cutoffs.items())), "reason": str(exc)}, [], [], None
    missing = sorted(set().union(*(absent for _, absent in graphs)))
    if missing:
        reason = (
            f"No bond cutoff for {', '.join(missing)}, so molecules are not defined; set them "
            "under [outputs.focus_bond_cutoffs]."
        )
        return {"cutoffs": dict(sorted(cutoffs.items())), "reason": reason}, [], [], None
    bonds = [pairs for pairs, _ in graphs]
    first = sequence.frames[0]
    molecules = _components((atom.index for atom in first.atoms), bonds[0])
    changed = bonds[0].union(*bonds[1:]) - bonds[0].intersection(*bonds[1:])
    touched = focus | {atom for pair in changed for atom in pair}
    spectators = [members for members in molecules if not touched.intersection(members)]
    element = {atom.index: atom.element for atom in first.atoms}
    record: dict[str, Any] = {
        "frame": first.index,
        "cutoffs": dict(sorted(cutoffs.items())),
        "count": len(molecules),
        "formulas": dict(
            sorted(Counter(formula(element[a] for a in members) for members in molecules).items())
        ),
        "spectators": len(spectators),
        "spectator_formulas": dict(
            sorted(Counter(formula(element[a] for a in members) for members in spectators).items())
        ),
    }
    if not spectators:
        record["reason"] = (
            "No molecule keeps its bonds through every frame without a focus atom, so there "
            "is no spectator floor."
        )
    return record, molecules, spectators, bonds[0]


def _series(
    sequence: Sequence,
    charges: list[dict[tuple[Producer, int], list[Feature]]],
    producer: Producer,
    atoms: list[int],
) -> tuple[list[dict[str, Any]], list[str]]:
    values: list[dict[str, Any]] = []
    missing: list[str] = []
    for frame, found in zip(sequence.frames, charges, strict=True):
        sums, reason = _sums(found, producer, atoms)
        values.append({"frame": frame.index, "frame_label": frame.label, **sums})
        if reason is not None:
            missing.append(f"{frame.label} ({reason})")
    return values, missing


def _floor(
    sequence: Sequence,
    charges: list[dict[tuple[Producer, int], list[Feature]]],
    producers: list[Producer],
    spectators: list[list[int]],
) -> list[dict[str, Any]]:
    """Percentiles of adjacent-frame |Δ| of spectator-molecule sums, per producer."""

    entries: list[dict[str, Any]] = []
    for producer in producers:
        steps: dict[str, list[float]] = {quantity: [] for quantity in FLOOR_QUANTITIES}
        contributors: dict[str, set[int]] = {quantity: set() for quantity in FLOOR_QUANTITIES}
        for number, members in enumerate(spectators):
            values, _ = _series(sequence, charges, producer, members)
            for before, after in pairwise(values):
                for quantity in FLOOR_QUANTITIES:
                    left, right = before[quantity], after[quantity]
                    if left is not None and right is not None:
                        steps[quantity].append(abs(right - left))
                        contributors[quantity].add(number)
        for quantity in FLOOR_QUANTITIES:
            if not steps[quantity]:
                continue
            percentiles = np.percentile(steps[quantity], [value for _, value in _PERCENTILES])
            entries.append(
                {
                    "producer": producer,
                    "quantity": quantity,
                    "molecules": len(contributors[quantity]),
                    "steps": len(steps[quantity]),
                    **{
                        name: float(value)
                        for (name, _), value in zip(_PERCENTILES, percentiles, strict=True)
                    },
                }
            )
    return entries


def atom_groups(
    sequence: Sequence,
    *,
    configured: Mapping[str, list[int]],
    molecule_groups: bool,
    bond_cutoffs: Mapping[str, float],
    focus_atoms: Iterable[int],
) -> tuple[dict[str, Any], list[str]]:
    """Sum atom charges and spins over configured groups and first-frame molecules.

    With a focus, only molecules that hold a focus atom are reported; every molecule
    still counts toward the spectator floor test. Each group gets one record per
    charge producer, with a value per frame that is null where any of its atoms lacks
    one. The whole-path changes are added by the caller.
    """

    check_atom_order(sequence, _SETTING)
    first = sequence.frames[0]
    known = {atom.index for atom in first.atoms}
    for name, atoms in configured.items():
        absent = sorted(set(atoms) - known)
        if absent:
            raise ValueError(
                f"outputs.atom_groups.{name} lists {absent}, which are not 0-based atom "
                f"indices of frame {first.label}, which has {len(first.atoms)} atoms"
            )
    focus = set(focus_atoms)
    record, molecules, spectators, first_bonds = _molecules(sequence, bond_cutoffs, focus)
    charges = [_atom_charges(frame) for frame in sequence.frames]
    producers: list[Producer] = sorted(
        {producer for found in charges for producer, _ in found}, key=lambda item: item or ""
    )
    element = {atom.index: atom.element for atom in first.atoms}
    warnings: list[str] = []
    if not producers:
        warnings.append("Atom groups have no atom charges to sum in any frame.")

    chosen: list[tuple[str, str, list[int]]] = [
        (name, "configured", sorted(atoms)) for name, atoms in configured.items()
    ]
    if molecule_groups:
        for number, members in enumerate(molecules, start=1):
            if not focus or focus.intersection(members):
                chosen.append((f"M{number:03d}", "molecule", members))
        record["reported"] = sum(source == "molecule" for _, source, _ in chosen)
    groups: list[dict[str, Any]] = []
    for group_id, source, atoms in chosen:
        cut = (
            None
            if first_bonds is None
            else [list(pair) for pair in sorted(first_bonds) if len(set(pair) & set(atoms)) == 1]
        )
        for producer in producers:
            values, missing = _series(sequence, charges, producer, atoms)
            if missing:
                warnings.append(
                    f"Atom group {group_id} ({producer}) has no sum in frame(s) "
                    f"{', '.join(missing)}; those values are null, not partial sums."
                )
            groups.append(
                {
                    "group_id": group_id,
                    "source": source,
                    "atoms": atoms,
                    "formula": formula(element[atom] for atom in atoms),
                    "producer": producer,
                    "cut_bonds": cut,
                    "values": values,
                }
            )
    if record.get("reason"):
        warnings.append(f"Atom groups: {record['reason']}")
    floor = _floor(sequence, charges, producers, spectators)
    return {"molecules": record, "noise_floor": floor, "groups": groups}, warnings


def ratio_to_floor(delta: float, entry: Mapping[str, Any] | None) -> dict[str, float | None] | None:
    """Give |delta| as multiples of each floor percentile; a zero percentile gives None."""

    if entry is None:
        return None
    return {name: abs(delta) / entry[name] if entry[name] > 0 else None for name, _ in _PERCENTILES}


__all__ = ["FLOOR_QUANTITIES", "GROUP_QUANTITIES", "atom_groups", "formula", "ratio_to_floor"]
