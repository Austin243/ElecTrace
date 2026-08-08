from __future__ import annotations

import pytest

from electrace.events import (
    EventDetectionConfig,
    _topology_persistence,
    detect_sequence_events,
    detect_transition_events,
)
from electrace.matching import match_features
from electrace.models import Event, EventType, Feature, Frame, MatchEdge, MatchResult, Sequence

CELL = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))


def frame(index: int, *features: Feature) -> Frame:
    return Frame(index, str(index), CELL, (), features)


def test_transition_reports_birth_and_death() -> None:
    previous = frame(0, Feature("gone", "nna", (0.1, 0.1, 0.1)))
    current = frame(1, Feature("born", "bcp", (0.8, 0.8, 0.8)))
    events = detect_transition_events(previous, current, match_features(previous, current))
    assert {event.event_type for event in events} == {EventType.BIRTH, EventType.DEATH}
    assert all(event.evidence["unmatched"] for event in events)


def test_transition_reports_split_with_conservation_evidence() -> None:
    previous = frame(0, Feature("p", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0))
    current = frame(
        1,
        Feature("a", "elf_basin", (0.49, 0.5, 0.5), 0.4, 4.0),
        Feature("b", "elf_basin", (0.51, 0.5, 0.5), 0.6, 6.0),
    )
    events = detect_transition_events(previous, current, match_features(previous, current))
    assert len(events) == 1
    assert events[0].event_type is EventType.SPLIT
    assert "conservation" in events[0].evidence["match"]


def test_ambiguous_assignment_suppresses_mapping_birth_and_property_claims() -> None:
    previous = frame(
        0,
        Feature(
            "source",
            "bond_critical_point",
            (0.5, 0.5, 0.5),
            attributes={"icohp": -0.2},
        ),
    )
    targets = (
        Feature(
            "a",
            "bond_critical_point",
            (0.5, 0.5, 0.5),
            attributes={"icohp": -1.2},
        ),
        Feature(
            "b",
            "bond_critical_point",
            (0.5, 0.5, 0.5),
            attributes={"icohp": -1.2},
        ),
    )
    results = []
    for ordered in (targets, tuple(reversed(targets))):
        current = frame(1, *ordered)
        matches = match_features(previous, current)
        results.append((matches, detect_transition_events(previous, current, matches)))

    assert all(matches.diagnostics["ambiguous"] is True for matches, _ in results)
    assert all(events == [] for _, events in results)


def test_proximity_only_candidate_never_becomes_a_persistent_split() -> None:
    sequence = Sequence(
        (
            frame(0, Feature("parent", "elf_basin", (0.5, 0.5, 0.5))),
            frame(
                1,
                Feature("left-1", "elf_basin", (0.48, 0.5, 0.5)),
                Feature("right-1", "elf_basin", (0.52, 0.5, 0.5)),
            ),
            frame(
                2,
                Feature("left-2", "elf_basin", (0.47, 0.5, 0.5)),
                Feature("right-2", "elf_basin", (0.53, 0.5, 0.5)),
            ),
        )
    )

    events = detect_sequence_events(sequence)

    assert not {event.event_type for event in events} & {
        EventType.SPLIT,
        EventType.MERGE,
        EventType.REARRANGEMENT,
    }


def test_merge_persistence_ends_when_merged_descendant_splits() -> None:
    sequence = Sequence(
        (
            frame(
                0,
                Feature("a", "elf_basin", (0.48, 0.5, 0.5), 0.4, 4.0),
                Feature("b", "elf_basin", (0.52, 0.5, 0.5), 0.6, 6.0),
            ),
            frame(1, Feature("c", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0)),
            frame(
                2,
                Feature("d", "elf_basin", (0.48, 0.5, 0.5), 0.4, 4.0),
                Feature("e", "elf_basin", (0.52, 0.5, 0.5), 0.6, 6.0),
            ),
        )
    )

    merge = next(
        event for event in detect_sequence_events(sequence) if event.event_type is EventType.MERGE
    )

    assert merge.persistence == 1
    assert merge.metadata["persistence_status"] == "transient"
    assert merge.metadata["persistence_end_frame"] == 1
    assert merge.evidence["persistence_termination"] == "merge_participant_expanded"
    assert (
        merge.evidence["persistence_cardinality_criterion"]
        == "all_target_participants_distinct_one_to_one"
    )


def test_observed_split_termination_is_transient_not_right_censored() -> None:
    sequence = Sequence(
        (
            frame(0, Feature("parent", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0)),
            frame(
                1,
                Feature("left", "elf_basin", (0.48, 0.5, 0.5), 0.4, 4.0),
                Feature("right", "elf_basin", (0.52, 0.5, 0.5), 0.6, 6.0),
            ),
            frame(2, Feature("merged", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0)),
        )
    )

    split = next(
        event
        for event in detect_sequence_events(
            sequence,
            event_config=EventDetectionConfig(
                min_persistence=3,
                hysteresis_frames=0,
            ),
        )
        if event.event_type is EventType.SPLIT
    )

    assert split.persistence == 1
    assert split.evidence["maximum_observable_persistence"] == 2
    assert split.evidence["persistence_termination"] == "split_participants_coalesced"
    assert split.metadata["persistence_status"] == "transient"


def test_observed_termination_above_hysteresis_is_still_transient() -> None:
    sequence = Sequence(
        (
            frame(0, Feature("parent", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0)),
            frame(
                1,
                Feature("left-1", "elf_basin", (0.48, 0.5, 0.5), 0.4, 4.0),
                Feature("right-1", "elf_basin", (0.52, 0.5, 0.5), 0.6, 6.0),
            ),
            frame(
                2,
                Feature("left-2", "elf_basin", (0.47, 0.5, 0.5), 0.4, 4.0),
                Feature("right-2", "elf_basin", (0.53, 0.5, 0.5), 0.6, 6.0),
            ),
            frame(3, Feature("merged", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0)),
        )
    )

    split = next(
        event
        for event in detect_sequence_events(
            sequence,
            event_config=EventDetectionConfig(
                min_persistence=4,
                hysteresis_frames=1,
            ),
        )
        if event.event_type is EventType.SPLIT
    )

    assert split.persistence == 2
    assert split.persistence > 1
    assert split.evidence["maximum_observable_persistence"] == 3
    assert split.evidence["persistence_termination"] == "split_participants_coalesced"
    assert split.metadata["persistence_status"] == "transient"


def test_rearrangement_requires_distinct_one_to_one_participant_lineages() -> None:
    event = Event(
        EventType.REARRANGEMENT,
        0,
        1,
        ("a", "b"),
        ("c", "d"),
        0.8,
    )
    onset = MatchResult(
        0,
        1,
        edges=(MatchEdge(("a", "b"), ("c", "d"), 0.1, 0.8),),
    )
    participant_expands = MatchResult(
        1,
        2,
        edges=(
            MatchEdge(("c",), ("e", "f"), 0.1, 0.8),
            MatchEdge(("d",), ("g",), 0.1, 0.8),
        ),
    )

    persistence, end_position, evidence = _topology_persistence(
        event,
        0,
        (onset, participant_expands),
    )

    assert persistence == 1
    assert end_position == 1
    assert evidence["persistence_termination"] == "rearrangement_participant_expanded"
    assert (
        evidence["persistence_cardinality_criterion"]
        == "all_target_participants_distinct_one_to_one"
    )


def test_property_change_requires_configured_effect_size() -> None:
    previous = frame(
        0,
        Feature("a", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -0.2}),
    )
    current = frame(
        1,
        Feature("b", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -1.2}),
    )
    events = detect_transition_events(previous, current, match_features(previous, current))
    assert len(events) == 1
    event = events[0]
    assert event.event_type is EventType.PROPERTY_CHANGE
    assert event.evidence["descriptor_changes"]["icohp"]["delta"] == pytest.approx(-1.0)
    assert event.evidence["property_signals"]["attribute:icohp"]["baseline"] == pytest.approx(-0.2)


def test_property_change_is_confirmed_when_changed_state_persists() -> None:
    sequence = Sequence(
        (
            frame(
                0,
                Feature("f0", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -0.2}),
            ),
            frame(
                1,
                Feature("f1", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -1.2}),
            ),
            frame(
                2,
                Feature("f2", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -1.1}),
            ),
        )
    )

    event = next(
        item
        for item in detect_sequence_events(sequence)
        if item.event_type is EventType.PROPERTY_CHANGE
    )
    assert event.persistence == 2
    assert event.metadata["persistence_status"] == "confirmed"
    assert event.metadata["persistence_end_frame"] == 2


def test_property_persistence_tolerates_only_configured_gap_frames() -> None:
    sequence = Sequence(
        (
            frame(
                0,
                Feature("f0", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -0.2}),
            ),
            frame(
                1,
                Feature("f1", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -1.2}),
            ),
            frame(
                2,
                Feature("f2", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -0.4}),
            ),
            frame(
                3,
                Feature("f3", "bond_critical_point", (0.5, 0.5, 0.5), attributes={"icohp": -1.1}),
            ),
        )
    )

    tolerant = next(
        item
        for item in detect_sequence_events(
            sequence, event_config=EventDetectionConfig(hysteresis_frames=1)
        )
        if item.event_type is EventType.PROPERTY_CHANGE and item.frame_after == 1
    )
    strict = next(
        item
        for item in detect_sequence_events(
            sequence, event_config=EventDetectionConfig(hysteresis_frames=0)
        )
        if item.event_type is EventType.PROPERTY_CHANGE and item.frame_after == 1
    )

    assert tolerant.persistence == 2
    assert tolerant.metadata["persistence_status"] == "confirmed"
    assert tolerant.metadata["persistence_end_frame"] == 3
    assert tolerant.evidence["hysteresis_gap_frames"] == [2]
    assert strict.persistence == 1
    assert strict.metadata["persistence_status"] == "transient"


def test_hysteresis_rejects_one_frame_topology_flicker_even_when_minimum_is_one() -> None:
    sequence = Sequence(
        (
            frame(0),
            frame(1, Feature("noise", "nna", (0.5, 0.5, 0.5))),
            frame(2),
        )
    )
    rejected = next(
        item
        for item in detect_sequence_events(
            sequence,
            event_config=EventDetectionConfig(min_persistence=1, hysteresis_frames=1),
        )
        if item.event_type is EventType.BIRTH
    )
    accepted = next(
        item
        for item in detect_sequence_events(
            sequence,
            event_config=EventDetectionConfig(min_persistence=1, hysteresis_frames=0),
        )
        if item.event_type is EventType.BIRTH
    )

    assert rejected.metadata["persistence_status"] == "transient"
    assert accepted.metadata["persistence_status"] == "confirmed"


def test_negative_hysteresis_frames_are_rejected() -> None:
    with pytest.raises(ValueError, match="hysteresis_frames"):
        EventDetectionConfig(hysteresis_frames=-1)


def test_sequence_birth_is_confirmed_when_descendant_persists() -> None:
    sequence = Sequence(
        (
            frame(0),
            frame(1, Feature("new-1", "nna", (0.5, 0.5, 0.5))),
            frame(2, Feature("new-2", "nna", (0.51, 0.5, 0.5))),
        )
    )
    events = detect_sequence_events(sequence)
    birth = next(event for event in events if event.event_type is EventType.BIRTH)
    assert birth.persistence == 2
    assert birth.metadata["persistence_status"] == "confirmed"


def test_sequence_one_frame_birth_is_downweighted_as_transient() -> None:
    sequence = Sequence(
        (
            frame(0),
            frame(1, Feature("noise", "nna", (0.5, 0.5, 0.5))),
            frame(2),
        )
    )
    events = detect_sequence_events(sequence)
    birth = next(event for event in events if event.event_type is EventType.BIRTH)
    assert birth.persistence == 1
    assert birth.metadata["persistence_status"] == "transient"
    assert birth.confidence == pytest.approx(0.75 / 2)

    filtered = detect_sequence_events(
        sequence, event_config=EventDetectionConfig(include_transient=False)
    )
    assert not any(event.event_type is EventType.BIRTH for event in filtered)


def test_event_round_trip_is_serializable() -> None:
    event = Event(
        EventType.MERGE,
        2,
        3,
        ("a", "b"),
        ("c",),
        0.8,
        3,
        {"charge": {"delta": 0.1}},
        "merge",
    )
    assert Event.from_dict(event.to_dict()).to_dict() == event.to_dict()
