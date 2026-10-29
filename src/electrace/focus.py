"""Automatic focus atoms and the spectator noise floor.

Both read the finished matching, tracks, and events. They are reporting only: no
assignment, threshold, event, or confidence depends on them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import combinations_with_replacement, pairwise
from typing import Any

import numpy as np

from electrace.config import bond_pair
from electrace.events import _as_finite_float
from electrace.models import Event, EventType, FeatureKind, Frame, Sequence

FOCUS_RULES = ("bond", "spin", "bcp")
NOISE_QUANTITIES = ("charge", "volume", "spin")
_PERCENTILES = (("median", 50.0), ("p95", 95.0), ("p99", 99.0))

Observation = Mapping[str, Any]


@dataclass(slots=True)
class AutoFocus:
    """Atoms chosen by ``focus_atoms = "auto"``, with the rules that chose each."""

    rules: dict[int, list[str]] = field(default_factory=dict)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def check_atom_order(sequence: Sequence) -> None:
    """Require one atom order, which the index-based bond comparison needs."""

    first = sequence.frames[0]
    reference = [(atom.index, atom.element) for atom in first.atoms]
    for frame in sequence.frames[1:]:
        if [(atom.index, atom.element) for atom in frame.atoms] != reference:
            raise ValueError(
                'outputs.focus_atoms = "auto" needs every frame to list the same elements '
                f"in the same atom order; frame {frame.label} differs from frame {first.label}"
            )


def _value(observation: Observation, quantity: str) -> float | None:
    if quantity in ("charge", "volume"):
        return _as_finite_float(observation[quantity])
    return _as_finite_float(observation["attributes"].get(quantity))


def _is_atom(observation: Observation) -> bool:
    # One atom-charge feature per atom and producer; BaderKit's per-maximum
    # atomic_basin features would count an atom more than once.
    return (
        observation["kind"] == FeatureKind.ATOMIC_CHARGE
        and len(observation["neighbor_atom_indices"]) == 1
    )


def _elements(sequence: Sequence) -> dict[tuple[int, int], str]:
    return {
        (frame.index, atom.index): atom.element for frame in sequence.frames for atom in frame.atoms
    }


def _lineages(tracks: Iterable[Observation]) -> dict[str, list[Observation]]:
    lineages: dict[str, list[Observation]] = {}
    # Tracks are emitted frame by frame, so each lineage is in path order.
    for observation in tracks:
        lineages.setdefault(str(observation["track_id"]), []).append(observation)
    return lineages


def _atom_steps(
    observations: list[Observation], position: Mapping[int, int]
) -> list[tuple[Observation, Observation]]:
    """Consecutive atom-charge observations of one lineage in adjacent frames."""

    return [
        (before, after)
        for before, after in pairwise(observations)
        if position[after["frame"]] == position[before["frame"]] + 1
        and _is_atom(before)
        and _is_atom(after)
    ]


def _bonds(frame: Frame, cutoffs: Mapping[str, float]) -> tuple[set[tuple[int, int]], set[str]]:
    """Return the atom pairs within their element-pair cutoff, and the pairs without one.

    Rounding fractional differences finds the minimum image exactly here: with every
    cutoff at most half the smallest lattice-plane spacing, a vector shorter than the
    cutoff has no fractional component of 0.5 or more, and no atom reaches two images
    of a partner or its own image.
    """

    elements = [atom.element.strip().capitalize() for atom in frame.atoms]
    counts = Counter(elements)
    species = sorted(counts)
    code = {element: number for number, element in enumerate(species)}
    limits = np.zeros((len(species), len(species)))
    missing: set[str] = set()
    for first, second in combinations_with_replacement(species, 2):
        if first == second and counts[first] < 2:
            continue
        pair = bond_pair(first, second)
        if pair in cutoffs:
            limits[code[first], code[second]] = limits[code[second], code[first]] = cutoffs[pair]
        else:
            missing.add(pair)
    cell = np.asarray(frame.cell, dtype=float)
    spacing = abs(np.linalg.det(cell)) / np.linalg.norm(
        np.cross(cell[[1, 2, 0]], cell[[2, 0, 1]]), axis=1
    )
    longest = float(limits.max(initial=0.0))
    if 2.0 * longest > spacing.min():
        raise ValueError(
            f'outputs.focus_atoms = "auto": frame {frame.label} has a lattice-plane spacing '
            f"of {spacing.min():.3f} angstrom, less than twice its {longest:.3f} angstrom "
            "bond cutoff, so an atom could bond to more than one periodic image"
        )
    positions = np.asarray([atom.fractional_position for atom in frame.atoms], dtype=float)
    codes = np.asarray([code[element] for element in elements], dtype=int)
    bonds: set[tuple[int, int]] = set()
    for row in range(len(frame.atoms) - 1):
        delta = positions[row + 1 :] - positions[row]
        delta -= np.rint(delta)
        distance = np.linalg.norm(delta @ cell, axis=1)
        for column in np.flatnonzero(distance < limits[codes[row], codes[row + 1 :]]):
            bonds.add((frame.atoms[row].index, frame.atoms[row + 1 + int(column)].index))
    return bonds, missing


def select_focus_atoms(
    sequence: Sequence,
    tracks: Iterable[Observation],
    events: Iterable[Event],
    *,
    bond_cutoffs: Mapping[str, float],
    spin_threshold: float | None,
) -> AutoFocus:
    """Select the atoms whose bonds, spin, or bond-critical-point neighbors change.

    - bond: a covalent bond, by element-pair cutoff under minimum image, is present in
      some frames and absent in others. Atom indices are each frame's own, as for an
      explicit focus.
    - spin: an atom-charge track's spin changes between adjacent frames by at least
      the configured spin threshold, the test spin events use.
    - bcp: the atoms before and after a bond-critical-point connectivity change.
    """

    position = {frame.index: number for number, frame in enumerate(sequence.frames)}
    lineages = _lineages(tracks)
    selection = AutoFocus()
    chosen: dict[str, set[int]] = {}

    graphs: list[set[tuple[int, int]]] = []
    missing: set[str] = set()
    for frame in sequence.frames:
        bonds, absent = _bonds(frame, bond_cutoffs)
        graphs.append(bonds)
        missing |= absent
    changed = graphs[0].union(*graphs[1:]) - graphs[0].intersection(*graphs[1:])
    chosen["bond"] = {atom for pair in changed for atom in pair}
    selection.records["bond"] = {
        "applied": True,
        "cutoffs": dict(sorted(bond_cutoffs.items())),
        "changed_bonds": [list(pair) for pair in sorted(changed)],
    }
    if missing:
        selection.warnings.append(
            f'focus_atoms = "auto" has no bond cutoff for {", ".join(sorted(missing))}, so '
            "those bonds were not compared; set them under [outputs.focus_bond_cutoffs]."
        )

    has_spin = any(
        _is_atom(item) and _value(item, "spin") is not None
        for observations in lineages.values()
        for item in observations
    )
    if not has_spin:
        selection.records["spin"] = {
            "applied": False,
            "threshold": spin_threshold,
            "reason": "No spin populations in any frame.",
        }
    elif spin_threshold is None:
        selection.records["spin"] = {
            "applied": False,
            "threshold": None,
            "reason": "No spin threshold under [events.descriptor_thresholds].",
        }
        selection.warnings.append(
            'focus_atoms = "auto" did not apply the spin rule: spin populations are '
            "present, but [events.descriptor_thresholds] sets no spin threshold."
        )
    else:
        selection.records["spin"] = {"applied": True, "threshold": spin_threshold}
        chosen["spin"] = set()
        for observations in lineages.values():
            for before, after in _atom_steps(observations, position):
                left, right = _value(before, "spin"), _value(after, "spin")
                if left is not None and right is not None and abs(right - left) >= spin_threshold:
                    chosen["spin"].update(
                        before["neighbor_atom_indices"], after["neighbor_atom_indices"]
                    )

    if any(
        feature.kind == FeatureKind.BOND_CRITICAL_POINT
        for frame in sequence.frames
        for feature in frame.features
    ):
        changes = [item for item in events if item.event_type is EventType.CONNECTIVITY_CHANGE]
        chosen["bcp"] = {
            int(atom["index"])
            for item in changes
            for atom in (*item.evidence["neighbors_before"], *item.evidence["neighbors_after"])
        }
        selection.records["bcp"] = {"applied": True, "connectivity_changes": len(changes)}
    else:
        selection.records["bcp"] = {
            "applied": False,
            "connectivity_changes": 0,
            "reason": "No bond critical points in any frame.",
        }

    for rule in FOCUS_RULES:
        for atom in chosen.get(rule, ()):
            selection.rules.setdefault(atom, []).append(rule)
    selection.rules = dict(sorted(selection.rules.items()))
    if not selection.rules:
        selection.warnings.append(
            'focus_atoms = "auto" selected no atoms: no bond, spin, or bond-critical-point '
            "connectivity change was found, so every feature is reported."
        )
    return selection


def auto_focus_record(
    selection: AutoFocus, focus: Mapping[str, Any] | None, analysis: Mapping[str, Any]
) -> dict[str, Any]:
    """Add each atom's rules to the focus record; without atoms, count everything."""

    if focus is None:
        counts = {
            "tracks": len(analysis["track_summary"]),
            "observations": len(analysis["tracks"]),
            "events": len(analysis["events"]),
        }
        focus = {
            "atoms": [],
            **{name: {"reported": count, "total": count} for name, count in counts.items()},
        }
    return {
        **focus,
        "selection": "auto",
        "atoms": [{**atom, "rules": selection.rules[atom["index"]]} for atom in focus["atoms"]],
        "rules": selection.records,
    }


def noise_floor(
    sequence: Sequence, tracks: Iterable[Observation], focus_atoms: Iterable[int]
) -> list[dict[str, Any]]:
    """Percentiles of adjacent-frame |Δ| of spectator atom charges, volumes, and spins.

    A spectator is an atom-charge track that never touches a focus atom, the test the
    focus filter uses. Producers stay apart because their partitions differ, and
    missing values give no step.
    """

    focus = set(focus_atoms)
    position = {frame.index: number for number, frame in enumerate(sequence.frames)}
    elements = _elements(sequence)
    steps: dict[tuple[str | None, str, str], list[float]] = {}
    atoms: dict[tuple[str | None, str, str], set[int]] = {}
    for observations in _lineages(tracks).values():
        if any(focus.intersection(item["neighbor_atom_indices"]) for item in observations):
            continue
        for before, after in _atom_steps(observations, position):
            atom = after["neighbor_atom_indices"][0]
            producer = after["attributes"].get("descriptor_producer")
            for quantity in NOISE_QUANTITIES:
                left, right = _value(before, quantity), _value(after, quantity)
                if left is None or right is None:
                    continue
                key = (producer, elements[after["frame"], atom], quantity)
                steps.setdefault(key, []).append(abs(right - left))
                atoms.setdefault(key, set()).add(atom)
    entries = []
    for key in sorted(steps, key=lambda k: (k[0] or "", k[1], NOISE_QUANTITIES.index(k[2]))):
        producer, element, quantity = key
        values = np.percentile(steps[key], [percentile for _, percentile in _PERCENTILES])
        entries.append(
            {
                "producer": producer,
                "element": element,
                "quantity": quantity,
                "atoms": len(atoms[key]),
                "steps": len(steps[key]),
                **{
                    name: float(value)
                    for (name, _), value in zip(_PERCENTILES, values, strict=True)
                },
            }
        )
    return entries


def annotate_events(
    events: Iterable[dict[str, Any]],
    sequence: Sequence,
    tracks: Iterable[Observation],
    floor: Iterable[Mapping[str, Any]],
) -> None:
    """Give each atom event's flagged changes as multiples of the spectator floor.

    Only a ``noise_floor`` key is added; no event is created, removed, or rescored.
    Volume events are flagged by relative change, but the floor is absolute, so the
    absolute change is compared.
    """

    entries = {(item["producer"], item["element"], item["quantity"]): item for item in floor}
    if not entries:
        return
    observations = {(item["frame"], item["feature_id"]): item for item in tracks}
    elements = _elements(sequence)
    for event in events:
        if event["type"] != EventType.PROPERTY_CHANGE:
            continue
        before = observations.get((event["frame_before"], event["source_ids"][0]))
        after = observations.get((event["frame_after"], event["target_ids"][0]))
        if before is None or after is None or not (_is_atom(before) and _is_atom(after)):
            continue
        evidence = event["evidence"]
        flagged = {
            "charge": "charge_delta" in evidence,
            "volume": "volume_relative_change" in evidence,
            "spin": "spin" in evidence.get("descriptor_changes", {}),
        }
        producer = after["attributes"].get("descriptor_producer")
        element = elements[after["frame"], after["neighbor_atom_indices"][0]]
        changes: dict[str, Any] = {}
        for quantity in NOISE_QUANTITIES:
            entry = entries.get((producer, element, quantity))
            left, right = _value(before, quantity), _value(after, quantity)
            if not flagged[quantity] or entry is None or left is None or right is None:
                continue
            delta = right - left
            changes[quantity] = {
                "delta": delta,
                # A zero percentile (a closed-shell spin floor) has no finite ratio.
                "ratio_to_floor": {
                    name: abs(delta) / entry[name] if entry[name] > 0 else None
                    for name, _ in _PERCENTILES
                },
            }
        if changes:
            event["noise_floor"] = {"element": element, "producer": producer, "changes": changes}
