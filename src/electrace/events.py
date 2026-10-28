"""Electronic-topology event detection and sequence persistence.

ElecTrace reports an evidence vector rather than treating any single charge,
ELF, or bond-order definition as ground truth.  Transition-level events are
therefore useful on their own, while sequence-level tracking annotates them
with a compact consecutive-lineage persistence classification.
"""

from __future__ import annotations

import math
from collections.abc import Sequence as TypingSequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any

import numpy as np

from .matching import (
    AtomMatchConfig,
    FeatureMatchConfig,
    match_atoms,
    match_features,
)
from .models import (
    Event,
    EventType,
    Feature,
    FeatureKind,
    Frame,
    MatchEdge,
    MatchRelation,
    MatchResult,
    Sequence,
    TrackingResult,
    ensure_sequence,
)


@dataclass(slots=True)
class EventDetectionConfig:
    """Thresholds for event detection and consecutive-frame persistence."""

    charge_change_threshold: float = 0.15
    volume_relative_threshold: float = 0.25
    descriptor_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "icohp": 0.5,
            "icobi": 0.1,
            "bond_order": 0.1,
            "elf": 0.1,
        }
    )
    min_event_confidence: float = 0.1
    min_persistence: int = 2
    include_transient: bool = True

    def __post_init__(self) -> None:
        for name in (
            "charge_change_threshold",
            "volume_relative_threshold",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            setattr(self, name, value)
        self.min_event_confidence = float(self.min_event_confidence)
        if (
            not math.isfinite(self.min_event_confidence)
            or not 0.0 <= self.min_event_confidence <= 1.0
        ):
            raise ValueError("min_event_confidence must lie in [0, 1]")
        self.min_persistence = int(self.min_persistence)
        if self.min_persistence < 1:
            raise ValueError("min_persistence must be at least one")
        self.descriptor_thresholds = {
            str(key): _positive(value, f"descriptor threshold {key!r}")
            for key, value in self.descriptor_thresholds.items()
        }


def _positive(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _feature_quality(features: TypingSequence[Feature]) -> tuple[float, list[str]]:
    factor = 1.0
    notes: list[str] = []
    for feature in features:
        if feature.attributes.get("grid_converged") is False:
            factor *= 0.7
            notes.append(f"{feature.id}: grid_refinement_failed")
        uncertainty = feature.attributes.get("uncertainty")
        uncertainty_value = _as_finite_float(uncertainty)
        if uncertainty_value is not None:
            factor *= 1.0 - min(1.0, max(0.0, uncertainty_value))
            notes.append(f"{feature.id}: uncertainty={uncertainty_value:g}")
    return float(max(0.0, min(1.0, factor))), notes


def _event_description(
    event_type: EventType,
    source_ids: TypingSequence[str],
    target_ids: TypingSequence[str],
) -> str:
    if event_type is EventType.BIRTH:
        return f"Feature {target_ids[0]} appears."
    if event_type is EventType.DEATH:
        return f"Feature {source_ids[0]} disappears."
    if event_type is EventType.SPLIT:
        return f"Feature {source_ids[0]} splits into {', '.join(target_ids)}."
    if event_type is EventType.MERGE:
        return f"Features {', '.join(source_ids)} merge into {target_ids[0]}."
    if event_type is EventType.REARRANGEMENT:
        return f"Features {', '.join(source_ids)} rearrange into {', '.join(target_ids)}."
    return f"Feature {source_ids[0]} changes electronic descriptors."


def _edge_event_type(edge: MatchEdge) -> EventType | None:
    if edge.relation is MatchRelation.SPLIT:
        return EventType.SPLIT
    if edge.relation is MatchRelation.MERGE:
        return EventType.MERGE
    if edge.relation is MatchRelation.MANY_TO_MANY:
        return EventType.REARRANGEMENT
    return None


def _property_change_event(
    source: Feature,
    target: Feature,
    edge: MatchEdge,
    previous: Frame,
    current: Frame,
    config: EventDetectionConfig,
) -> Event | None:
    evidence: dict[str, Any] = {}
    severities: list[float] = []
    if source.charge is not None and target.charge is not None:
        delta = target.charge - source.charge
        if abs(delta) >= config.charge_change_threshold:
            evidence["charge_delta"] = delta
            evidence["charge_threshold"] = config.charge_change_threshold
            severities.append(abs(delta) / config.charge_change_threshold)
    if source.volume is not None and target.volume is not None:
        relative = abs(target.volume - source.volume) / max(
            abs(source.volume), abs(target.volume), 1.0e-12
        )
        if relative >= config.volume_relative_threshold:
            evidence["volume_relative_change"] = relative
            evidence["volume_threshold"] = config.volume_relative_threshold
            severities.append(relative / config.volume_relative_threshold)
    descriptor_changes: dict[str, dict[str, float]] = {}
    for key, threshold in config.descriptor_thresholds.items():
        left = source.attributes.get(key)
        right = target.attributes.get(key)
        left_value = _as_finite_float(left)
        right_value = _as_finite_float(right)
        if left_value is None or right_value is None:
            continue
        delta = right_value - left_value
        if abs(delta) >= threshold:
            descriptor_changes[key] = {"delta": delta, "threshold": threshold}
            severities.append(abs(delta) / threshold)
    if descriptor_changes:
        evidence["descriptor_changes"] = descriptor_changes
    if not severities:
        return None
    severity = float(np.mean(severities))
    # Threshold-level evidence is deliberately moderate, then asymptotically
    # approaches one as multiple effect sizes exceed their thresholds.
    effect_confidence = 0.5 + 0.5 * (1.0 - math.exp(-(severity - 1.0)))
    _quality, notes = _feature_quality((source, target))
    # Match confidence already includes input-quality penalties; keep notes in
    # the event evidence without applying the same penalty a second time.
    confidence = edge.confidence * effect_confidence
    evidence.update(
        {
            "effect_size": severity,
            "match_confidence": edge.confidence,
            "quality_notes": notes,
        }
    )
    if confidence < config.min_event_confidence:
        return None
    return Event(
        type=EventType.PROPERTY_CHANGE,
        frame_before=previous.index,
        frame_after=current.index,
        source_ids=(source.id,),
        target_ids=(target.id,),
        confidence=confidence,
        evidence=evidence,
        description=_event_description(EventType.PROPERTY_CHANGE, (source.id,), (target.id,)),
    )


def _as_finite_float(value: Any) -> float | None:
    if not isinstance(value, (int, float, np.integer, np.floating)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _connectivity_change_event(
    source: Feature,
    target: Feature,
    edge: MatchEdge,
    previous: Frame,
    current: Frame,
    atom_matches: MatchResult | None,
    config: EventDetectionConfig,
) -> Event | None:
    """Compare known BCP neighbors using matched atoms, not frame-local indices.

    A matching BCP with different neighbors is a candidate reassignment, not
    proof of a bond or mechanism. Missing or unmapped neighbors are not changes.
    """
    if (
        atom_matches is None
        or source.kind != FeatureKind.BOND_CRITICAL_POINT
        or target.kind != FeatureKind.BOND_CRITICAL_POINT
        or len(source.neighbor_atom_indices) != 2
        or len(target.neighbor_atom_indices) != 2
    ):
        return None
    if (atom_matches.previous_frame_index, atom_matches.current_frame_index) != (
        previous.index,
        current.index,
    ):
        raise ValueError("atom match result does not correspond to the supplied frames")
    mapping = atom_matches.mapping
    reverse = {target_id: source_id for source_id, target_id in mapping.items()}
    before = set(source.neighbor_atom_indices)
    after = set(target.neighbor_atom_indices)
    if not before.issubset(mapping) or not after.issubset(reverse):
        return None
    if {mapping[index] for index in before} == after:
        return None

    relevant = before | {reverse[index] for index in after}
    atom_edges = [
        item
        for item in atom_matches.edges
        if item.relation is MatchRelation.ONE_TO_ONE and item.source_id in relevant
    ]
    if len(atom_edges) != len(relevant):
        return None
    confidence = min(edge.confidence, *(item.confidence for item in atom_edges))
    if confidence < config.min_event_confidence:
        return None
    previous_atoms = {atom.index: atom for atom in previous.atoms}
    current_atoms = {atom.index: atom for atom in current.atoms}
    atom_identity = []
    for atom_edge in atom_edges:
        old_atom = previous_atoms[int(atom_edge.source_id)]
        new_atom = current_atoms[int(atom_edge.target_id)]
        atom_identity.append(
            {
                "source_atom_index": old_atom.index,
                "target_atom_index": new_atom.index,
                "element": old_atom.element,
                "target_element": new_atom.element,
                "same_element_count_before": sum(
                    atom.element.casefold() == old_atom.element.casefold()
                    for atom in previous.atoms
                ),
                "same_element_count_after": sum(
                    atom.element.casefold() == new_atom.element.casefold() for atom in current.atoms
                ),
                "displacement_angstrom": atom_edge.evidence.get("distance"),
                "motion_similarity": atom_edge.confidence,
            }
        )
    neighbors_before = [
        {"index": index, "element": previous_atoms[index].element} for index in sorted(before)
    ]
    neighbors_after = [
        {"index": index, "element": current_atoms[index].element} for index in sorted(after)
    ]

    def label(neighbors: list[dict[str, Any]]) -> str:
        return "–".join(f"{atom['element']}[{atom['index']}]" for atom in neighbors)

    return Event(
        type=EventType.CONNECTIVITY_CHANGE,
        frame_before=previous.index,
        frame_after=current.index,
        source_ids=edge.source_ids,
        target_ids=edge.target_ids,
        confidence=confidence,
        description=(
            "Candidate matched bond critical-point neighbor change: "
            f"{label(neighbors_before)} → {label(neighbors_after)}. Review the match."
        ),
        evidence={
            "neighbors_before": neighbors_before,
            "neighbors_after": neighbors_after,
            "atom_matches": [item.to_dict() for item in atom_edges],
            "atom_identity": atom_identity,
            "feature_match": edge.to_dict(),
        },
    )


def detect_transition_events(
    previous: Frame,
    current: Frame,
    feature_matches: MatchResult,
    config: EventDetectionConfig | None = None,
    *,
    atom_matches: MatchResult | None = None,
) -> list[Event]:
    """Detect births, deaths, split/merge, and descriptor changes for one step."""

    settings = config or EventDetectionConfig()
    if (
        feature_matches.previous_frame_index != previous.index
        or feature_matches.current_frame_index != current.index
    ):
        raise ValueError("feature match result does not correspond to the supplied frames")

    previous_features = (
        {feature.id: feature for feature in previous.features}
        if feature_matches.edges or feature_matches.unmatched_previous
        else {}
    )
    current_features = (
        {feature.id: feature for feature in current.features}
        if feature_matches.edges or feature_matches.unmatched_current
        else {}
    )
    events: list[Event] = []
    for edge in feature_matches.edges:
        topology_type = _edge_event_type(edge)
        if topology_type is not None:
            sources = tuple(previous_features[str(item)] for item in edge.source_ids)
            targets = tuple(current_features[str(item)] for item in edge.target_ids)
            _quality, notes = _feature_quality((*sources, *targets))
            confidence = edge.confidence
            if confidence >= settings.min_event_confidence:
                events.append(
                    Event(
                        type=topology_type,
                        frame_before=previous.index,
                        frame_after=current.index,
                        source_ids=edge.source_ids,
                        target_ids=edge.target_ids,
                        confidence=confidence,
                        evidence={
                            "match": edge.evidence,
                            "match_cost": edge.cost,
                            "relation": edge.relation.value,
                            "quality_notes": notes,
                        },
                        description=_event_description(
                            topology_type,
                            tuple(str(item) for item in edge.source_ids),
                            tuple(str(item) for item in edge.target_ids),
                        ),
                    )
                )
        elif edge.relation is MatchRelation.ONE_TO_ONE:
            source = previous_features[str(edge.source_id)]
            target = current_features[str(edge.target_id)]
            changed = _property_change_event(source, target, edge, previous, current, settings)
            if changed is not None:
                events.append(changed)
            connectivity = _connectivity_change_event(
                source, target, edge, previous, current, atom_matches, settings
            )
            if connectivity is not None:
                events.append(connectivity)

    for source_id in feature_matches.unmatched_previous:
        source = previous_features[str(source_id)]
        quality, notes = _feature_quality((source,))
        confidence = 0.75 * quality
        if confidence >= settings.min_event_confidence:
            events.append(
                Event(
                    type=EventType.DEATH,
                    frame_before=previous.index,
                    frame_after=current.index,
                    source_ids=(source.id,),
                    confidence=confidence,
                    evidence={
                        "unmatched": True,
                        "feature_kind": source.kind,
                        "quality_notes": notes,
                    },
                    description=_event_description(EventType.DEATH, (source.id,), ()),
                )
            )
    for target_id in feature_matches.unmatched_current:
        target = current_features[str(target_id)]
        quality, notes = _feature_quality((target,))
        confidence = 0.75 * quality
        if confidence >= settings.min_event_confidence:
            events.append(
                Event(
                    type=EventType.BIRTH,
                    frame_before=previous.index,
                    frame_after=current.index,
                    target_ids=(target.id,),
                    confidence=confidence,
                    evidence={
                        "unmatched": True,
                        "feature_kind": target.kind,
                        "quality_notes": notes,
                    },
                    description=_event_description(EventType.BIRTH, (), (target.id,)),
                )
            )
    return sorted(
        events,
        key=lambda event: (
            event.frame_after,
            event.event_type.value,
            tuple(str(item) for item in event.source_ids),
            tuple(str(item) for item in event.target_ids),
        ),
    )


def _next_lineage(ids: set[str], result: MatchResult) -> set[str]:
    """Advance a lineage only when every current participant has a match."""

    successors: set[str] = set()
    matched_sources: set[str] = set()
    for edge in result.edges:
        sources = {str(item) for item in edge.source_ids}
        overlap = ids & sources
        if overlap:
            matched_sources.update(overlap)
            successors.update(str(item) for item in edge.target_ids)
    return successors if matched_sources == ids else set()


def _consecutive_persistence(
    event: Event,
    transition_index: int,
    frames: TypingSequence[Frame],
    matches: TypingSequence[MatchResult],
) -> tuple[int, int]:
    """Count consecutive frames with a surviving target or absent source lineage."""

    persistence = 1
    end_position = transition_index + 1
    live = {str(item) for item in event.target_ids}
    if live:
        for result_index in range(transition_index + 1, len(matches)):
            live = _next_lineage(live, matches[result_index])
            if not live:
                break
            persistence += 1
            end_position = result_index + 1
        return persistence, end_position

    # Feature identifiers are frame-local, so a later reuse of the same label is
    # not evidence that the ended lineage reappeared.
    return len(frames) - transition_index - 1, len(frames) - 1


def _annotate_persistence(
    events: TypingSequence[Event],
    frames: TypingSequence[Frame],
    matches: TypingSequence[MatchResult],
    config: EventDetectionConfig,
) -> list[Event]:
    transition_by_frames = {
        (frame.index, next_frame.index): index
        for index, (frame, next_frame) in enumerate(pairwise(frames))
    }
    annotated: list[Event] = []
    for event in events:
        transition_index = transition_by_frames[event.frame_before, event.frame_after]
        persistence, end_position = _consecutive_persistence(
            event, transition_index, frames, matches
        )
        maximum_observable = len(frames) - transition_index - 1
        metadata = dict(event.metadata)
        evidence = dict(event.evidence)
        evidence["minimum_persistence"] = config.min_persistence
        evidence["maximum_observable_persistence"] = maximum_observable
        if persistence >= config.min_persistence:
            status = "confirmed"
            confidence = event.confidence
        elif end_position == len(frames) - 1 and maximum_observable < config.min_persistence:
            status = "right_censored"
            confidence = event.confidence * 0.85
        else:
            status = "transient"
            confidence = event.confidence * persistence / config.min_persistence
        metadata["persistence_status"] = status
        metadata["persistence_end_frame"] = frames[end_position].index
        revised = replace(
            event,
            persistence=persistence,
            confidence=max(0.0, min(1.0, confidence)),
            evidence=evidence,
            metadata=metadata,
        )
        if config.include_transient or status != "transient":
            annotated.append(revised)
    return annotated


def track_sequence(
    sequence: Sequence | TypingSequence[Frame],
    atom_config: AtomMatchConfig | None = None,
    feature_config: FeatureMatchConfig | None = None,
    event_config: EventDetectionConfig | None = None,
) -> TrackingResult:
    """Match all adjacent frames and return persistence-aware event history."""

    ordered = ensure_sequence(sequence)
    atom_settings = atom_config or AtomMatchConfig()
    feature_settings = feature_config or FeatureMatchConfig()
    settings = event_config or EventDetectionConfig()
    atom_results: list[MatchResult] = []
    feature_results: list[MatchResult] = []
    raw_events: list[Event] = []
    for previous, current in pairwise(ordered.frames):
        atoms = match_atoms(previous, current, atom_settings)
        features = match_features(previous, current, atoms, feature_settings)
        atom_results.append(atoms)
        feature_results.append(features)
        raw_events.extend(
            detect_transition_events(previous, current, features, settings, atom_matches=atoms)
        )
    events = (
        _annotate_persistence(raw_events, ordered.frames, feature_results, settings)
        if raw_events
        else []
    )
    return TrackingResult(
        atom_matches=tuple(atom_results),
        feature_matches=tuple(feature_results),
        events=tuple(events),
        diagnostics={
            "frames": len(ordered.frames),
            "transitions": max(0, len(ordered.frames) - 1),
            "raw_events": len(raw_events),
            "reported_events": len(events),
            "minimum_persistence": settings.min_persistence,
        },
    )


def detect_sequence_events(
    sequence: Sequence | TypingSequence[Frame],
    atom_config: AtomMatchConfig | None = None,
    feature_config: FeatureMatchConfig | None = None,
    event_config: EventDetectionConfig | None = None,
) -> list[Event]:
    """Convenience wrapper returning only the sequence's annotated events."""

    return list(track_sequence(sequence, atom_config, feature_config, event_config).events)
