"""Serializable domain models used by the ElecTrace tracking engine.

Coordinates are fractional and cells use the crystallographic row-vector
convention: ``cartesian = fractional @ cell``.  Keeping this convention in
the model layer makes data produced by ASE, pymatgen, and common electronic
structure codes straightforward to adapt at the I/O boundary.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from collections.abc import Sequence as TypingSequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import islice, pairwise
from typing import Any, TypeAlias

Vec3: TypeAlias = tuple[float, float, float]
Matrix3: TypeAlias = tuple[Vec3, Vec3, Vec3]
Identifier: TypeAlias = int | str


class FeatureKind(StrEnum):
    """Well-known feature kinds.

    Parsers may also use arbitrary strings: ElecTrace intentionally does not
    reject unknown feature kinds so third-party topology tools can extend the
    vocabulary without changing the core package.
    """

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
    """Cardinality of a correspondence edge."""

    ONE_TO_ONE = "one_to_one"
    SPLIT = "split"
    MERGE = "merge"
    MANY_TO_MANY = "many_to_many"


class EventType(StrEnum):
    """Electronic-topology changes reported between frames."""

    BIRTH = "birth"
    DEATH = "death"
    SPLIT = "split"
    MERGE = "merge"
    REARRANGEMENT = "rearrangement"
    PROPERTY_CHANGE = "property_change"


def _vec3(value: Iterable[float], *, name: str) -> Vec3:
    # Consume at most one item beyond the accepted shape.  Inputs cross an
    # untrusted parser boundary and may be generators; materializing an
    # accidentally unbounded iterable just to reject its shape is avoidable.
    values = tuple(float(component) for component in islice(value, 4))
    if len(values) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    if not all(math.isfinite(component) for component in values):
        raise ValueError(f"{name} must contain only finite values")
    return values


def _matrix3(value: Iterable[Iterable[float]], *, name: str = "cell") -> Matrix3:
    rows = tuple(_vec3(row, name=f"{name} row") for row in islice(value, 4))
    if len(rows) != 3:
        raise ValueError(f"{name} must contain exactly three row vectors")
    # Determinant in pure Python so importing the data model never requires
    # importing NumPy (useful for metadata-only inspection).
    a, b, c = rows
    determinant = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    scale = max(abs(component) for row in rows for component in row)
    if abs(determinant) <= max(1.0, scale**3) * 1.0e-12:
        raise ValueError(f"{name} must be nonsingular")
    return rows


def _finite_optional(value: float | None, *, name: str) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when provided")
    return result


def _json_value(value: Any) -> Any:
    """Convert common scientific-Python values to JSON-compatible objects."""

    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("non-finite floats are not JSON serializable")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


@dataclass(slots=True)
class Atom:
    """An atom in one frame, identified by its frame-local integer index."""

    index: int
    element: str
    fractional_position: Vec3
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = int(self.index)
        if self.index < 0:
            raise ValueError("atom index must be non-negative")
        self.element = str(self.element).strip()
        if not self.element:
            raise ValueError("atom element must not be empty")
        self.fractional_position = _vec3(self.fractional_position, name="fractional_position")
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
            index=int(data["index"]),
            element=str(data["element"]),
            fractional_position=data["fractional_position"],
            attributes=dict(data.get("attributes", {})),
        )


@dataclass(slots=True)
class Feature:
    """A non-atomic electronic feature in one frame.

    ``charge`` and ``volume`` are treated as extensive properties by the
    split/merge logic.  ``neighbor_atom_indices`` is a frame-local atom
    fingerprint.  ``voxel_indices`` is optional because point-like critical
    points do not have a voxel support; when present it should refer to the
    grid shape stored in ``attributes['grid_shape']`` or frame metadata.
    """

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
        if not self.id:
            raise ValueError("feature id must not be empty")
        self.kind = str(self.kind).strip().lower()
        if not self.kind:
            raise ValueError("feature kind must not be empty")
        self.fractional_position = _vec3(self.fractional_position, name="fractional_position")
        self.charge = _finite_optional(self.charge, name="feature charge")
        self.volume = _finite_optional(self.volume, name="feature volume")
        if self.volume is not None and self.volume < 0:
            raise ValueError("feature volume must be non-negative")
        neighbors = tuple(int(index) for index in self.neighbor_atom_indices)
        if any(index < 0 for index in neighbors):
            raise ValueError("neighbor atom indices must be non-negative")
        if len(set(neighbors)) != len(neighbors):
            raise ValueError("neighbor atom indices must be unique")
        self.neighbor_atom_indices = tuple(sorted(neighbors))
        self.attributes = dict(self.attributes)
        if self.voxel_indices is not None:
            voxels: set[tuple[int, int, int]] = set()
            for voxel in self.voxel_indices:
                item = tuple(int(component) for component in voxel)
                if len(item) != 3:
                    raise ValueError("every voxel index must have three components")
                voxels.add(item)
            self.voxel_indices = tuple(sorted(voxels))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
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
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            fractional_position=data["fractional_position"],
            charge=data.get("charge"),
            volume=data.get("volume"),
            neighbor_atom_indices=tuple(data.get("neighbor_atom_indices", ())),
            attributes=dict(data.get("attributes", {})),
            voxel_indices=(
                tuple(tuple(voxel) for voxel in data["voxel_indices"])
                if data.get("voxel_indices") is not None
                else None
            ),
        )


@dataclass(slots=True)
class Frame:
    """Atoms and electronic features sampled at one ordered path image."""

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
            atom if isinstance(atom, Atom) else Atom.from_dict(atom) for atom in self.atoms
        )
        self.features = tuple(
            feature if isinstance(feature, Feature) else Feature.from_dict(feature)
            for feature in self.features
        )
        atom_indices = [atom.index for atom in self.atoms]
        if len(set(atom_indices)) != len(atom_indices):
            raise ValueError("atom indices must be unique within a frame")
        feature_ids = [feature.id for feature in self.features]
        if len(set(feature_ids)) != len(feature_ids):
            raise ValueError("feature ids must be unique within a frame")
        known_atoms = set(atom_indices)
        for feature in self.features:
            unknown = set(feature.neighbor_atom_indices) - known_atoms
            if unknown:
                raise ValueError(
                    f"feature {feature.id!r} references unknown atom indices {sorted(unknown)}"
                )
        self.energy = _finite_optional(self.energy, name="frame energy")
        self.metadata = dict(self.metadata)

    def atom_by_index(self, index: int) -> Atom:
        for atom in self.atoms:
            if atom.index == index:
                return atom
        raise KeyError(index)

    def feature_by_id(self, feature_id: str) -> Feature:
        for feature in self.features:
            if feature.id == feature_id:
                return feature
        raise KeyError(feature_id)

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
            index=index,
            label=str(data.get("label", index)),
            cell=data["cell"],
            atoms=tuple(Atom.from_dict(atom) for atom in data.get("atoms", ())),
            features=tuple(Feature.from_dict(feature) for feature in data.get("features", ())),
            energy=data.get("energy"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class Sequence:
    """An ordered NEB, pressure, strain, or trajectory sequence."""

    frames: tuple[Frame, ...]
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.frames = tuple(
            frame if isinstance(frame, Frame) else Frame.from_dict(frame) for frame in self.frames
        )
        indices = [frame.index for frame in self.frames]
        if len(set(indices)) != len(indices):
            raise ValueError("frame indices must be unique")
        if any(right <= left for left, right in pairwise(indices)):
            raise ValueError("frames must be strictly ordered by increasing index")
        self.label = str(self.label)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "frames": [frame.to_dict() for frame in self.frames],
            "metadata": _json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Sequence:
        return cls(
            frames=tuple(Frame.from_dict(frame) for frame in data.get("frames", ())),
            label=str(data.get("label", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class MatchEdge:
    """A one-to-one or hyperedge correspondence between adjacent frames."""

    source_ids: tuple[Identifier, ...]
    target_ids: tuple[Identifier, ...]
    cost: float
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source_ids = tuple(self.source_ids)
        self.target_ids = tuple(self.target_ids)
        if not self.source_ids or not self.target_ids:
            raise ValueError("a match edge needs at least one source and one target")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source ids in a match edge must be unique")
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("target ids in a match edge must be unique")
        self.cost = float(self.cost)
        self.confidence = float(self.confidence)
        if not math.isfinite(self.cost) or self.cost < 0:
            raise ValueError("match cost must be finite and non-negative")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("match confidence must lie in [0, 1]")
        self.evidence = dict(self.evidence)

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
            raise AttributeError("hyperedge does not have a single source_id")
        return self.source_ids[0]

    @property
    def target_id(self) -> Identifier:
        if len(self.target_ids) != 1:
            raise AttributeError("hyperedge does not have a single target_id")
        return self.target_ids[0]

    @property
    def score(self) -> float:
        """Alias retained for consumers that call confidence a match score."""

        return self.confidence

    @property
    def ambiguous(self) -> bool:
        """Whether a complete near-optimal assignment changes this decision."""

        return isinstance(self.evidence.get("assignment_ambiguity"), Mapping)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ids": _json_value(self.source_ids),
            "target_ids": _json_value(self.target_ids),
            "relation": self.relation.value,
            "cost": self.cost,
            "confidence": self.confidence,
            "evidence": _json_value(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MatchEdge:
        return cls(
            source_ids=tuple(data["source_ids"]),
            target_ids=tuple(data["target_ids"]),
            cost=float(data["cost"]),
            confidence=float(data["confidence"]),
            evidence=dict(data.get("evidence", {})),
        )


@dataclass(slots=True)
class MatchResult:
    """Complete correspondence result for a pair of adjacent frames."""

    previous_frame_index: int
    current_frame_index: int
    edges: tuple[MatchEdge, ...] = ()
    unmatched_previous: tuple[Identifier, ...] = ()
    unmatched_current: tuple[Identifier, ...] = ()
    total_cost: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.previous_frame_index = int(self.previous_frame_index)
        self.current_frame_index = int(self.current_frame_index)
        self.edges = tuple(
            edge if isinstance(edge, MatchEdge) else MatchEdge.from_dict(edge)
            for edge in self.edges
        )
        self.unmatched_previous = tuple(self.unmatched_previous)
        self.unmatched_current = tuple(self.unmatched_current)
        self.total_cost = float(self.total_cost)
        if not math.isfinite(self.total_cost) or self.total_cost < 0:
            raise ValueError("total match cost must be finite and non-negative")
        self.diagnostics = dict(self.diagnostics)

    @property
    def matches(self) -> tuple[MatchEdge, ...]:
        return self.edges

    @property
    def mapping(self) -> dict[Identifier, Identifier]:
        """Return only unambiguous one-to-one portions of the mapping."""

        return {
            edge.source_id: edge.target_id
            for edge in self.edges
            if edge.relation is MatchRelation.ONE_TO_ONE and not edge.ambiguous
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_frame_index": self.previous_frame_index,
            "current_frame_index": self.current_frame_index,
            "edges": [edge.to_dict() for edge in self.edges],
            "unmatched_previous": _json_value(self.unmatched_previous),
            "unmatched_current": _json_value(self.unmatched_current),
            "total_cost": self.total_cost,
            "diagnostics": _json_value(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MatchResult:
        return cls(
            previous_frame_index=int(data["previous_frame_index"]),
            current_frame_index=int(data["current_frame_index"]),
            edges=tuple(MatchEdge.from_dict(edge) for edge in data.get("edges", ())),
            unmatched_previous=tuple(data.get("unmatched_previous", ())),
            unmatched_current=tuple(data.get("unmatched_current", ())),
            total_cost=float(data.get("total_cost", 0.0)),
            diagnostics=dict(data.get("diagnostics", {})),
        )


@dataclass(slots=True)
class Event:
    """A topology event with an explicit, machine-readable evidence vector."""

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
        self.frame_before = int(self.frame_before)
        self.frame_after = int(self.frame_after)
        self.source_ids = tuple(self.source_ids)
        self.target_ids = tuple(self.target_ids)
        self.confidence = float(self.confidence)
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("event confidence must lie in [0, 1]")
        self.persistence = int(self.persistence)
        if self.persistence < 1:
            raise ValueError("event persistence must be at least one frame")
        self.evidence = dict(self.evidence)
        self.description = str(self.description)
        self.metadata = dict(self.metadata)

    @property
    def event_type(self) -> EventType:
        return EventType(self.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type.value,
            "frame_before": self.frame_before,
            "frame_after": self.frame_after,
            "source_ids": _json_value(self.source_ids),
            "target_ids": _json_value(self.target_ids),
            "confidence": self.confidence,
            "persistence": self.persistence,
            "evidence": _json_value(self.evidence),
            "description": self.description,
            "metadata": _json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(
            type=str(data["type"]),
            frame_before=int(data["frame_before"]),
            frame_after=int(data["frame_after"]),
            source_ids=tuple(data.get("source_ids", ())),
            target_ids=tuple(data.get("target_ids", ())),
            confidence=float(data.get("confidence", 0.0)),
            persistence=int(data.get("persistence", 1)),
            evidence=dict(data.get("evidence", {})),
            description=str(data.get("description", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class TrackingResult:
    """Matching and event results for a complete sequence."""

    atom_matches: tuple[MatchResult, ...]
    feature_matches: tuple[MatchResult, ...]
    events: tuple[Event, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_matches": [result.to_dict() for result in self.atom_matches],
            "feature_matches": [result.to_dict() for result in self.feature_matches],
            "events": [event.to_dict() for event in self.events],
            "diagnostics": _json_value(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrackingResult:
        return cls(
            atom_matches=tuple(
                MatchResult.from_dict(result) for result in data.get("atom_matches", ())
            ),
            feature_matches=tuple(
                MatchResult.from_dict(result) for result in data.get("feature_matches", ())
            ),
            events=tuple(Event.from_dict(event) for event in data.get("events", ())),
            diagnostics=dict(data.get("diagnostics", {})),
        )


def ensure_sequence(value: Sequence | TypingSequence[Frame]) -> Sequence:
    """Coerce a frame iterable to :class:`Sequence` for public APIs."""

    if isinstance(value, Sequence):
        return value
    return Sequence(tuple(value))
