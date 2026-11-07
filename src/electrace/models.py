"""Domain records for periodic structures, matches, and events."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from itertools import pairwise
from typing import Any, TypeAlias

Vec3: TypeAlias = tuple[float, float, float]
Matrix3: TypeAlias = tuple[Vec3, Vec3, Vec3]
Identifier: TypeAlias = int | str


class FeatureKind(StrEnum):
    BOND_CRITICAL_POINT = "bond_critical_point"
    RING_CRITICAL_POINT = "ring_critical_point"
    CAGE_CRITICAL_POINT = "cage_critical_point"
    ATOMIC_CHARGE = "atomic_charge"


class MatchRelation(StrEnum):
    ONE_TO_ONE = "one_to_one"
    SPLIT = "split"
    MERGE = "merge"
    MANY_TO_MANY = "many_to_many"


class EventType(StrEnum):
    BIRTH = "birth"
    DEATH = "death"
    SPLIT = "split"
    MERGE = "merge"
    REARRANGEMENT = "rearrangement"
    PROPERTY_CHANGE = "property_change"
    CONNECTIVITY_CHANGE = "connectivity_change"


def _vec3(value: Iterable[float], name: str = "coordinate") -> Vec3:
    result = tuple(float(component) for component in value)
    if len(result) != 3 or not all(math.isfinite(component) for component in result):
        raise ValueError(f"{name} must contain three finite values")
    return result


def _matrix3(value: Iterable[Iterable[float]]) -> Matrix3:
    rows = tuple(_vec3(row, "cell row") for row in value)
    if len(rows) != 3:
        raise ValueError("cell must contain three rows")
    a, b, c = rows
    determinant = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if abs(determinant) < 1e-12:
        raise ValueError("cell must be nonsingular")
    return rows


def _finite(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("non-finite value is not JSON serializable")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if isinstance(value, (Atom, Feature, MatchEdge)):
        return value.to_dict()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _record(item: Any) -> dict[str, Any]:
    """Serialize a dataclass field by field; float fields pass through unchecked."""

    record = {}
    for field_ in fields(item):
        value = getattr(item, field_.name)
        record[field_.name] = value if isinstance(value, float) else _json_value(value)
    return record


@dataclass(slots=True)
class Atom:
    index: int
    element: str
    fractional_position: Vec3
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = int(self.index)
        self.element = str(self.element).strip()
        if self.index < 0 or not self.element:
            raise ValueError("atom requires a non-negative index and element")
        self.fractional_position = _vec3(self.fractional_position)
        self.attributes = dict(self.attributes)

    def to_dict(self) -> dict[str, Any]:
        return _record(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Atom:
        return cls(
            int(data["index"]),
            str(data["element"]),
            data["fractional_position"],
            dict(data.get("attributes", {})),
        )


@dataclass(slots=True)
class Feature:
    id: str
    kind: str
    fractional_position: Vec3
    charge: float | None = None
    volume: float | None = None
    neighbor_atom_indices: tuple[int, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)
    voxel_indices: tuple[tuple[int, int, int], ...] | None = None

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.kind = str(self.kind).strip().lower()
        if not self.id or not self.kind:
            raise ValueError("feature requires an id and kind")
        self.fractional_position = _vec3(self.fractional_position)
        self.charge = _finite(self.charge, "feature charge")
        self.volume = _finite(self.volume, "feature volume")
        if self.volume is not None and self.volume < 0:
            raise ValueError("feature volume must be non-negative")
        neighbors = tuple(sorted(int(index) for index in self.neighbor_atom_indices))
        if len(neighbors) != len(set(neighbors)) or any(index < 0 for index in neighbors):
            raise ValueError("neighbor atom indices must be unique and non-negative")
        self.neighbor_atom_indices = neighbors
        self.attributes = dict(self.attributes)
        if self.voxel_indices is not None:
            voxels: set[tuple[int, int, int]] = set()
            for voxel in self.voxel_indices:
                item = tuple(int(component) for component in voxel)
                if len(item) != 3:
                    raise ValueError("voxel indices must have three components")
                voxels.add((item[0], item[1], item[2]))
            self.voxel_indices = tuple(sorted(voxels))

    def to_dict(self) -> dict[str, Any]:
        result = _record(self)
        if self.voxel_indices is None:
            del result["voxel_indices"]
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Feature:
        voxels = data.get("voxel_indices")
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            fractional_position=data["fractional_position"],
            charge=data.get("charge"),
            volume=data.get("volume"),
            neighbor_atom_indices=tuple(data.get("neighbor_atom_indices", ())),
            attributes=dict(data.get("attributes", {})),
            voxel_indices=tuple(tuple(item) for item in voxels) if voxels is not None else None,
        )


@dataclass(slots=True)
class Frame:
    index: int
    label: str
    cell: Matrix3
    atoms: tuple[Atom, ...] = ()
    features: tuple[Feature, ...] = ()
    energy: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = int(self.index)
        self.label = str(self.label)
        self.cell = _matrix3(self.cell)
        atom_ids = {atom.index for atom in self.atoms}
        if len(atom_ids) != len(self.atoms) or len({item.id for item in self.features}) != len(
            self.features
        ):
            raise ValueError("atom indices and feature ids must be unique within a frame")
        if any(set(item.neighbor_atom_indices) - atom_ids for item in self.features):
            raise ValueError("feature references an unknown atom")
        self.energy = _finite(self.energy, "frame energy")
        self.metadata = dict(self.metadata)

    def atom_by_index(self, index: int) -> Atom:
        try:
            return next(atom for atom in self.atoms if atom.index == index)
        except StopIteration:
            raise KeyError(index) from None

    def to_dict(self) -> dict[str, Any]:
        return _record(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Frame:
        index = int(data.get("index", 0))
        return cls(
            index,
            str(data.get("label", index)),
            data["cell"],
            tuple(Atom.from_dict(item) for item in data.get("atoms", ())),
            tuple(Feature.from_dict(item) for item in data.get("features", ())),
            data.get("energy"),
            dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class Sequence:
    frames: tuple[Frame, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        indices = [frame.index for frame in self.frames]
        if any(right <= left for left, right in pairwise(indices)):
            raise ValueError("frames must have increasing unique indices")
        self.metadata = dict(self.metadata)


@dataclass(slots=True)
class MatchEdge:
    source_ids: tuple[Identifier, ...]
    target_ids: tuple[Identifier, ...]
    cost: float
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def relation(self) -> MatchRelation:
        if len(self.source_ids) == len(self.target_ids) == 1:
            return MatchRelation.ONE_TO_ONE
        if len(self.source_ids) == 1:
            return MatchRelation.SPLIT
        if len(self.target_ids) == 1:
            return MatchRelation.MERGE
        return MatchRelation.MANY_TO_MANY

    def to_dict(self) -> dict[str, Any]:
        return {**_record(self), "relation": self.relation.value}


@dataclass(slots=True)
class MatchResult:
    previous_frame_index: int
    current_frame_index: int
    edges: tuple[MatchEdge, ...] = ()
    unmatched_previous: tuple[Identifier, ...] = ()
    unmatched_current: tuple[Identifier, ...] = ()
    total_cost: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def mapping(self) -> dict[Identifier, Identifier]:
        return {
            edge.source_ids[0]: edge.target_ids[0]
            for edge in self.edges
            if edge.relation is MatchRelation.ONE_TO_ONE
        }

    def to_dict(self) -> dict[str, Any]:
        return _record(self)


@dataclass(slots=True)
class Event:
    type: EventType
    frame_before: int
    frame_after: int
    source_ids: tuple[Identifier, ...] = ()
    target_ids: tuple[Identifier, ...] = ()
    confidence: float = 0.0
    persistence: int = 1
    evidence: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> EventType:
        return self.type


@dataclass(slots=True)
class TrackingResult:
    atom_matches: tuple[MatchResult, ...]
    feature_matches: tuple[MatchResult, ...]
    events: tuple[Event, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)
    descriptor_gaps: tuple[dict[str, Any], ...] = ()
