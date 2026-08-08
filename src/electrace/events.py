"""Electronic-topology event detection, persistence, and hysteresis.

ElecTrace reports an evidence vector rather than treating any single charge,
ELF, or bond-order definition as ground truth.  Transition-level events are
therefore useful on their own, while sequence-level tracking annotates them
with persistence and right-censoring information before confidence is used
for scientific interpretation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence as TypingSequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any

import numpy as np

from .geometry import deformation_aware_distance
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
    """Thresholds for topology, descriptor-change, and persistence evidence."""

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
    hysteresis_frames: int = 1
    include_transient: bool = True
    hysteresis_distance: float = 0.75
    transient_confidence_exponent: float = 1.0

    def __post_init__(self) -> None:
        for name in (
            "charge_change_threshold",
            "volume_relative_threshold",
            "hysteresis_distance",
            "transient_confidence_exponent",
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
        self.hysteresis_frames = int(self.hysteresis_frames)
        if self.hysteresis_frames < 0:
            raise ValueError("hysteresis_frames must be non-negative")
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
    signals: dict[str, dict[str, Any]] = {}
    severities: list[float] = []
    if source.charge is not None and target.charge is not None:
        delta = target.charge - source.charge
        if abs(delta) >= config.charge_change_threshold:
            evidence["charge_delta"] = delta
            evidence["charge_threshold"] = config.charge_change_threshold
            signals["charge"] = {
                "location": "charge",
                "baseline": source.charge,
                "onset": target.charge,
                "threshold": config.charge_change_threshold,
                "mode": "absolute",
                "direction": 1 if delta > 0 else -1,
            }
            severities.append(abs(delta) / config.charge_change_threshold)
    if source.volume is not None and target.volume is not None:
        relative = abs(target.volume - source.volume) / max(
            abs(source.volume), abs(target.volume), 1.0e-12
        )
        if relative >= config.volume_relative_threshold:
            evidence["volume_relative_change"] = relative
            evidence["volume_threshold"] = config.volume_relative_threshold
            volume_delta = target.volume - source.volume
            signals["volume"] = {
                "location": "volume",
                "baseline": source.volume,
                "onset": target.volume,
                "threshold": config.volume_relative_threshold,
                "mode": "relative",
                "direction": 1 if volume_delta > 0 else -1,
            }
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
            signals[f"attribute:{key}"] = {
                "location": "attribute",
                "attribute": key,
                "baseline": left_value,
                "onset": right_value,
                "threshold": threshold,
                "mode": "absolute",
                "direction": 1 if delta > 0 else -1,
            }
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
            "property_signals": signals,
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


def detect_transition_events(
    previous: Frame,
    current: Frame,
    feature_matches: MatchResult,
    config: EventDetectionConfig | None = None,
) -> list[Event]:
    """Detect births, deaths, split/merge, and descriptor changes for one step."""

    settings = config or EventDetectionConfig()
    if (
        feature_matches.previous_frame_index != previous.index
        or feature_matches.current_frame_index != current.index
    ):
        raise ValueError("feature match result does not correspond to the supplied frames")

    raw_ambiguities = feature_matches.diagnostics.get("ambiguities", [])
    ambiguities = raw_ambiguities if isinstance(raw_ambiguities, list) else []
    ambiguous_unmatched_previous = {
        str(item.get("selected", {}).get("source_id"))
        for item in ambiguities
        if isinstance(item, dict)
        and item.get("decision") == "unmatched_previous"
        and isinstance(item.get("selected"), dict)
    }
    ambiguous_unmatched_current = {
        str(item.get("selected", {}).get("target_id"))
        for item in ambiguities
        if isinstance(item, dict)
        and item.get("decision") == "unmatched_current"
        and isinstance(item.get("selected"), dict)
    }

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
        if edge.ambiguous:
            continue
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

    for source_id in feature_matches.unmatched_previous:
        if str(source_id) in ambiguous_unmatched_previous:
            continue
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
        if str(target_id) in ambiguous_unmatched_current:
            continue
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


def _successors(ids: set[str], result: MatchResult) -> set[str]:
    successors: set[str] = set()
    for edge in result.edges:
        if edge.ambiguous:
            continue
        if any(str(item) in ids for item in edge.source_ids):
            successors.update(str(item) for item in edge.target_ids)
    return successors


def _stable_one_to_one_successors(
    ids: set[str], result: MatchResult
) -> tuple[set[str] | None, str | None]:
    """Advance every participant through a distinct one-to-one lineage.

    Split, merge, and rearrangement persistence describes the lifetime of the
    topology created at onset, not merely survival of any descendant.  An
    outgoing hyperedge therefore ends that state even when descendants still
    exist.
    """

    outgoing: dict[str, list[MatchEdge]] = {}
    for edge in result.edges:
        for source_id in edge.source_ids:
            outgoing.setdefault(str(source_id), []).append(edge)

    successors: set[str] = set()
    for source_id in sorted(ids):
        source_edges = outgoing.get(source_id, [])
        if not source_edges:
            return None, "participant_lost"
        if len(source_edges) != 1:
            return None, "ambiguous_outgoing_lineage"
        edge = source_edges[0]
        if edge.ambiguous:
            return None, "ambiguous_outgoing_lineage"
        if edge.relation is MatchRelation.SPLIT:
            return None, "participant_expanded"
        if edge.relation is MatchRelation.MERGE:
            return None, "participants_coalesced"
        if edge.relation is MatchRelation.MANY_TO_MANY:
            return None, "participant_rearranged"
        successor = str(edge.target_id)
        if successor in successors:
            return None, "participants_coalesced"
        successors.add(successor)
    if len(successors) != len(ids):
        return None, "cardinality_changed"
    return successors, None


def _topology_persistence(
    event: Event,
    transition_index: int,
    matches: TypingSequence[MatchResult],
) -> tuple[int, int, dict[str, Any]]:
    live = {str(item) for item in event.target_ids}
    end_position = transition_index + 1
    if not live:
        return 1, end_position, {"persistence_termination": "no_target_lineage"}
    persistence = 1
    exact_state = event.event_type in {
        EventType.SPLIT,
        EventType.MERGE,
        EventType.REARRANGEMENT,
    }
    termination = "sequence_end"
    for result_index, result in enumerate(
        matches[transition_index + 1 :], start=transition_index + 1
    ):
        if exact_state:
            next_live, reason = _stable_one_to_one_successors(live, result)
            if next_live is None:
                termination = f"{event.event_type.value}_{reason}"
                break
            live = next_live
        else:
            live = _successors(live, result)
            if not live:
                termination = "lineage_ended"
                break
        persistence += 1
        end_position = result_index + 1
    return (
        persistence,
        end_position,
        {
            "persistence_termination": termination,
            "persistence_cardinality_criterion": (
                "all_target_participants_distinct_one_to_one"
                if exact_state
                else "any_descendant_lineage"
            ),
        },
    )


def _one_to_one_successor(source_id: str, result: MatchResult) -> str | None:
    for edge in result.edges:
        if edge.relation is not MatchRelation.ONE_TO_ONE or edge.ambiguous:
            continue
        if str(edge.source_id) == source_id:
            return str(edge.target_id)
    return None


def _property_signal_value(feature: Feature, signal: dict[str, Any]) -> float | None:
    location = signal.get("location")
    if location == "charge":
        return feature.charge
    if location == "volume":
        return feature.volume
    if location == "attribute":
        return _as_finite_float(feature.attributes.get(str(signal.get("attribute", ""))))
    return None


def _property_signal_state(feature: Feature, signal: dict[str, Any]) -> str:
    current = _property_signal_value(feature, signal)
    baseline = _as_finite_float(signal.get("baseline"))
    threshold = _as_finite_float(signal.get("threshold"))
    direction = int(signal.get("direction", 0))
    if current is None or baseline is None or threshold is None or direction not in (-1, 1):
        return "missing"
    delta = current - baseline
    if delta * direction < 0:
        return "reversed"
    if signal.get("mode") == "relative":
        magnitude = abs(delta) / max(abs(current), abs(baseline), 1.0e-12)
    else:
        magnitude = abs(delta)
    return "supported" if magnitude >= threshold else "below_threshold"


def _property_change_persistence(
    event: Event,
    transition_index: int,
    frames: TypingSequence[Frame],
    matches: TypingSequence[MatchResult],
    config: EventDetectionConfig,
) -> tuple[int, int, dict[str, Any]]:
    raw_signals = event.evidence.get("property_signals", {})
    if not isinstance(raw_signals, dict) or not raw_signals:
        return 1, transition_index + 1, {"persistence_termination": "missing_property_signal"}
    signals = {str(key): value for key, value in raw_signals.items() if isinstance(value, dict)}
    if not signals or len(event.target_ids) != 1:
        return 1, transition_index + 1, {"persistence_termination": "ambiguous_property_lineage"}

    current_id = str(event.target_ids[0])
    persistence = 1
    end_position = transition_index + 1
    consecutive_gaps = 0
    gap_frames: list[int] = []
    termination = "sequence_end"
    for result_index in range(transition_index + 1, len(matches)):
        successor = _one_to_one_successor(current_id, matches[result_index])
        if successor is None:
            termination = "lineage_changed"
            break
        target_position = result_index + 1
        feature = frames[target_position].feature_by_id(successor)
        states = {key: _property_signal_state(feature, signal) for key, signal in signals.items()}
        if "reversed" in states.values():
            termination = "property_reversed"
            break
        if all(state == "supported" for state in states.values()):
            persistence += 1
            end_position = target_position
            consecutive_gaps = 0
        else:
            consecutive_gaps += 1
            gap_frames.append(frames[target_position].index)
            if consecutive_gaps > config.hysteresis_frames:
                termination = "hysteresis_exceeded"
                break
        current_id = successor
    return (
        persistence,
        end_position,
        {
            "persistence_termination": termination,
            "hysteresis_gap_frames": gap_frames,
            "sustained_signal_ids": sorted(signals),
        },
    )


def _death_persistence(
    event: Event,
    transition_index: int,
    frames: TypingSequence[Frame],
    config: EventDetectionConfig,
) -> tuple[int, int, dict[str, Any]]:
    source_frame = frames[transition_index]
    source = source_frame.feature_by_id(str(event.source_ids[0]))
    persistence = 1
    end_position = transition_index + 1
    # Begin after the immediate current frame, whose absence generated death.
    for frame_position, frame in enumerate(
        frames[transition_index + 2 :], start=transition_index + 2
    ):
        reappeared = False
        for feature in frame.features:
            if feature.kind != source.kind:
                continue
            distance = float(
                deformation_aware_distance(
                    source.fractional_position,
                    feature.fractional_position,
                    source_frame.cell,
                    frame.cell,
                )
            )
            if distance <= config.hysteresis_distance:
                reappeared = True
                break
        if reappeared:
            return (
                persistence,
                end_position,
                {
                    "persistence_termination": "nearby_feature_reappeared",
                    "reappearance_frame": frame.index,
                },
            )
        persistence += 1
        end_position = frame_position
    return persistence, end_position, {"persistence_termination": "sequence_end"}


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
        if event.event_type is EventType.PROPERTY_CHANGE:
            persistence, end_position, persistence_evidence = _property_change_persistence(
                event, transition_index, frames, matches, config
            )
        elif event.event_type is EventType.DEATH:
            persistence, end_position, persistence_evidence = _death_persistence(
                event, transition_index, frames, config
            )
        else:
            persistence, end_position, persistence_evidence = _topology_persistence(
                event, transition_index, matches
            )
        maximum_observable = len(frames) - transition_index - 1
        metadata = dict(event.metadata)
        evidence = dict(event.evidence)
        evidence["minimum_persistence"] = config.min_persistence
        evidence["hysteresis_frames"] = config.hysteresis_frames
        evidence.update(persistence_evidence)
        terminated_early = persistence_evidence.get("persistence_termination") != "sequence_end"
        hysteresis_rejected = terminated_early and persistence <= config.hysteresis_frames
        if hysteresis_rejected:
            status = "transient"
            denominator = max(config.min_persistence, config.hysteresis_frames + 1)
            confidence = event.confidence * persistence / denominator
        elif terminated_early and persistence < config.min_persistence:
            status = "transient"
            fraction = persistence / config.min_persistence
            confidence = event.confidence * (fraction**config.transient_confidence_exponent)
        elif persistence >= config.min_persistence:
            status = "confirmed"
            confidence = event.confidence
        elif not terminated_early and maximum_observable < config.min_persistence:
            status = "right_censored"
            # Do not call an event noise merely because the sequence ended.
            confidence = event.confidence * 0.85
        else:
            status = "transient"
            fraction = persistence / config.min_persistence
            confidence = event.confidence * fraction**config.transient_confidence_exponent
        metadata["persistence_status"] = status
        metadata["persistence_end_frame"] = frames[end_position].index
        evidence["maximum_observable_persistence"] = maximum_observable
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
        raw_events.extend(detect_transition_events(previous, current, features, settings))
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
            "ambiguous_atom_decisions": sum(
                len(result.diagnostics.get("ambiguities", [])) for result in atom_results
            ),
            "ambiguous_feature_decisions": sum(
                len(result.diagnostics.get("ambiguities", [])) for result in feature_results
            ),
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
