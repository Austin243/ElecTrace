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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations, islice
from typing import Any

import numpy as np
import numpy.typing as npt

from .geometry import (
    as_pbc,
    deformation_aware_distance,
    periodic_voxel_overlap,
)
from .models import Atom, Feature, Frame, MatchEdge, MatchRelation, MatchResult

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


@dataclass(slots=True)
class AtomMatchConfig:
    """Controls atom assignment between two adjacent frames."""

    max_distance: float = 2.0
    distance_scale: float = 0.5
    unmatched_cost: float = 3.0
    allow_element_change: bool = False
    element_mismatch_cost: float = 4.0
    attribute_scales: dict[str, float] = field(default_factory=dict)
    attribute_weight: float = 0.25
    ambiguity_tolerance: float = 0.05
    pbc: tuple[bool, bool, bool] = (True, True, True)

    def __post_init__(self) -> None:
        self.max_distance = _positive(self.max_distance, "max_distance")
        self.distance_scale = _positive(self.distance_scale, "distance_scale")
        self.unmatched_cost = _positive(self.unmatched_cost, "unmatched_cost")
        self.element_mismatch_cost = _nonnegative(
            self.element_mismatch_cost, "element_mismatch_cost"
        )
        self.attribute_weight = _nonnegative(self.attribute_weight, "attribute_weight")
        self.ambiguity_tolerance = _nonnegative(self.ambiguity_tolerance, "ambiguity_tolerance")
        self.attribute_scales = {
            str(key): _positive(value, f"attribute scale {key!r}")
            for key, value in self.attribute_scales.items()
        }
        self.pbc = as_pbc(self.pbc)


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
    allow_kind_mismatch: bool = False
    equivalent_kinds: tuple[tuple[str, str], ...] = ()
    kind_mismatch_cost: float = 3.0
    detect_splits_merges: bool = True
    split_merge_distance: float = 2.5
    split_merge_max_cost: float = 2.25
    conservation_tolerance: float = 0.35
    max_group_size: int = 6
    min_split_merge_confidence: float = 0.15
    ambiguity_tolerance: float = 0.05
    pbc: tuple[bool, bool, bool] = (True, True, True)

    def __post_init__(self) -> None:
        for name in (
            "max_distance",
            "distance_scale",
            "max_cost",
            "unmatched_cost",
            "charge_scale",
            "volume_log_scale",
            "split_merge_distance",
            "split_merge_max_cost",
        ):
            setattr(self, name, _positive(getattr(self, name), name))
        for name in (
            "distance_weight",
            "charge_weight",
            "volume_weight",
            "neighbor_weight",
            "voxel_weight",
            "descriptor_weight",
            "kind_mismatch_cost",
            "conservation_tolerance",
            "min_split_merge_confidence",
            "ambiguity_tolerance",
        ):
            setattr(self, name, _nonnegative(getattr(self, name), name))
        if self.min_split_merge_confidence > 1.0:
            raise ValueError("min_split_merge_confidence must lie in [0, 1]")
        if self.conservation_tolerance > 1.0:
            raise ValueError("conservation_tolerance must lie in [0, 1]")
        self.max_group_size = int(self.max_group_size)
        if self.max_group_size < 2:
            raise ValueError("max_group_size must be at least two")
        self.descriptor_scales = {
            str(key): _positive(value, f"descriptor scale {key!r}")
            for key, value in self.descriptor_scales.items()
        }
        self.equivalent_kinds = tuple(
            (str(left).lower(), str(right).lower()) for left, right in self.equivalent_kinds
        )
        self.pbc = as_pbc(self.pbc)


def _positive(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _nonnegative(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _finite_cost_matrix(cost_matrix: npt.ArrayLike) -> tuple[FloatArray, float]:
    matrix = np.asarray(cost_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("cost matrix must be two-dimensional")
    if np.any(np.isnan(matrix)) or np.any(matrix == -np.inf):
        raise ValueError("cost matrix cannot contain NaN or negative infinity")
    if np.any(matrix < 0):
        raise ValueError("assignment costs must be non-negative")
    finite = matrix[np.isfinite(matrix)]
    baseline = float(finite.max()) if finite.size else 1.0
    # Any complete assignment using this sentinel is worse than every
    # assignment composed only of finite entries.
    sentinel = max(1.0, baseline) * (max(matrix.shape, default=0) + 1) * 1.0e6
    return np.where(np.isfinite(matrix), matrix, sentinel), sentinel


def _hungarian_fallback(cost_matrix: npt.ArrayLike) -> tuple[IntArray, IntArray]:
    """Rectangular O(n^2 m) Hungarian solver used when SciPy is unavailable.

    The implementation is the shortest-augmenting-path form of the Hungarian
    algorithm.  It is deterministic and supports infinite forbidden edges by
    replacing them with a provably dominant finite sentinel.
    """

    cost, sentinel = _finite_cost_matrix(cost_matrix)
    original = np.asarray(cost_matrix, dtype=float)
    rows, columns = cost.shape
    if rows == 0 or columns == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    transposed = rows > columns
    if transposed:
        cost = cost.T
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
        while True:
            used[column0] = True
            row0 = int(column_match[column0])
            delta = np.inf
            column1 = 0
            for column in range(1, columns + 1):
                if used[column]:
                    continue
                reduced = (
                    cost[row0 - 1, column - 1] - potential_rows[row0] - potential_columns[column]
                )
                if reduced < minimum[column]:
                    minimum[column] = reduced
                    predecessor[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            if not math.isfinite(float(delta)):
                raise ValueError("assignment problem is infeasible")
            for column in range(columns + 1):
                if used[column]:
                    potential_rows[column_match[column]] += delta
                    potential_columns[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if column_match[column0] == 0:
                break
        while True:
            column1 = int(predecessor[column0])
            column_match[column0] = column_match[column1]
            column0 = column1
            if column0 == 0:
                break

    row_indices: list[int] = []
    column_indices: list[int] = []
    for column in range(1, columns + 1):
        if column_match[column]:
            row_indices.append(int(column_match[column] - 1))
            column_indices.append(column - 1)
    row_array = np.asarray(row_indices, dtype=np.int64)
    column_array = np.asarray(column_indices, dtype=np.int64)
    if transposed:
        row_array, column_array = column_array, row_array
    order = np.argsort(row_array, kind="stable")
    row_array = row_array[order]
    column_array = column_array[order]
    if any(
        original[row, column] == np.inf for row, column in zip(row_array, column_array, strict=True)
    ):
        # Match scipy.optimize.linear_sum_assignment's infeasibility behavior.
        raise ValueError(f"assignment problem is infeasible (forbidden cost {sentinel:g} selected)")
    return row_array, column_array


def solve_assignment(cost_matrix: npt.ArrayLike) -> tuple[IntArray, IntArray]:
    """Solve a rectangular linear assignment, preferring SciPy when present."""

    matrix = np.asarray(cost_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("cost matrix must be two-dimensional")
    # Keep validation and the documented non-negative-cost contract identical
    # across the SciPy and bundled implementations.
    _finite_cost_matrix(matrix)
    if matrix.shape[0] == 0 or matrix.shape[1] == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    try:
        from scipy.optimize import linear_sum_assignment

        rows, columns = linear_sum_assignment(matrix)
        return rows.astype(np.int64, copy=False), columns.astype(np.int64, copy=False)
    except ImportError:
        return _hungarian_fallback(matrix)


# A familiar name for callers working directly with optimization routines.
linear_assignment = solve_assignment


def _assign_with_unmatched(
    pair_cost: FloatArray, unmatched_cost: float
) -> tuple[list[tuple[int, int]], set[int], set[int], float]:
    rows, columns = pair_cost.shape
    if rows == 0:
        return [], set(), set(range(columns)), columns * unmatched_cost
    if columns == 0:
        return [], set(range(rows)), set(), rows * unmatched_cost
    # Each source and target has its own dummy node.  The bottom-right block
    # lets unused dummies pair at zero cost.
    augmented = _augmented_unmatched_cost(pair_cost, unmatched_cost)
    assigned_rows, assigned_columns = solve_assignment(augmented)
    matched = [
        (int(row), int(column))
        for row, column in zip(assigned_rows, assigned_columns, strict=True)
        if row < rows and column < columns and math.isfinite(pair_cost[row, column])
    ]
    matched_rows = {row for row, _ in matched}
    matched_columns = {column for _, column in matched}
    unmatched_rows = set(range(rows)) - matched_rows
    unmatched_columns = set(range(columns)) - matched_columns
    total = sum(float(pair_cost[row, column]) for row, column in matched)
    total += unmatched_cost * (len(unmatched_rows) + len(unmatched_columns))
    return matched, unmatched_rows, unmatched_columns, float(total)


def _augmented_unmatched_cost(pair_cost: FloatArray, unmatched_cost: float) -> FloatArray:
    rows, columns = pair_cost.shape
    size = rows + columns
    augmented = np.full((size, size), np.inf, dtype=float)
    augmented[:rows, :columns] = pair_cost
    for row in range(rows):
        augmented[row, columns + row] = unmatched_cost
    for column in range(columns):
        augmented[rows + column, column] = unmatched_cost
    augmented[rows:, columns:] = 0.0
    return augmented


def _assignment_snapshot(
    assignment: set[tuple[int, int]],
    pair_cost: FloatArray,
    source_ids: Sequence[Any],
    target_ids: Sequence[Any],
) -> dict[str, Any]:
    rows, columns = pair_cost.shape
    matches = sorted(
        (
            {
                "source_id": source_ids[row],
                "target_id": target_ids[column],
            }
            for row, column in assignment
            if row < rows and column < columns and math.isfinite(pair_cost[row, column])
        ),
        key=lambda item: (str(item["source_id"]), str(item["target_id"])),
    )
    matched_sources = {
        row
        for row, column in assignment
        if row < rows and column < columns and math.isfinite(pair_cost[row, column])
    }
    matched_targets = {
        column
        for row, column in assignment
        if row < rows and column < columns and math.isfinite(pair_cost[row, column])
    }
    return {
        "matches": matches,
        "unmatched_previous": [
            source_ids[row]
            for row in sorted(
                set(range(rows)) - matched_sources, key=lambda item: str(source_ids[item])
            )
        ],
        "unmatched_current": [
            target_ids[column]
            for column in sorted(
                set(range(columns)) - matched_targets,
                key=lambda item: str(target_ids[item]),
            )
        ],
    }


def _assignment_ambiguities(
    pair_cost: FloatArray,
    unmatched_cost: float,
    matched: Sequence[tuple[int, int]],
    unmatched_rows: set[int],
    unmatched_columns: set[int],
    optimal_total: float,
    source_ids: Sequence[Any],
    target_ids: Sequence[Any],
    tolerance: float,
) -> tuple[list[dict[str, Any]], dict[tuple[int, int], dict[str, Any]]]:
    """Find selected decisions with a complete near-optimal alternative assignment.

    Cheap local gaps screen the common, clearly separated case.  Every reported
    ambiguity is then verified by resolving the complete assignment with one
    selected scientific decision forbidden.  This avoids treating a local tie
    as ambiguous when the rest of the global assignment resolves it.
    """

    rows, columns = pair_cost.shape
    if rows == 0 or columns == 0:
        return [], {}
    augmented = _augmented_unmatched_cost(pair_cost, unmatched_cost)
    decisions: dict[tuple[int, int], tuple[str, int | None, int | None]] = {}
    candidates: set[tuple[int, int]] = set()
    for row, column in matched:
        coordinate = (row, column)
        decisions[coordinate] = ("match", row, column)
        local_gap = _ambiguity_gap(pair_cost, row, column)
        unmatched_gap = 2.0 * unmatched_cost - float(pair_cost[row, column])
        if min(local_gap, unmatched_gap) <= tolerance:
            candidates.add(coordinate)
    for row in unmatched_rows:
        coordinate = (row, columns + row)
        decisions[coordinate] = ("unmatched_previous", row, None)
        if np.isfinite(pair_cost[row]).any():
            candidates.add(coordinate)
    for column in unmatched_columns:
        coordinate = (rows + column, column)
        decisions[coordinate] = ("unmatched_current", None, column)
        if np.isfinite(pair_cost[:, column]).any():
            candidates.add(coordinate)

    records: dict[tuple[int, int], dict[str, Any]] = {}
    unresolved = set(candidates)
    while unresolved:
        selected_coordinate = min(unresolved)
        unresolved.remove(selected_coordinate)
        alternative_cost = augmented.copy()
        alternative_cost[selected_coordinate] = math.inf
        try:
            alternative_rows, alternative_columns = solve_assignment(alternative_cost)
        except ValueError:
            continue
        alternative_assignment = {
            (int(row), int(column))
            for row, column in zip(
                alternative_rows,
                alternative_columns,
                strict=True,
            )
        }
        chosen_costs = [augmented[row, column] for row, column in alternative_assignment]
        if any(not math.isfinite(float(value)) for value in chosen_costs):
            continue
        alternative_total = float(sum(float(value) for value in chosen_costs))
        gap = max(0.0, alternative_total - optimal_total)
        if gap > tolerance + 1.0e-12:
            continue
        snapshot = _assignment_snapshot(
            alternative_assignment,
            pair_cost,
            source_ids,
            target_ids,
        )
        changed = set(decisions) - alternative_assignment
        affected_previous_set: set[Any] = set()
        affected_current_set: set[Any] = set()
        for changed_coordinate in changed:
            _, affected_row, affected_column = decisions[changed_coordinate]
            if affected_row is not None:
                affected_previous_set.add(source_ids[affected_row])
            if affected_column is not None:
                affected_current_set.add(target_ids[affected_column])
        affected_previous = sorted(affected_previous_set, key=str)
        affected_current = sorted(affected_current_set, key=str)
        for coordinate in sorted(changed):
            decision, decision_row, decision_column = decisions[coordinate]
            selected: dict[str, Any] = {}
            if decision_row is not None:
                selected["source_id"] = source_ids[decision_row]
            if decision_column is not None:
                selected["target_id"] = target_ids[decision_column]
            record = {
                "decision": decision,
                "selected": selected,
                "reason": "alternative_assignment_within_tolerance",
                "assignment_cost_gap": gap,
                "selected_assignment_cost": optimal_total,
                "alternative_assignment_cost": alternative_total,
                "tolerance": tolerance,
                "affected_previous": affected_previous,
                "affected_current": affected_current,
                "alternative": snapshot,
            }
            previous_record = records.get(coordinate)
            if previous_record is None or gap < float(previous_record["assignment_cost_gap"]):
                records[coordinate] = record
            unresolved.discard(coordinate)

    ordered = [
        records[coordinate]
        for coordinate in sorted(
            records,
            key=lambda coordinate: (
                decisions[coordinate][0],
                str(records[coordinate]["selected"].get("source_id", "")),
                str(records[coordinate]["selected"].get("target_id", "")),
            ),
        )
    ]
    by_match = {
        (row, column): records[coordinate]
        for coordinate, (decision, row, column) in decisions.items()
        if decision == "match" and row is not None and column is not None and coordinate in records
    }
    return ordered, by_match


def _weighted_cost(components: Mapping[str, tuple[float, float]]) -> float:
    active = [(value, weight) for value, weight in components.values() if weight > 0]
    if not active:
        return math.inf
    denominator = sum(weight for _, weight in active)
    return float(sum(value * weight for value, weight in active) / denominator)


def _pair_confidence(cost: float, ambiguity_gap: float = math.inf) -> float:
    base = math.exp(-max(0.0, cost))
    if math.isfinite(ambiguity_gap):
        # A tie retains half the base confidence rather than becoming exactly
        # zero; a clearly separated optimum approaches full base confidence.
        base *= 0.5 + 0.5 * (1.0 - math.exp(-max(0.0, ambiguity_gap)))
    return float(min(1.0, max(0.0, base)))


def _ambiguity_gap(cost: FloatArray, row: int, column: int) -> float:
    alternatives = [
        *(
            float(value)
            for index, value in enumerate(cost[row])
            if index != column and math.isfinite(float(value))
        ),
        *(
            float(value)
            for index, value in enumerate(cost[:, column])
            if index != row and math.isfinite(float(value))
        ),
    ]
    if not alternatives:
        return math.inf
    return max(0.0, min(alternatives) - float(cost[row, column]))


def _atom_pair(
    previous: Atom,
    current: Atom,
    distance: float,
    config: AtomMatchConfig,
) -> tuple[float, dict[str, Any]]:
    element_match = previous.element.casefold() == current.element.casefold()
    if not element_match and not config.allow_element_change:
        return math.inf, {"element_match": False}
    if distance > config.max_distance:
        return math.inf, {"distance": distance, "element_match": element_match}
    components: dict[str, tuple[float, float]] = {
        "distance": (distance / config.distance_scale, 1.0)
    }
    attribute_deltas: dict[str, float] = {}
    for key, scale in config.attribute_scales.items():
        left = previous.attributes.get(key)
        right = current.attributes.get(key)
        left_value = _as_finite_float(left)
        right_value = _as_finite_float(right)
        if left_value is not None and right_value is not None:
            delta = abs(left_value - right_value) / scale
            attribute_deltas[key] = delta
    if attribute_deltas:
        components["attributes"] = (
            float(np.mean(list(attribute_deltas.values()))),
            config.attribute_weight,
        )
    if not element_match:
        components["element_mismatch"] = (config.element_mismatch_cost, 1.0)
    cost = _weighted_cost(components)
    return cost, {
        "distance": distance,
        "element_match": element_match,
        "attribute_deltas": attribute_deltas,
        "components": {key: value for key, (value, _) in components.items()},
    }


def match_atoms(
    previous: Frame,
    current: Frame,
    config: AtomMatchConfig | None = None,
) -> MatchResult:
    """Globally match atoms using element identity and periodic motion."""

    settings = config or AtomMatchConfig()
    distances = _candidate_distances(
        previous.atoms,
        current.atoms,
        (
            (row, column)
            for row, source in enumerate(previous.atoms)
            for column, target in enumerate(current.atoms)
            if settings.allow_element_change
            or source.element.casefold() == target.element.casefold()
        ),
        previous.cell,
        current.cell,
        settings.pbc,
    )
    costs = np.full((len(previous.atoms), len(current.atoms)), np.inf, dtype=float)
    evidence: dict[tuple[int, int], dict[str, Any]] = {}
    for row, source in enumerate(previous.atoms):
        for column, target in enumerate(current.atoms):
            cost, pair_evidence = _atom_pair(
                source,
                target,
                float(distances[row, column]),
                settings,
            )
            costs[row, column] = cost
            evidence[row, column] = pair_evidence
    matched, unmatched_rows, unmatched_columns, total = _assign_with_unmatched(
        costs, settings.unmatched_cost
    )
    ambiguities, ambiguous_pairs = _assignment_ambiguities(
        costs,
        settings.unmatched_cost,
        matched,
        unmatched_rows,
        unmatched_columns,
        total,
        [atom.index for atom in previous.atoms],
        [atom.index for atom in current.atoms],
        settings.ambiguity_tolerance,
    )
    edges: list[MatchEdge] = []
    for row, column in sorted(matched):
        pair_evidence = dict(evidence[row, column])
        ambiguity = ambiguous_pairs.get((row, column))
        if ambiguity is not None:
            pair_evidence["assignment_ambiguity"] = ambiguity
        edges.append(
            MatchEdge(
                source_ids=(previous.atoms[row].index,),
                target_ids=(current.atoms[column].index,),
                cost=float(costs[row, column]),
                confidence=_pair_confidence(
                    float(costs[row, column]), _ambiguity_gap(costs, row, column)
                ),
                evidence=pair_evidence,
            )
        )
    return MatchResult(
        previous_frame_index=previous.index,
        current_frame_index=current.index,
        edges=tuple(edges),
        unmatched_previous=tuple(previous.atoms[row].index for row in sorted(unmatched_rows)),
        unmatched_current=tuple(
            current.atoms[column].index for column in sorted(unmatched_columns)
        ),
        total_cost=total,
        diagnostics={
            "entity": "atom",
            "candidate_pairs": int(np.isfinite(costs).sum()),
            "ambiguous": bool(ambiguities),
            "ambiguity_tolerance": settings.ambiguity_tolerance,
            "ambiguities": ambiguities,
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
    pairs: Iterable[tuple[int, int]],
    previous_cell: npt.ArrayLike,
    current_cell: npt.ArrayLike,
    pbc: tuple[bool, bool, bool],
) -> FloatArray:
    """Evaluate deformation-aware geometry once for all semantically viable pairs."""

    distances = np.full((len(previous), len(current)), np.inf, dtype=float)
    flat_indices = np.fromiter(
        (index for pair in pairs for index in pair),
        dtype=np.intp,
    )
    if flat_indices.size == 0:
        return distances
    pair_indices = flat_indices.reshape(-1, 2)
    rows = pair_indices[:, 0]
    columns = pair_indices[:, 1]
    previous_positions = np.asarray(
        [entity.fractional_position for entity in previous], dtype=float
    ).reshape(len(previous), 3)
    current_positions = np.asarray(
        [entity.fractional_position for entity in current], dtype=float
    ).reshape(len(current), 3)
    source_positions = previous_positions[rows]
    target_positions = current_positions[columns]
    values = np.asarray(
        deformation_aware_distance(
            source_positions,
            target_positions,
            previous_cell,
            current_cell,
            pbc,
        ),
        dtype=float,
    )
    distances[rows, columns] = values
    return distances


def _as_finite_float(value: Any) -> float | None:
    if not isinstance(value, (int, float, np.integer, np.floating)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _atom_mapping(
    atom_mapping: MatchResult | Mapping[int, int] | None,
) -> dict[int, int]:
    if atom_mapping is None:
        return {}
    if isinstance(atom_mapping, MatchResult):
        return {int(source): int(target) for source, target in atom_mapping.mapping.items()}
    return {int(source): int(target) for source, target in atom_mapping.items()}


def _counter_jaccard(left: Counter[str], right: Counter[str]) -> float:
    keys = set(left) | set(right)
    union = sum(max(left[key], right[key]) for key in keys)
    if union == 0:
        return 1.0
    intersection = sum(min(left[key], right[key]) for key in keys)
    return float(intersection / union)


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
        union = mapped | right
        return float(len(mapped & right) / len(union)) if union else 1.0
    previous_elements = Counter(previous.atom_by_index(index).element.casefold() for index in left)
    current_elements = Counter(current.atom_by_index(index).element.casefold() for index in right)
    return _counter_jaccard(previous_elements, current_elements)


def _compatible_kind(source: Feature, target: Feature, config: FeatureMatchConfig) -> bool:
    if source.kind == target.kind:
        return True
    pair = (source.kind, target.kind)
    reverse = (target.kind, source.kind)
    return (
        config.allow_kind_mismatch
        or pair in config.equivalent_kinds
        or reverse in config.equivalent_kinds
    )


_DESCRIPTOR_FAMILY_ALIASES = {
    # LOBSTER exposes both spellings for an integrated COHP.  They are one
    # scientific quantity, not disjoint evidence families.
    "icohp_ev": "icohp",
}


def _semantic_token(value: Any) -> str | None:
    """Normalize a declared semantic identifier without guessing synonyms."""

    if not isinstance(value, str):
        return None
    token = value.strip().casefold().replace("-", "_")
    token = "_".join(token.split())
    return token or None


def _descriptor_family_token(value: Any) -> str | None:
    token = _semantic_token(value)
    if token is None:
        return None
    return _DESCRIPTOR_FAMILY_ALIASES.get(token, token)


def _declared_descriptor_families(feature: Feature) -> frozenset[str]:
    """Return normalized ``+``-joined descriptor-family declarations."""

    value = feature.attributes.get("descriptor_family")
    if not isinstance(value, str):
        return frozenset()
    return frozenset(
        token for part in value.split("+") if (token := _descriptor_family_token(part)) is not None
    )


def _configured_descriptor_families(feature: Feature, config: FeatureMatchConfig) -> frozenset[str]:
    """Infer evidence families only from configured, finite numeric values.

    This is a defensive fallback for canonical/plugin features predating the
    explicit ``descriptor_family`` tag.  A key absent on one side remains
    optional; only two non-empty, disjoint sets are incompatible.
    """

    families: set[str] = set()
    for key in config.descriptor_scales:
        if _as_finite_float(feature.attributes.get(key)) is None:
            continue
        family = _descriptor_family_token(key)
        if family is not None:
            families.add(family)
    return frozenset(families)


def _semantic_compatibility(
    source: Feature, target: Feature, config: FeatureMatchConfig
) -> tuple[bool, dict[str, Any]]:
    """Conservatively gate pairs with explicitly contradictory semantics.

    Producer identity is also conservative by default because cross-tool
    feature identity has not been calibrated.  Conversely, an absent producer,
    family, or charge-definition declaration is treated as genuinely missing
    optional evidence and does not reject a candidate.
    """

    source_declared = _declared_descriptor_families(source)
    target_declared = _declared_descriptor_families(target)
    source_configured = _configured_descriptor_families(source, config)
    target_configured = _configured_descriptor_families(target, config)
    source_charge = _semantic_token(source.attributes.get("charge_definition"))
    target_charge = _semantic_token(target.attributes.get("charge_definition"))
    source_convention = _semantic_token(source.attributes.get("charge_convention"))
    target_convention = _semantic_token(target.attributes.get("charge_convention"))
    source_producer = _semantic_token(source.attributes.get("descriptor_producer"))
    target_producer = _semantic_token(target.attributes.get("descriptor_producer"))
    reasons: list[str] = []
    if source_declared and target_declared and source_declared.isdisjoint(target_declared):
        reasons.append("incompatible_descriptor_families")
    if source_charge is not None and target_charge is not None and source_charge != target_charge:
        reasons.append("incompatible_charge_definitions")
    if (
        source.charge is not None
        and target.charge is not None
        and source_convention is not None
        and target_convention is not None
        and source_convention != target_convention
    ):
        reasons.append("incompatible_charge_conventions")
    if (
        source_producer is not None
        and target_producer is not None
        and source_producer != target_producer
    ):
        reasons.append("incompatible_descriptor_producers")
    if source_configured and target_configured and source_configured.isdisjoint(target_configured):
        reasons.append("disjoint_configured_descriptor_families")

    evidence: dict[str, Any] = {
        "compatible": not reasons,
        "rejection_reasons": reasons,
        "source_descriptor_families": sorted(source_declared),
        "target_descriptor_families": sorted(target_declared),
        "source_configured_descriptor_families": sorted(source_configured),
        "target_configured_descriptor_families": sorted(target_configured),
        "source_charge_definition": source_charge,
        "target_charge_definition": target_charge,
        "source_charge_convention": source_convention,
        "target_charge_convention": target_convention,
        "source_descriptor_producer": source_producer,
        "target_descriptor_producer": target_producer,
    }
    return not reasons, evidence


def _grid_shape(feature: Feature, frame: Frame) -> tuple[int, int, int] | None:
    value = feature.attributes.get("grid_shape", frame.metadata.get("grid_shape"))
    if value is None:
        return None
    shape = tuple(int(component) for component in islice(value, 4))
    return shape if len(shape) == 3 and all(component > 0 for component in shape) else None


def _voxel_similarity(
    source: Feature, target: Feature, previous: Frame, current: Frame
) -> float | None:
    if source.voxel_indices is None or target.voxel_indices is None:
        return None
    shape_a = _grid_shape(source, previous)
    shape_b = _grid_shape(target, current)
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
        source.voxel_indices,
        target.voxel_indices,
        shape_a,
        shift_b=shift,
        metric="dice",
    )


def _feature_pair(
    source: Feature,
    target: Feature,
    previous: Frame,
    current: Frame,
    atom_mapping: Mapping[int, int],
    config: FeatureMatchConfig,
    *,
    distance: float,
    distance_gate: float | None = None,
) -> tuple[float, dict[str, Any]]:
    kind_match = source.kind == target.kind
    if not _compatible_kind(source, target, config):
        return math.inf, {"kind_match": False}
    semantic_match, semantic_evidence = _semantic_compatibility(source, target, config)
    if not semantic_match:
        return math.inf, {
            "kind_match": kind_match,
            "semantic_compatibility": semantic_evidence,
        }
    if distance > (config.max_distance if distance_gate is None else distance_gate):
        return math.inf, {"distance": distance, "kind_match": kind_match}

    components: dict[str, tuple[float, float]] = {
        "distance": (distance / config.distance_scale, config.distance_weight)
    }
    evidence: dict[str, Any] = {
        "distance": distance,
        "kind_match": kind_match,
        "semantic_compatibility": semantic_evidence,
    }
    if not kind_match:
        components["kind_mismatch"] = (config.kind_mismatch_cost, 1.0)
    if source.charge is not None and target.charge is not None:
        charge_delta = abs(source.charge - target.charge)
        components["charge"] = (charge_delta / config.charge_scale, config.charge_weight)
        evidence["charge_delta"] = charge_delta
    if source.volume is not None and target.volume is not None:
        epsilon = 1.0e-12
        log_ratio = abs(math.log((target.volume + epsilon) / (source.volume + epsilon)))
        components["volume"] = (
            log_ratio / config.volume_log_scale,
            config.volume_weight,
        )
        evidence["volume_log_ratio"] = log_ratio

    neighbor_similarity = _neighbor_similarity(source, target, previous, current, atom_mapping)
    if neighbor_similarity is not None:
        components["neighbors"] = (1.0 - neighbor_similarity, config.neighbor_weight)
        evidence["neighbor_similarity"] = neighbor_similarity

    voxel_similarity = _voxel_similarity(source, target, previous, current)
    if voxel_similarity is not None:
        components["voxels"] = (1.0 - voxel_similarity, config.voxel_weight)
        evidence["voxel_similarity"] = voxel_similarity

    descriptor_deltas: dict[str, float] = {}
    for key, scale in config.descriptor_scales.items():
        left = source.attributes.get(key)
        right = target.attributes.get(key)
        left_value = _as_finite_float(left)
        right_value = _as_finite_float(right)
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
        uncertainty = feature.attributes.get("uncertainty")
        uncertainty_value = _as_finite_float(uncertainty)
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
    channels = {
        "charge": "charge_relative_error",
        "volume": "volume_relative_error",
    }
    complete = {
        "charge": all(feature.charge is not None for feature in participants),
        "volume": all(feature.volume is not None for feature in participants),
    }
    errors: dict[str, float] = {}
    if complete["volume"]:
        left = sum(float(feature.volume) for feature in sources if feature.volume is not None)
        right = sum(float(feature.volume) for feature in targets if feature.volume is not None)
        errors["volume_relative_error"] = abs(left - right) / max(abs(left), abs(right), 1.0e-12)
    if complete["charge"]:
        left = sum(float(feature.charge) for feature in sources if feature.charge is not None)
        right = sum(float(feature.charge) for feature in targets if feature.charge is not None)
        # Signed basin charges can cross zero.  Include a small absolute scale
        # so tiny charges do not create an unbounded relative error.
        errors["charge_relative_error"] = abs(left - right) / max(abs(left), abs(right), 0.1)
    charge_semantics = {
        key: sorted(
            {
                token
                for feature in participants
                if (token := _semantic_token(feature.attributes.get(key))) is not None
            }
        )
        for key in ("charge_definition", "charge_convention", "descriptor_producer")
    }
    charge_conflicts = {key: values for key, values in charge_semantics.items() if len(values) > 1}
    incompatible = ("charge",) if complete["charge"] and charge_conflicts else ()
    if incompatible:
        errors.pop("charge_relative_error", None)
    available = tuple(
        channel
        for channel, key in channels.items()
        if complete[channel] and channel not in incompatible and key in errors
    )
    missing = tuple(channel for channel in channels if not complete[channel])
    failed = tuple(channel for channel, key in channels.items() if errors.get(key, 0.0) > tolerance)
    reason: str | None = None
    if not available:
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
        **errors,
        "available_channels": list(available),
        "missing_channels": list(missing),
        "incompatible_channels": list(incompatible),
        "failed_channels": list(failed),
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

    raw = evidence.get("components", {})
    if not isinstance(raw, Mapping):
        return math.inf
    weights = {
        "distance": config.distance_weight,
        "neighbors": config.neighbor_weight,
        "voxels": config.voxel_weight,
        "descriptors": config.descriptor_weight,
        "kind_mismatch": 1.0,
    }
    components: dict[str, tuple[float, float]] = {}
    for key, weight in weights.items():
        value = raw.get(key)
        numeric = _as_finite_float(value)
        if numeric is not None:
            components[key] = (numeric, weight)
    return _weighted_cost(components)


def _best_group(
    fixed: Sequence[Feature],
    base: Sequence[Feature],
    extras: Sequence[tuple[Feature, float, dict[str, Any]]],
    *,
    split: bool,
    config: FeatureMatchConfig,
) -> tuple[
    list[tuple[Feature, float, dict[str, Any]]],
    dict[str, Any],
    dict[str, Any] | None,
]:
    """Select the best structurally plausible, conservation-backed subset."""

    limited = sorted(extras, key=lambda item: (item[1], str(item[0].id)))[
        : config.max_group_size - 1
    ]
    best: list[tuple[Feature, float, dict[str, Any]]] = []
    best_evidence: dict[str, Any] = {}
    best_objective = math.inf
    best_rejection: dict[str, Any] | None = None
    best_rejection_objective = math.inf
    # Exhaustive subset selection is cheap at max_group_size <= 6 and avoids
    # greedy failures when two children together conserve volume.
    for size in range(1, len(limited) + 1):
        for subset in combinations(limited, size):
            if split:
                sources = tuple(fixed)
                targets = (*base, *(item[0] for item in subset))
            else:
                sources = (*base, *(item[0] for item in subset))
                targets = tuple(fixed)
            error, evidence, rejection_reason = _conservation_assessment(
                sources,
                targets,
                config.conservation_tolerance,
            )
            mean_pair_cost = float(np.mean([item[1] for item in subset]))
            # Conservation dominates when available; a modest complexity term
            # avoids swallowing unrelated nearby births/deaths.
            objective = mean_pair_cost + 0.08 * len(subset)
            if error is not None:
                objective += 2.0 * error
            if rejection_reason is not None:
                if objective < best_rejection_objective:
                    best_rejection_objective = objective
                    best_rejection = {
                        "relation": "split" if split else "merge",
                        "source_ids": [feature.id for feature in sources],
                        "target_ids": [feature.id for feature in targets],
                        "reason": rejection_reason,
                        "candidate_member_cost": mean_pair_cost,
                        "conservation": evidence,
                    }
                continue
            if objective < best_objective:
                best_objective = objective
                best = list(subset)
                best_evidence = evidence
    return best, best_evidence, best_rejection


def _augment_hyperedges(
    direct: list[tuple[int, int]],
    unmatched_rows: set[int],
    unmatched_columns: set[int],
    costs: FloatArray,
    distances: FloatArray,
    pair_evidence: Mapping[tuple[int, int], dict[str, Any]],
    ambiguous_pairs: Mapping[tuple[int, int], dict[str, Any]],
    previous: Frame,
    current: Frame,
    atom_mapping: Mapping[int, int],
    config: FeatureMatchConfig,
) -> tuple[list[MatchEdge], set[int], set[int], list[dict[str, Any]]]:
    if not config.detect_splits_merges or not direct:
        direct_edges = [
            _direct_feature_edge(
                row,
                column,
                costs,
                pair_evidence,
                previous,
                current,
                assignment_ambiguity=ambiguous_pairs.get((row, column)),
            )
            for row, column in direct
        ]
        return direct_edges, unmatched_rows, unmatched_columns, []

    matched_rows = {row for row, _ in direct}
    matched_columns = {column for _, column in direct}
    # Assign every unmatched entity to its best compatible direct backbone.
    split_candidates: dict[int, list[tuple[int, float, dict[str, Any]]]] = {}
    for column in unmatched_columns:
        choices: list[tuple[float, int, dict[str, Any]]] = []
        for row in matched_rows:
            cost, evidence = _feature_pair(
                previous.features[row],
                current.features[column],
                previous,
                current,
                atom_mapping,
                config,
                distance=float(distances[row, column]),
                distance_gate=config.split_merge_distance,
            )
            structural_cost = _hyperedge_member_cost(evidence, config)
            if math.isfinite(cost) and structural_cost <= config.split_merge_max_cost:
                evidence["one_to_one_cost"] = cost
                choices.append((structural_cost, row, evidence))
        if choices:
            cost, row, evidence = min(choices, key=lambda item: (item[0], item[1]))
            split_candidates.setdefault(row, []).append((column, cost, evidence))

    merge_candidates: dict[int, list[tuple[int, float, dict[str, Any]]]] = {}
    for row in unmatched_rows:
        choices = []
        for column in matched_columns:
            cost, evidence = _feature_pair(
                previous.features[row],
                current.features[column],
                previous,
                current,
                atom_mapping,
                config,
                distance=float(distances[row, column]),
                distance_gate=config.split_merge_distance,
            )
            structural_cost = _hyperedge_member_cost(evidence, config)
            if math.isfinite(cost) and structural_cost <= config.split_merge_max_cost:
                evidence["one_to_one_cost"] = cost
                choices.append((structural_cost, column, evidence))
        if choices:
            cost, column, evidence = min(choices, key=lambda item: (item[0], item[1]))
            merge_candidates.setdefault(column, []).append((row, cost, evidence))

    edges: list[MatchEdge] = []
    rejections: list[dict[str, Any]] = []
    consumed_rows: set[int] = set()
    consumed_columns: set[int] = set()
    for row, column in sorted(direct):
        source = previous.features[row]
        target = current.features[column]
        assignment_ambiguity = ambiguous_pairs.get((row, column))
        split_options = [
            (current.features[index], cost, evidence)
            for index, cost, evidence in split_candidates.get(row, [])
        ]
        merge_options = [
            (previous.features[index], cost, evidence)
            for index, cost, evidence in merge_candidates.get(column, [])
        ]
        selected_split, split_evidence, split_rejection = (
            _best_group((source,), (target,), split_options, split=True, config=config)
            if split_options
            else ([], {}, None)
        )
        selected_merge, merge_evidence, merge_rejection = (
            _best_group((target,), (source,), merge_options, split=False, config=config)
            if merge_options
            else ([], {}, None)
        )
        if not selected_split and split_rejection is not None:
            rejections.append(split_rejection)
        if not selected_merge and merge_rejection is not None:
            rejections.append(merge_rejection)

        source_members = [source, *(item[0] for item in selected_merge)]
        target_members = [target, *(item[0] for item in selected_split)]
        one_to_one_costs = [
            float(costs[row, column]),
            *(float(item[2].get("one_to_one_cost", item[1])) for item in selected_split),
            *(float(item[2].get("one_to_one_cost", item[1])) for item in selected_merge),
        ]
        member_evidence = [
            pair_evidence[row, column],
            *(item[2] for item in selected_split),
            *(item[2] for item in selected_merge),
        ]
        member_costs = [
            structural if math.isfinite(structural) else assignment
            for structural, assignment in zip(
                (_hyperedge_member_cost(evidence, config) for evidence in member_evidence),
                one_to_one_costs,
                strict=True,
            )
        ]
        conservation_error, conservation_evidence, conservation_rejection = (
            _conservation_assessment(
                source_members,
                target_members,
                config.conservation_tolerance,
            )
        )
        conservation_factor = (
            math.exp(-2.0 * conservation_error) if conservation_error is not None else 0.0
        )
        quality, quality_evidence = _quality_factor((*source_members, *target_members))
        # A Gaussian-like decay is appropriate for a jointly conserved group;
        # it is less punitive than multiplying independent child likelihoods.
        confidence = math.exp(-0.5 * float(np.mean(member_costs))) * conservation_factor * quality
        has_hyperedge_members = bool(selected_split or selected_merge)
        if (
            has_hyperedge_members
            and conservation_rejection is None
            and confidence >= config.min_split_merge_confidence
        ):
            selected_split_ids = {item[0].id for item in selected_split}
            selected_merge_ids = {item[0].id for item in selected_merge}
            consumed_columns.update(
                index
                for index in unmatched_columns
                if current.features[index].id in selected_split_ids
            )
            consumed_rows.update(
                index
                for index in unmatched_rows
                if previous.features[index].id in selected_merge_ids
            )
            evidence = {
                "member_costs": member_costs,
                "member_one_to_one_costs": one_to_one_costs,
                "conservation": conservation_evidence,
                "split_conservation": split_evidence,
                "merge_conservation": merge_evidence,
                **quality_evidence,
            }
            edges.append(
                MatchEdge(
                    source_ids=tuple(feature.id for feature in source_members),
                    target_ids=tuple(feature.id for feature in target_members),
                    cost=float(np.mean(member_costs)),
                    confidence=min(1.0, max(0.0, confidence)),
                    evidence=evidence,
                )
            )
        else:
            if has_hyperedge_members:
                relation = (
                    "many_to_many"
                    if selected_split and selected_merge
                    else "split"
                    if selected_split
                    else "merge"
                )
                rejection: dict[str, Any] = {
                    "relation": relation,
                    "source_ids": [feature.id for feature in source_members],
                    "target_ids": [feature.id for feature in target_members],
                    "reason": conservation_rejection or "confidence_below_threshold",
                    "conservation": conservation_evidence,
                }
                if conservation_rejection is None:
                    rejection.update(
                        {
                            "confidence": confidence,
                            "minimum_confidence": config.min_split_merge_confidence,
                        }
                    )
                rejections.append(rejection)
            edges.append(
                _direct_feature_edge(
                    row,
                    column,
                    costs,
                    pair_evidence,
                    previous,
                    current,
                    assignment_ambiguity=assignment_ambiguity,
                )
            )
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
        unmatched_rows - consumed_rows,
        unmatched_columns - consumed_columns,
        rejections,
    )


def _resolve_hyperedge_ambiguities(
    ambiguities: Sequence[dict[str, Any]],
    edges: Sequence[MatchEdge],
) -> list[dict[str, Any]]:
    """Retain only assignment ties that alter final scientific membership.

    A split or merge may have multiple equivalent one-to-one backbones while
    still producing the same conservation-backed participant set.  Such a tie
    is evidence about an implementation detail, not about the final hyperedge.
    """

    unresolved: list[dict[str, Any]] = []
    for ambiguity in ambiguities:
        affected_previous = {str(value) for value in ambiguity.get("affected_previous", [])}
        affected_current = {str(value) for value in ambiguity.get("affected_current", [])}
        resolving_edge = next(
            (
                edge
                for edge in edges
                if edge.relation is not MatchRelation.ONE_TO_ONE
                and affected_previous
                and affected_current
                and affected_previous.issubset({str(value) for value in edge.source_ids})
                and affected_current.issubset({str(value) for value in edge.target_ids})
            ),
            None,
        )
        if resolving_edge is None:
            unresolved.append(ambiguity)
            continue
        resolved = resolving_edge.evidence.setdefault("resolved_assignment_ambiguities", [])
        if isinstance(resolved, list):
            resolved.append(ambiguity)
    return unresolved


def _direct_feature_edge(
    row: int,
    column: int,
    costs: FloatArray,
    pair_evidence: Mapping[tuple[int, int], dict[str, Any]],
    previous: Frame,
    current: Frame,
    *,
    assignment_ambiguity: Mapping[str, Any] | None = None,
) -> MatchEdge:
    source = previous.features[row]
    target = current.features[column]
    quality, quality_evidence = _quality_factor((source, target))
    evidence = dict(pair_evidence[row, column])
    evidence.update(quality_evidence)
    if assignment_ambiguity is not None:
        evidence["assignment_ambiguity"] = dict(assignment_ambiguity)
    return MatchEdge(
        source_ids=(source.id,),
        target_ids=(target.id,),
        cost=float(costs[row, column]),
        confidence=_pair_confidence(float(costs[row, column]), _ambiguity_gap(costs, row, column))
        * quality,
        evidence=evidence,
    )


def match_features(
    previous: Frame,
    current: Frame,
    atom_mapping: MatchResult | Mapping[int, int] | None = None,
    config: FeatureMatchConfig | None = None,
) -> MatchResult:
    """Match electronic features, including supported split/merge hyperedges."""

    settings = config or FeatureMatchConfig()
    mapping = _atom_mapping(atom_mapping)
    distances = _candidate_distances(
        previous.features,
        current.features,
        (
            (row, column)
            for row, source in enumerate(previous.features)
            for column, target in enumerate(current.features)
            if _compatible_kind(source, target, settings)
            and _semantic_compatibility(source, target, settings)[0]
        ),
        previous.cell,
        current.cell,
        settings.pbc,
    )
    costs = np.full((len(previous.features), len(current.features)), np.inf, dtype=float)
    pair_evidence: dict[tuple[int, int], dict[str, Any]] = {}
    for row, source in enumerate(previous.features):
        for column, target in enumerate(current.features):
            cost, evidence = _feature_pair(
                source,
                target,
                previous,
                current,
                mapping,
                settings,
                distance=float(distances[row, column]),
            )
            if cost > settings.max_cost:
                cost = math.inf
                evidence["rejected_by_max_cost"] = True
            costs[row, column] = cost
            pair_evidence[row, column] = evidence

    direct, unmatched_rows, unmatched_columns, assignment_total = _assign_with_unmatched(
        costs, settings.unmatched_cost
    )
    ambiguities, ambiguous_pairs = _assignment_ambiguities(
        costs,
        settings.unmatched_cost,
        direct,
        unmatched_rows,
        unmatched_columns,
        assignment_total,
        [feature.id for feature in previous.features],
        [feature.id for feature in current.features],
        settings.ambiguity_tolerance,
    )
    edges, unmatched_rows, unmatched_columns, hyperedge_rejections = _augment_hyperedges(
        direct,
        unmatched_rows,
        unmatched_columns,
        costs,
        distances,
        pair_evidence,
        ambiguous_pairs,
        previous,
        current,
        mapping,
        settings,
    )
    ambiguities = _resolve_hyperedge_ambiguities(ambiguities, edges)
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
            "semantic_rejections": sum(
                pair.get("semantic_compatibility", {}).get("compatible") is False
                for pair in pair_evidence.values()
            ),
            "one_to_one_backbone": len(direct),
            "ambiguous": bool(ambiguities),
            "ambiguity_tolerance": settings.ambiguity_tolerance,
            "ambiguities": ambiguities,
            "hyperedges": sum(
                len(edge.source_ids) != 1 or len(edge.target_ids) != 1 for edge in edges
            ),
            "hyperedge_rejections": hyperedge_rejections,
            "atom_mapping_size": len(mapping),
            "cell_volume_ratio": _cell_volume_ratio(previous, current),
        },
    )
