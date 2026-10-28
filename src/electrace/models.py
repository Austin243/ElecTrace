"""Domain records for periodic structures, matches, and events."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from collections.abc import Sequence as TypingSequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from typing import Any, TypeAlias

Vec3: TypeAlias = tuple[float, float, float]
Matrix3: TypeAlias = tuple[Vec3, Vec3, Vec3]
Identifier: TypeAlias = int | str


class FeatureKind(StrEnum):
    ATOMIC_BASIN = "atomic_basin"
    ELF_BASIN = "elf_basin"
    NON_NUCLEAR_ATTRACTOR = "non_nuclear_attractor"
    BOND_CRITICAL_POINT = "bond_critical_point"
    RING_CRITICAL_POINT = "ring_critical_point"
    CAGE_CRITICAL_POINT = "cage_critical_point"
    BOND_PATH = "bond_path"
    BOND_DESCRIPTOR = "bond_descriptor"
    ATOMIC_CHARGE = "atomic_charge"
    OTHER = "other"


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
    if isinstance(value, StrEnum):
        return value.value
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
    if hasattr(value, "item"):
        return _json_value(value.item())
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


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
        return {
            "index": self.index,
            "element": self.element,
            "fractional_position": list(self.fractional_position),
            "attributes": _json_value(self.attributes),
        }

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
    kind: str | FeatureKind
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
        result = {
            "id": self.id,
            "kind": self.kind,
            "fractional_position": list(self.fractional_position),
            "charge": self.charge,
            "volume": self.volume,
            "neighbor_atom_indices": list(self.neighbor_atom_indices),
            "attributes": _json_value(self.attributes),
        }
        if self.voxel_indices is not None:
            result["voxel_indices"] = [list(voxel) for voxel in self.voxel_indices]
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
        self.atoms = tuple(
            item if isinstance(item, Atom) else Atom.from_dict(item) for item in self.atoms
        )
        self.features = tuple(
            item if isinstance(item, Feature) else Feature.from_dict(item) for item in self.features
        )
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

    def feature_by_id(self, feature_id: str) -> Feature:
        try:
            return next(feature for feature in self.features if feature.id == feature_id)
        except StopIteration:
            raise KeyError(feature_id) from None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "cell": [list(row) for row in self.cell],
            "atoms": [atom.to_dict() for atom in self.atoms],
            "features": [feature.to_dict() for feature in self.features],
            "energy": self.energy,
            "metadata": _json_value(self.metadata),
        }

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
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.frames = tuple(
            item if isinstance(item, Frame) else Frame.from_dict(item) for item in self.frames
        )
        indices = [frame.index for frame in self.frames]
        if any(right <= left for left, right in pairwise(indices)):
            raise ValueError("frames must have increasing unique indices")
        self.label = str(self.label)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "frames": [item.to_dict() for item in self.frames],
            "metadata": _json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Sequence:
        return cls(
            tuple(Frame.from_dict(item) for item in data.get("frames", ())),
            str(data.get("label", "")),
            dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class MatchEdge:
    source_ids: tuple[Identifier, ...]
    target_ids: tuple[Identifier, ...]
    cost: float
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source_ids, self.target_ids = tuple(self.source_ids), tuple(self.target_ids)
        self.cost, self.confidence = float(self.cost), float(self.confidence)
        self.evidence = dict(self.evidence)
        if (
            not self.source_ids
            or not self.target_ids
            or self.cost < 0
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("invalid match edge")

    @property
    def relation(self) -> MatchRelation:
        if len(self.source_ids) == len(self.target_ids) == 1:
            return MatchRelation.ONE_TO_ONE
        if len(self.source_ids) == 1:
            return MatchRelation.SPLIT
        if len(self.target_ids) == 1:
            return MatchRelation.MERGE
        return MatchRelation.MANY_TO_MANY

    @property
    def source_id(self) -> Identifier:
        if len(self.source_ids) != 1:
            raise AttributeError("hyperedge has multiple sources")
        return self.source_ids[0]

    @property
    def target_id(self) -> Identifier:
        if len(self.target_ids) != 1:
            raise AttributeError("hyperedge has multiple targets")
        return self.target_ids[0]

    score = property(lambda self: self.confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ids": list(self.source_ids),
            "target_ids": list(self.target_ids),
            "relation": self.relation.value,
            "cost": self.cost,
            "confidence": self.confidence,
            "evidence": _json_value(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MatchEdge:
        return cls(
            tuple(data["source_ids"]),
            tuple(data["target_ids"]),
            float(data["cost"]),
            float(data["confidence"]),
            dict(data.get("evidence", {})),
        )


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
    def matches(self) -> tuple[MatchEdge, ...]:
        return self.edges

    @property
    def mapping(self) -> dict[Identifier, Identifier]:
        return {
            edge.source_id: edge.target_id
            for edge in self.edges
            if edge.relation is MatchRelation.ONE_TO_ONE
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_frame_index": self.previous_frame_index,
            "current_frame_index": self.current_frame_index,
            "edges": [edge.to_dict() for edge in self.edges],
            "unmatched_previous": list(self.unmatched_previous),
            "unmatched_current": list(self.unmatched_current),
            "total_cost": self.total_cost,
            "diagnostics": _json_value(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MatchResult:
        return cls(
            int(data["previous_frame_index"]),
            int(data["current_frame_index"]),
            tuple(MatchEdge.from_dict(item) for item in data.get("edges", ())),
            tuple(data.get("unmatched_previous", ())),
            tuple(data.get("unmatched_current", ())),
            float(data.get("total_cost", 0)),
            dict(data.get("diagnostics", {})),
        )


@dataclass(slots=True)
class Event:
    type: EventType | str
    frame_before: int
    frame_after: int
    source_ids: tuple[Identifier, ...] = ()
    target_ids: tuple[Identifier, ...] = ()
    confidence: float = 0.0
    persistence: int = 1
    evidence: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = EventType(self.type)
        self.source_ids, self.target_ids = tuple(self.source_ids), tuple(self.target_ids)
        self.confidence, self.persistence = float(self.confidence), int(self.persistence)
        self.evidence, self.metadata = dict(self.evidence), dict(self.metadata)
        if not 0 <= self.confidence <= 1 or self.persistence < 1:
            raise ValueError("invalid event confidence or persistence")

    @property
    def event_type(self) -> EventType:
        return EventType(self.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type.value,
            "frame_before": self.frame_before,
            "frame_after": self.frame_after,
            "source_ids": list(self.source_ids),
            "target_ids": list(self.target_ids),
            "confidence": self.confidence,
            "persistence": self.persistence,
            "evidence": _json_value(self.evidence),
            "description": self.description,
            "metadata": _json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(
            str(data["type"]),
            int(data["frame_before"]),
            int(data["frame_after"]),
            tuple(data.get("source_ids", ())),
            tuple(data.get("target_ids", ())),
            float(data.get("confidence", 0)),
            int(data.get("persistence", 1)),
            dict(data.get("evidence", {})),
            str(data.get("description", "")),
            dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class TrackingResult:
    atom_matches: tuple[MatchResult, ...]
    feature_matches: tuple[MatchResult, ...]
    events: tuple[Event, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_matches": [item.to_dict() for item in self.atom_matches],
            "feature_matches": [item.to_dict() for item in self.feature_matches],
            "events": [item.to_dict() for item in self.events],
            "diagnostics": _json_value(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrackingResult:
        return cls(
            tuple(MatchResult.from_dict(item) for item in data.get("atom_matches", ())),
            tuple(MatchResult.from_dict(item) for item in data.get("feature_matches", ())),
            tuple(Event.from_dict(item) for item in data.get("events", ())),
            dict(data.get("diagnostics", {})),
        )


def ensure_sequence(value: Sequence | TypingSequence[Frame]) -> Sequence:
    return value if isinstance(value, Sequence) else Sequence(tuple(value))
