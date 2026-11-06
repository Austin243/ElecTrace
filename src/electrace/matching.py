"""Assignment and deformation-aware correspondence for ElecTrace.

The matcher first finds a globally optimal one-to-one backbone with optional
unmatched penalties.  It then promotes well-supported local groups to
one-to-many or many-to-one hyperedges.  This deliberately separates the
combinatorial assignment problem from scientific split/merge evidence:
proximity alone can suggest a candidate, while volume, charge, voxel-shape,
and neighboring-atom conservation determine its confidence.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

import numpy as np
import numpy.typing as npt

from .geometry import (
    deformation_aware_distance,
    deformation_aware_distance_lower_bound,
    minimum_image_displacement,
    periodic_voxel_overlap,
)
from .models import Atom, Feature, FeatureKind, Frame, Identifier, MatchEdge, MatchResult

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
# A candidate split or merge member: the feature, its structural cost, and its pair evidence.
GroupMember = tuple[Feature, float, dict[str, Any]]


@dataclass(slots=True)
class AtomMatchConfig:
    """Controls atom assignment between two adjacent frames.

    ``identity="index"`` pairs atoms by their order in each frame, as in NEB
    images that share one atom order, instead of solving the assignment.
    """

    max_distance: float = 2.0
    distance_scale: float = 0.5
    unmatched_cost: float = 3.0
    identity: str = "geometric"


@dataclass(slots=True)
class FeatureMatchConfig:
    """Weights, gates, and split/merge criteria for electronic features.

    Costs are dimensionless weighted means.  Missing optional descriptors are
    omitted rather than imputed, and the available component weights are
    renormalized for each candidate pair.  That permissive missing-data rule
    does not override declared semantics: incompatible descriptor families or
    charge definitions are forbidden rather than compared numerically.
    """

    max_distance: float = 2.0
    distance_scale: float = 0.75
    max_cost: float = 3.0
    unmatched_cost: float = 1.6
    distance_weight: float = 1.0
    charge_weight: float = 0.45
    charge_scale: float = 0.25
    volume_weight: float = 0.45
    volume_log_scale: float = 0.4
    neighbor_weight: float = 0.8
    voxel_weight: float = 0.9
    descriptor_weight: float = 0.35
    descriptor_scales: dict[str, float] = field(default_factory=dict)
    detect_splits_merges: bool = True
    split_merge_distance: float = 2.5
    split_merge_max_cost: float = 2.25
    conservation_tolerance: float = 0.35
    max_group_size: int = 6
    min_split_merge_confidence: float = 0.15
    pbc: tuple[bool, bool, bool] = (True, True, True)


def _hungarian_fallback(cost_matrix: npt.ArrayLike) -> tuple[IntArray, IntArray]:
    """O(n^2 m) Hungarian solver, for n <= m, used when SciPy is unavailable.

    The implementation is the shortest-augmenting-path form of the Hungarian
    algorithm.  It is deterministic and supports infinite forbidden edges by
    replacing them with a provably dominant finite sentinel.  Each column scan is
    vectorized with the scalar algorithm's element-wise arithmetic and its choice of
    the first minimal column, so the assignment does not depend on the vectorization.
    """

    matrix = np.asarray(cost_matrix, dtype=float)
    finite = np.isfinite(matrix)
    # Any complete assignment using this sentinel is worse than every
    # assignment composed only of finite entries.
    sentinel = max(1.0, float(matrix[finite].max())) * (max(matrix.shape) + 1) * 1.0e6
    cost = np.where(finite, matrix, sentinel)
    rows, columns = cost.shape

    potential_rows = np.zeros(rows + 1, dtype=float)
    potential_columns = np.zeros(columns + 1, dtype=float)
    column_match = np.zeros(columns + 1, dtype=np.int64)
    predecessor = np.zeros(columns + 1, dtype=np.int64)

    for row in range(1, rows + 1):
        column_match[0] = row
        minimum = np.full(columns + 1, np.inf, dtype=float)
        used = np.zeros(columns + 1, dtype=bool)
        column0 = 0
        while column_match[column0] != 0:
            used[column0] = True
            row0 = int(column_match[column0])
            # Unused columns in increasing order; column 0 is always used.
            free = np.flatnonzero(~used)
            reduced = cost[row0 - 1, free - 1] - potential_rows[row0] - potential_columns[free]
            improved = reduced < minimum[free]
            minimum[free[improved]] = reduced[improved]
            predecessor[free[improved]] = column0
            nearest = int(np.argmin(minimum[free]))
            delta = minimum[free[nearest]]
            column1 = int(free[nearest])
            # Used columns hold distinct rows: matched ones plus the row being inserted.
            potential_rows[column_match[used]] += delta
            potential_columns[used] -= delta
            minimum[~used] -= delta
            column0 = column1
        while column0 != 0:
            column1 = int(predecessor[column0])
            column_match[column0] = column_match[column1]
            column0 = column1

    assigned = np.flatnonzero(column_match[1:])
    row_array = column_match[1:][assigned] - 1
    order = np.argsort(row_array, kind="stable")
    return row_array[order], assigned[order].astype(np.int64)


def _assign_with_unmatched(
    pair_cost: FloatArray, unmatched_cost: float
) -> tuple[list[tuple[int, int]], set[int], set[int], float]:
    rows, columns = pair_cost.shape
    if rows == 0 or columns == 0:
        return [], set(range(rows)), set(range(columns)), (rows + columns) * unmatched_cost
    # Each source and target has its own dummy node.  The bottom-right block
    # lets unused dummies pair at zero cost.
    size = rows + columns
    augmented = np.full((size, size), np.inf, dtype=float)
    augmented[:rows, :columns] = pair_cost
    for row in range(rows):
        augmented[row, columns + row] = unmatched_cost
    for column in range(columns):
        augmented[rows + column, column] = unmatched_cost
    augmented[rows:, columns:] = 0.0
    if np.isnan(augmented).any():
        raise ValueError("cost matrix cannot contain NaN or negative infinity")
    try:
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        assigned_rows, assigned_columns = _hungarian_fallback(augmented)
    else:
        assigned_rows, assigned_columns = linear_sum_assignment(augmented)
    matched = [
        (int(row), int(column))
        for row, column in zip(assigned_rows, assigned_columns, strict=True)
        if row < rows and column < columns and math.isfinite(pair_cost[row, column])
    ]
    unmatched_rows = set(range(rows)) - {row for row, _ in matched}
    unmatched_columns = set(range(columns)) - {column for _, column in matched}
    total = sum(float(pair_cost[row, column]) for row, column in matched)
    total += unmatched_cost * (len(unmatched_rows) + len(unmatched_columns))
    return matched, unmatched_rows, unmatched_columns, float(total)


def _weighted_cost(components: Mapping[str, tuple[float, float]]) -> float:
    active = [(value, weight) for value, weight in components.values() if weight > 0]
    if not active:
        return math.inf
    denominator = sum(weight for _, weight in active)
    return float(sum(value * weight for value, weight in active) / denominator)


def match_atoms(
    previous: Frame,
    current: Frame,
    config: AtomMatchConfig | None = None,
) -> MatchResult:
    """Globally match atoms using element identity and periodic motion."""

    settings = config or AtomMatchConfig()
    if settings.identity == "index":
        return _match_atoms_by_index(previous, current)
    # Integer codes compare exactly as the casefolded symbols do.
    codes: dict[str, int] = {}
    before = [codes.setdefault(atom.element.casefold(), len(codes)) for atom in previous.atoms]
    after = [codes.setdefault(atom.element.casefold(), len(codes)) for atom in current.atoms]
    allowed = np.equal.outer(before, after)
    distances = _candidate_distances(
        previous.atoms,
        current.atoms,
        allowed,
        previous.cell,
        current.cell,
        (True, True, True),
        settings.max_distance,
    )
    costs = np.full((len(previous.atoms), len(current.atoms)), np.inf, dtype=float)
    # Every other pair has an infinite cost, so only these can match or need evidence.
    evidence: dict[tuple[int, int], dict[str, Any]] = {}
    rows, columns = np.nonzero(distances <= settings.max_distance)
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        distance = float(distances[row, column])
        cost = distance / settings.distance_scale
        costs[row, column] = cost
        evidence[row, column] = {
            "distance": distance,
            "element_match": True,
            "attribute_deltas": {},
            "components": {"distance": cost},
        }
    matched, unmatched_rows, unmatched_columns, total = _assign_with_unmatched(
        costs, settings.unmatched_cost
    )
    edges = tuple(
        MatchEdge(
            source_ids=(previous.atoms[row].index,),
            target_ids=(current.atoms[column].index,),
            cost=float(costs[row, column]),
            confidence=math.exp(-float(costs[row, column])),
            evidence=evidence[row, column],
        )
        for row, column in sorted(matched)
    )
    return MatchResult(
        previous_frame_index=previous.index,
        current_frame_index=current.index,
        edges=edges,
        unmatched_previous=tuple(previous.atoms[row].index for row in sorted(unmatched_rows)),
        unmatched_current=tuple(
            current.atoms[column].index for column in sorted(unmatched_columns)
        ),
        total_cost=total,
        diagnostics={
            "entity": "atom",
            "candidate_pairs": int(np.isfinite(costs).sum()),
            "cell_volume_ratio": _cell_volume_ratio(previous, current),
        },
    )


def _match_atoms_by_index(previous: Frame, current: Frame) -> MatchResult:
    """Pair the i-th atoms of both frames; displacement is evidence, not cost.

    The caller asserts a fixed atom order.  Only the element sequence can be
    checked, so a permutation of same-element atoms is not detected.
    """

    if len(previous.atoms) != len(current.atoms):
        raise ValueError(
            "index atom identity requires identical element sequences, but frames "
            f"{previous.label!r} and {current.label!r} have {len(previous.atoms)} and "
            f"{len(current.atoms)} atoms; use geometric atom identity when atom order "
            "or content changes"
        )
    for position, (source, target) in enumerate(zip(previous.atoms, current.atoms, strict=True)):
        if source.element.casefold() != target.element.casefold():
            raise ValueError(
                "index atom identity requires identical element sequences, but atom "
                f"{position} is {source.element} in frame {previous.label!r} and "
                f"{target.element} in frame {current.label!r}; use geometric atom "
                "identity when atom order or content changes"
            )
    distances = np.asarray(
        deformation_aware_distance(
            [atom.fractional_position for atom in previous.atoms],
            [atom.fractional_position for atom in current.atoms],
            previous.cell,
            current.cell,
        ),
        dtype=float,
    )
    edges = tuple(
        MatchEdge(
            source_ids=(source.index,),
            target_ids=(target.index,),
            cost=0.0,
            confidence=1.0,
            evidence={"identity": "index", "distance": float(distance), "element_match": True},
        )
        for source, target, distance in zip(previous.atoms, current.atoms, distances, strict=True)
    )
    return MatchResult(
        previous_frame_index=previous.index,
        current_frame_index=current.index,
        edges=edges,
        total_cost=0.0,
        diagnostics={
            "entity": "atom",
            "identity": "index",
            "cell_volume_ratio": _cell_volume_ratio(previous, current),
        },
    )


def _cell_volume_ratio(previous: Frame, current: Frame) -> float:
    first = abs(float(np.linalg.det(np.asarray(previous.cell, dtype=float))))
    second = abs(float(np.linalg.det(np.asarray(current.cell, dtype=float))))
    return second / first


def _candidate_distances(
    previous: Sequence[Atom | Feature],
    current: Sequence[Atom | Feature],
    allowed: npt.NDArray[np.bool_],
    previous_cell: npt.ArrayLike,
    current_cell: npt.ArrayLike,
    pbc: tuple[bool, bool, bool],
    gate: float,
) -> FloatArray:
    """Evaluate deformation-aware geometry for allowed pairs that may lie within *gate*.

    A vectorized lower bound first discards pairs that are certainly farther apart
    than the gate; they, like disallowed pairs, keep an infinite distance.  Every other
    allowed pair gets its exact distance, which may still exceed the gate.
    """

    distances = np.full((len(previous), len(current)), np.inf, dtype=float)
    if not allowed.any():
        return distances
    previous_positions = np.asarray(
        [entity.fractional_position for entity in previous], dtype=float
    )
    current_positions = np.asarray([entity.fractional_position for entity in current], dtype=float)
    bound = deformation_aware_distance_lower_bound(
        previous_positions, current_positions, previous_cell, current_cell, pbc
    )
    # The relative margin absorbs rounding in the bound; the exact distance decides.
    rows, columns = np.nonzero(allowed & (bound <= gate * (1.0 + 1.0e-9)))
    if rows.size == 0:
        return distances
    distances[rows, columns] = deformation_aware_distance(
        previous_positions[rows],
        current_positions[columns],
        previous_cell,
        current_cell,
        pbc,
    )
    return distances


def _as_finite_float(value: Any) -> float | None:
    if not isinstance(value, (int, float, np.integer, np.floating)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _neighbor_similarity(
    source: Feature,
    target: Feature,
    previous: Frame,
    current: Frame,
    mapping: Mapping[int, int],
) -> float | None:
    left = set(source.neighbor_atom_indices)
    right = set(target.neighbor_atom_indices)
    if not left and not right:
        return None
    if mapping and all(index in mapping for index in left):
        mapped = {mapping[index] for index in left}
        return float(len(mapped & right) / len(mapped | right))
    before = Counter(previous.atom_by_index(index).element.casefold() for index in left)
    after = Counter(current.atom_by_index(index).element.casefold() for index in right)
    return float((before & after).total() / (before | after).total())


_DESCRIPTOR_FAMILY_ALIASES = {
    # LOBSTER exposes both spellings for an integrated COHP.  They are one
    # scientific quantity, not disjoint evidence families.
    "icohp_ev": "icohp",
}


def _semantic_token(value: Any) -> str | None:
    """Normalize a declared semantic identifier without guessing synonyms."""

    if not isinstance(value, str):
        return None
    return "_".join(value.casefold().replace("-", "_").split()) or None


def _declared_descriptor_families(feature: Feature) -> frozenset[str]:
    """Return normalized ``+``-joined descriptor-family declarations."""

    value = feature.attributes.get("descriptor_family")
    if not isinstance(value, str):
        return frozenset()
    tokens = (_semantic_token(part) for part in value.split("+"))
    return frozenset(
        _DESCRIPTOR_FAMILY_ALIASES.get(token, token) for token in tokens if token is not None
    )


def _semantically_compatible(source: Feature, target: Feature) -> bool:
    """Reject pairs with explicitly contradictory descriptor semantics."""

    def contradict(key: str) -> bool:
        left = _semantic_token(source.attributes.get(key))
        right = _semantic_token(target.attributes.get(key))
        return left is not None and right is not None and left != right

    source_declared = _declared_descriptor_families(source)
    target_declared = _declared_descriptor_families(target)
    if source_declared and target_declared and source_declared.isdisjoint(target_declared):
        return False
    if contradict("charge_definition") or contradict("descriptor_producer"):
        return False
    both_charged = source.charge is not None and target.charge is not None
    return not (both_charged and contradict("charge_convention"))


def _semantic_signature(feature: Feature) -> tuple[Any, ...]:
    """Feature kind plus everything :func:`_semantically_compatible` reads."""

    return (
        feature.kind,
        _declared_descriptor_families(feature),
        _semantic_token(feature.attributes.get("charge_definition")),
        feature.charge is not None,
        _semantic_token(feature.attributes.get("charge_convention")),
        _semantic_token(feature.attributes.get("descriptor_producer")),
    )


def _compatible_pairs(
    previous: Sequence[Feature], current: Sequence[Feature]
) -> tuple[npt.NDArray[np.bool_], int]:
    """Return the kind- and semantics-compatible pairs and the semantic rejection count.

    Compatibility depends only on the features' signatures, so it is decided once per
    pair of signature groups.  Rejections count kind-compatible pairs with
    contradictory semantics, at any distance.
    """

    def groups(features: Sequence[Feature]) -> list[list[int]]:
        result: dict[tuple[Any, ...], list[int]] = {}
        for index, feature in enumerate(features):
            result.setdefault(_semantic_signature(feature), []).append(index)
        return list(result.values())

    allowed = np.zeros((len(previous), len(current)), dtype=bool)
    rejections = 0
    current_groups = groups(current)
    for rows in groups(previous):
        for columns in current_groups:
            source, target = previous[rows[0]], current[columns[0]]
            if source.kind != target.kind:
                continue
            if _semantically_compatible(source, target):
                allowed[np.ix_(rows, columns)] = True
            else:
                rejections += len(rows) * len(columns)
    return allowed, rejections


def _grid_shape(feature: Feature) -> tuple[int, int, int] | None:
    value = feature.attributes.get("grid_shape")
    if value is None:
        return None
    shape = tuple(int(component) for component in islice(value, 4))
    return shape if len(shape) == 3 and all(component > 0 for component in shape) else None


def _voxel_similarity(source: Feature, target: Feature) -> float | None:
    if source.voxel_indices is None or target.voxel_indices is None:
        return None
    shape_a = _grid_shape(source)
    shape_b = _grid_shape(target)
    if shape_a is None or shape_a != shape_b:
        return None
    # Align centroids to compare shape support; absolute translation is already
    # represented by the position-distance component.
    fractional_shift = np.asarray(source.fractional_position) - np.asarray(
        target.fractional_position
    )
    fractional_shift -= np.rint(fractional_shift)
    shift = tuple(
        int(np.rint(delta * size)) for delta, size in zip(fractional_shift, shape_a, strict=True)
    )
    return periodic_voxel_overlap(
        source.voxel_indices, target.voxel_indices, shape_a, shift_b=shift
    )


def _shared_neighbor_motion(
    source: Feature,
    target: Feature,
    previous: Frame,
    current: Frame,
    atom_mapping: Mapping[int, int],
    config: FeatureMatchConfig,
) -> list[dict[str, Any]]:
    """Bond-critical-point motion relative to each shared, already-matched neighbor.

    Supplemental geometry only: it does not modify cost or confidence.  Each anchor
    is a shared, already-matched neighbor; all of them are reported.
    """

    motion = []
    for old_id in source.neighbor_atom_indices:
        new_id = atom_mapping.get(old_id)
        if new_id not in target.neighbor_atom_indices:
            continue
        old_atom, new_atom = previous.atom_by_index(old_id), current.atom_by_index(new_id)
        old_offset = np.asarray(source.fractional_position) - old_atom.fractional_position
        new_offset = np.asarray(target.fractional_position) - new_atom.fractional_position
        relative = deformation_aware_distance(
            old_offset, new_offset, previous.cell, current.cell, config.pbc
        )
        before = minimum_image_displacement(
            old_atom.fractional_position, source.fractional_position, previous.cell, config.pbc
        )
        after = minimum_image_displacement(
            new_atom.fractional_position, target.fractional_position, current.cell, config.pbc
        )
        motion.append(
            {
                "source_atom_index": old_id,
                "target_atom_index": new_id,
                "element": old_atom.element,
                "relative_displacement_angstrom": relative,
                "distance_before_angstrom": float(np.linalg.norm(before)),
                "distance_after_angstrom": float(np.linalg.norm(after)),
            }
        )
    return motion


def _feature_pair(
    source: Feature,
    target: Feature,
    previous: Frame,
    current: Frame,
    atom_mapping: Mapping[int, int],
    config: FeatureMatchConfig,
    *,
    distance: float,
    motion_evidence: bool = True,
) -> tuple[float, dict[str, Any]]:
    """Score a compatible pair inside its distance gate, both already checked by the caller.

    ``motion_evidence=False`` skips supplemental geometry.  Matching scans score many
    pairs that are never reported, so they skip the bond-critical-point motion evidence
    and :func:`_direct_feature_edge` adds it to the edges that are.
    """

    components: dict[str, tuple[float, float]] = {
        "distance": (distance / config.distance_scale, config.distance_weight)
    }
    evidence: dict[str, Any] = {
        "distance": distance,
        "kind_match": True,
        "semantic_compatibility": {"compatible": True, "rejection_reasons": []},
    }
    if source.charge is not None and target.charge is not None:
        charge_delta = abs(source.charge - target.charge)
        components["charge"] = (charge_delta / config.charge_scale, config.charge_weight)
        evidence["charge_delta"] = charge_delta
    if source.volume is not None and target.volume is not None:
        epsilon = 1.0e-12
        log_ratio = abs(math.log((target.volume + epsilon) / (source.volume + epsilon)))
        components["volume"] = (log_ratio / config.volume_log_scale, config.volume_weight)
        evidence["volume_log_ratio"] = log_ratio

    neighbor_similarity = _neighbor_similarity(source, target, previous, current, atom_mapping)
    if neighbor_similarity is not None:
        components["neighbors"] = (1.0 - neighbor_similarity, config.neighbor_weight)
        evidence["neighbor_similarity"] = neighbor_similarity

    if motion_evidence and source.kind == target.kind == FeatureKind.BOND_CRITICAL_POINT:
        evidence["shared_neighbor_motion"] = _shared_neighbor_motion(
            source, target, previous, current, atom_mapping, config
        )

    voxel_similarity = _voxel_similarity(source, target)
    if voxel_similarity is not None:
        components["voxels"] = (1.0 - voxel_similarity, config.voxel_weight)
        evidence["voxel_similarity"] = voxel_similarity

    descriptor_deltas: dict[str, float] = {}
    for key, scale in config.descriptor_scales.items():
        left_value = _as_finite_float(source.attributes.get(key))
        right_value = _as_finite_float(target.attributes.get(key))
        if left_value is not None and right_value is not None:
            descriptor_deltas[key] = abs(left_value - right_value) / scale
    if descriptor_deltas:
        components["descriptors"] = (
            float(np.mean(list(descriptor_deltas.values()))),
            config.descriptor_weight,
        )
        evidence["descriptor_normalized_deltas"] = descriptor_deltas

    cost = _weighted_cost(components)
    evidence["components"] = {key: value for key, (value, _) in components.items()}
    return cost, evidence


def _quality_factor(features: Sequence[Feature]) -> tuple[float, dict[str, Any]]:
    factor = 1.0
    reasons: list[str] = []
    for feature in features:
        if feature.attributes.get("grid_converged") is False:
            factor *= 0.7
            reasons.append(f"{feature.id}: grid not converged")
        uncertainty_value = _as_finite_float(feature.attributes.get("uncertainty"))
        if uncertainty_value is not None:
            factor *= max(0.0, 1.0 - min(1.0, uncertainty_value))
            reasons.append(f"{feature.id}: uncertainty={uncertainty_value:g}")
    return max(0.0, min(1.0, factor)), {"quality_notes": reasons}


def _conservation_assessment(
    sources: Sequence[Feature],
    targets: Sequence[Feature],
    tolerance: float,
) -> tuple[float | None, dict[str, Any], str | None]:
    participants = (*sources, *targets)
    channels = ("charge", "volume")
    complete = {
        channel: all(getattr(feature, channel) is not None for feature in participants)
        for channel in channels
    }
    errors: dict[str, float] = {}
    # Signed basin charges can cross zero.  Include a small absolute scale
    # so tiny charges do not create an unbounded relative error.
    for channel, scale in (("volume", 1.0e-12), ("charge", 0.1)):
        if complete[channel]:
            left = sum(getattr(feature, channel) for feature in sources)
            right = sum(getattr(feature, channel) for feature in targets)
            errors[channel] = abs(left - right) / max(abs(left), abs(right), scale)
    charge_conflicts: dict[str, list[str]] = {}
    for key in ("charge_definition", "charge_convention", "descriptor_producer"):
        tokens = {_semantic_token(feature.attributes.get(key)) for feature in participants}
        values = sorted(filter(None, tokens))
        if len(values) > 1:
            charge_conflicts[key] = values
    incompatible = ["charge"] if complete["charge"] and charge_conflicts else []
    if incompatible:
        del errors["charge"]
    failed = [channel for channel in channels if errors.get(channel, 0.0) > tolerance]
    reason: str | None = None
    if not errors:
        reason = (
            "incompatible_conservation_semantics"
            if incompatible
            else "missing_complete_conservation_channel"
        )
    elif failed:
        reason = "conservation_tolerance_exceeded"
    # A failed channel cannot be averaged away by a better channel.  The
    # aggregate is therefore the worst complete channel, while the full
    # evidence vector remains available for auditing.
    error = max(errors.values()) if errors else None
    evidence: dict[str, Any] = {
        **{f"{channel}_relative_error": value for channel, value in errors.items()},
        "available_channels": [channel for channel in channels if channel in errors],
        "missing_channels": [channel for channel in channels if not complete[channel]],
        "incompatible_channels": incompatible,
        "failed_channels": failed,
        "tolerance": tolerance,
    }
    if charge_conflicts:
        evidence["charge_semantic_conflicts"] = charge_conflicts
    return error, evidence, reason


def _hyperedge_member_cost(evidence: Mapping[str, Any], config: FeatureMatchConfig) -> float:
    """Score a split/merge member without penalizing extensive partitioning.

    A child is not expected to retain its parent's charge or volume by itself;
    those quantities are tested on the full hyperedge by
    :func:`_conservation_assessment`.
    Reusing the one-to-one cost here would systematically assign low
    confidence to the very splits and merges the model is intended to detect.
    """

    weights = {
        "distance": config.distance_weight,
        "neighbors": config.neighbor_weight,
        "voxels": config.voxel_weight,
        "descriptors": config.descriptor_weight,
    }
    components: dict[str, tuple[float, float]] = {}
    for key, weight in weights.items():
        numeric = _as_finite_float(evidence["components"].get(key))
        if numeric is not None:
            components[key] = (numeric, weight)
    return _weighted_cost(components)


def _best_group(
    source: Feature,
    target: Feature,
    extras: Sequence[GroupMember],
    *,
    split: bool,
    config: FeatureMatchConfig,
) -> tuple[list[GroupMember], dict[str, Any], dict[str, Any] | None]:
    """Select the best structurally plausible, conservation-backed subset."""

    limited = sorted(extras, key=lambda item: (item[1], str(item[0].id)))[
        : config.max_group_size - 1
    ]
    best: list[GroupMember] = []
    best_evidence: dict[str, Any] = {}
    best_objective = math.inf
    best_rejection: dict[str, Any] | None = None
    # Test only cumulative nearest-neighbor groups. This keeps the common
    # conserved split/merge case without exponential subset enumeration.
    for size in range(1, len(limited) + 1):
        subset = limited[:size]
        members = tuple(item[0] for item in subset)
        sources = (source,) if split else (source, *members)
        targets = (target, *members) if split else (target,)
        error, evidence, rejection_reason = _conservation_assessment(
            sources, targets, config.conservation_tolerance
        )
        mean_cost = float(np.mean([item[1] for item in subset]))
        objective = mean_cost + 2.0 * (error or 0.0)
        if rejection_reason is None and objective < best_objective:
            best_objective = objective
            best = list(subset)
            best_evidence = evidence
        elif best_rejection is None:
            best_rejection = {
                "relation": "split" if split else "merge",
                "source_ids": [feature.id for feature in sources],
                "target_ids": [feature.id for feature in targets],
                "reason": rejection_reason,
                "conservation": evidence,
            }
    return best, best_evidence, None if best else best_rejection


def _group_candidates(
    unmatched: set[int],
    backbone: set[int],
    distances: FloatArray,
    previous: Frame,
    current: Frame,
    atom_mapping: Mapping[int, int],
    config: FeatureMatchConfig,
    *,
    split: bool,
) -> dict[int, list[GroupMember]]:
    """Assign every unmatched feature to its best compatible direct backbone.

    For a split, *unmatched* holds current-frame columns and *backbone* the matched
    previous-frame rows; for a merge, the reverse.  Pairs beyond the split/merge gate
    cannot qualify, so they are skipped unscored.
    """

    features = current.features if split else previous.features
    candidates: dict[int, list[GroupMember]] = {}
    for index in unmatched:
        choices: list[tuple[float, int, dict[str, Any]]] = []
        for member in backbone:
            row, column = (member, index) if split else (index, member)
            if distances[row, column] > config.split_merge_distance:
                continue
            cost, evidence = _feature_pair(
                previous.features[row],
                current.features[column],
                previous,
                current,
                atom_mapping,
                config,
                distance=float(distances[row, column]),
                motion_evidence=False,
            )
            structural_cost = _hyperedge_member_cost(evidence, config)
            if math.isfinite(cost) and structural_cost <= config.split_merge_max_cost:
                evidence["one_to_one_cost"] = cost
                choices.append((structural_cost, member, evidence))
        if choices:
            structural_cost, member, evidence = min(choices, key=lambda item: (item[0], item[1]))
            candidates.setdefault(member, []).append((features[index], structural_cost, evidence))
    return candidates


def _hyperedge(
    source: Feature,
    target: Feature,
    selected_split: Sequence[GroupMember],
    selected_merge: Sequence[GroupMember],
    split_evidence: dict[str, Any],
    merge_evidence: dict[str, Any],
    direct_cost: float,
    direct_evidence: Mapping[str, Any],
    config: FeatureMatchConfig,
) -> tuple[MatchEdge | None, dict[str, Any] | None]:
    """Join a direct pair with its selected members: the edge, or why it was rejected."""

    source_members = [source, *(item[0] for item in selected_merge)]
    target_members = [target, *(item[0] for item in selected_split)]
    members = (*selected_split, *selected_merge)
    direct_structural = _hyperedge_member_cost(direct_evidence, config)
    member_costs = [
        direct_structural if math.isfinite(direct_structural) else direct_cost,
        *(item[1] for item in members),
    ]
    conservation_error, conservation_evidence, conservation_rejection = _conservation_assessment(
        source_members, target_members, config.conservation_tolerance
    )
    conservation_factor = (
        math.exp(-2.0 * conservation_error) if conservation_error is not None else 0.0
    )
    quality, quality_evidence = _quality_factor((*source_members, *target_members))
    # A Gaussian-like decay is appropriate for a jointly conserved group;
    # it is less punitive than multiplying independent child likelihoods.
    confidence = math.exp(-0.5 * float(np.mean(member_costs))) * conservation_factor * quality
    if conservation_rejection is None and confidence >= config.min_split_merge_confidence:
        edge = MatchEdge(
            source_ids=tuple(feature.id for feature in source_members),
            target_ids=tuple(feature.id for feature in target_members),
            cost=float(np.mean(member_costs)),
            confidence=min(1.0, max(0.0, confidence)),
            evidence={
                "member_costs": member_costs,
                "member_one_to_one_costs": [
                    direct_cost,
                    *(item[2]["one_to_one_cost"] for item in members),
                ],
                "conservation": conservation_evidence,
                "split_conservation": split_evidence,
                "merge_conservation": merge_evidence,
                **quality_evidence,
            },
        )
        return edge, None
    if selected_split and selected_merge:
        relation = "many_to_many"
    elif selected_split:
        relation = "split"
    else:
        relation = "merge"
    rejection: dict[str, Any] = {
        "relation": relation,
        "source_ids": [feature.id for feature in source_members],
        "target_ids": [feature.id for feature in target_members],
        "reason": conservation_rejection or "confidence_below_threshold",
        "conservation": conservation_evidence,
    }
    if conservation_rejection is None:
        rejection["confidence"] = confidence
        rejection["minimum_confidence"] = config.min_split_merge_confidence
    return None, rejection


def _augment_hyperedges(
    direct: list[tuple[int, int]],
    unmatched_rows: set[int],
    unmatched_columns: set[int],
    costs: FloatArray,
    distances: FloatArray,
    pair_evidence: Mapping[tuple[int, int], dict[str, Any]],
    previous: Frame,
    current: Frame,
    atom_mapping: Mapping[int, int],
    config: FeatureMatchConfig,
) -> tuple[list[MatchEdge], set[int], set[int], list[dict[str, Any]]]:
    split_candidates: dict[int, list[GroupMember]] = {}
    merge_candidates: dict[int, list[GroupMember]] = {}
    if config.detect_splits_merges:
        split_candidates = _group_candidates(
            unmatched_columns,
            {row for row, _ in direct},
            distances,
            previous,
            current,
            atom_mapping,
            config,
            split=True,
        )
        merge_candidates = _group_candidates(
            unmatched_rows,
            {column for _, column in direct},
            distances,
            previous,
            current,
            atom_mapping,
            config,
            split=False,
        )

    edges: list[MatchEdge] = []
    rejections: list[dict[str, Any]] = []
    consumed_sources: set[Identifier] = set()
    consumed_targets: set[Identifier] = set()
    for row, column in sorted(direct):
        source = previous.features[row]
        target = current.features[column]
        selected_split, split_evidence, split_rejection = _best_group(
            source, target, split_candidates.get(row, []), split=True, config=config
        )
        selected_merge, merge_evidence, merge_rejection = _best_group(
            source, target, merge_candidates.get(column, []), split=False, config=config
        )
        rejections.extend(filter(None, (split_rejection, merge_rejection)))

        edge: MatchEdge | None = None
        rejection: dict[str, Any] | None = None
        if selected_split or selected_merge:
            edge, rejection = _hyperedge(
                source,
                target,
                selected_split,
                selected_merge,
                split_evidence,
                merge_evidence,
                float(costs[row, column]),
                pair_evidence[row, column],
                config,
            )
        if edge is None:
            if rejection is not None:
                rejections.append(rejection)
            edge = _direct_feature_edge(
                row, column, costs, pair_evidence, previous, current, atom_mapping, config
            )
        else:
            consumed_sources.update(edge.source_ids)
            consumed_targets.update(edge.target_ids)
        edges.append(edge)
    rejections.sort(
        key=lambda item: (
            str(item["relation"]),
            tuple(str(value) for value in item["source_ids"]),
            tuple(str(value) for value in item["target_ids"]),
            str(item["reason"]),
        )
    )
    return (
        edges,
        {row for row in unmatched_rows if previous.features[row].id not in consumed_sources},
        {
            column
            for column in unmatched_columns
            if current.features[column].id not in consumed_targets
        },
        rejections,
    )


def _direct_feature_edge(
    row: int,
    column: int,
    costs: FloatArray,
    pair_evidence: Mapping[tuple[int, int], dict[str, Any]],
    previous: Frame,
    current: Frame,
    atom_mapping: Mapping[int, int],
    config: FeatureMatchConfig,
) -> MatchEdge:
    source = previous.features[row]
    target = current.features[column]
    quality, quality_evidence = _quality_factor((source, target))
    evidence = dict(pair_evidence[row, column])
    if source.kind == target.kind == FeatureKind.BOND_CRITICAL_POINT:
        evidence["shared_neighbor_motion"] = _shared_neighbor_motion(
            source, target, previous, current, atom_mapping, config
        )
    evidence.update(quality_evidence)
    return MatchEdge(
        source_ids=(source.id,),
        target_ids=(target.id,),
        cost=float(costs[row, column]),
        confidence=math.exp(-float(costs[row, column])) * quality,
        evidence=evidence,
    )


def match_features(
    previous: Frame,
    current: Frame,
    atom_matches: MatchResult | None = None,
    config: FeatureMatchConfig | None = None,
) -> MatchResult:
    """Match electronic features, including supported split/merge hyperedges."""

    settings = config or FeatureMatchConfig()
    mapping = (
        {int(source): int(target) for source, target in atom_matches.mapping.items()}
        if atom_matches is not None
        else {}
    )
    allowed, semantic_rejections = _compatible_pairs(previous.features, current.features)
    distances = _candidate_distances(
        previous.features,
        current.features,
        allowed,
        previous.cell,
        current.cell,
        settings.pbc,
        # Split/merge candidates reuse these distances with their own gate.
        max(settings.max_distance, settings.split_merge_distance),
    )
    costs = np.full((len(previous.features), len(current.features)), np.inf, dtype=float)
    # Every other pair has an infinite cost, so only these can match or need evidence.
    pair_evidence: dict[tuple[int, int], dict[str, Any]] = {}
    rows, columns = np.nonzero(distances <= settings.max_distance)
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        cost, evidence = _feature_pair(
            previous.features[row],
            current.features[column],
            previous,
            current,
            mapping,
            settings,
            distance=float(distances[row, column]),
            motion_evidence=False,
        )
        if cost > settings.max_cost:
            continue
        costs[row, column] = cost
        pair_evidence[row, column] = evidence

    direct, unmatched_rows, unmatched_columns, _ = _assign_with_unmatched(
        costs, settings.unmatched_cost
    )
    edges, unmatched_rows, unmatched_columns, hyperedge_rejections = _augment_hyperedges(
        direct,
        unmatched_rows,
        unmatched_columns,
        costs,
        distances,
        pair_evidence,
        previous,
        current,
        mapping,
        settings,
    )
    total = sum(edge.cost for edge in edges) + settings.unmatched_cost * (
        len(unmatched_rows) + len(unmatched_columns)
    )
    return MatchResult(
        previous_frame_index=previous.index,
        current_frame_index=current.index,
        edges=tuple(edges),
        unmatched_previous=tuple(previous.features[row].id for row in sorted(unmatched_rows)),
        unmatched_current=tuple(
            current.features[column].id for column in sorted(unmatched_columns)
        ),
        total_cost=float(total),
        diagnostics={
            "entity": "feature",
            "candidate_pairs": int(np.isfinite(costs).sum()),
            "semantic_rejections": semantic_rejections,
            "one_to_one_backbone": len(direct),
            "hyperedges": sum(
                len(edge.source_ids) != 1 or len(edge.target_ids) != 1 for edge in edges
            ),
            "hyperedge_rejections": hyperedge_rejections,
            "atom_mapping_size": len(mapping),
            "cell_volume_ratio": _cell_volume_ratio(previous, current),
        },
    )
