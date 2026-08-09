from __future__ import annotations

import pytest

from electrace.events import EventDetectionConfig, detect_sequence_events, detect_transition_events
from electrace.matching import match_features
from electrace.models import EventType, Feature, Frame, Sequence

CELL = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))


def frame(index: int, *features: Feature) -> Frame:
    return Frame(index, str(index), CELL, (), features)


def test_transition_reports_birth_and_death() -> None:
    previous = frame(0, Feature("gone", "nna", (0.1, 0.1, 0.1)))
    current = frame(1, Feature("born", "bcp", (0.8, 0.8, 0.8)))

    events = detect_transition_events(previous, current, match_features(previous, current))

    assert {event.event_type for event in events} == {EventType.BIRTH, EventType.DEATH}
    assert all(event.evidence["unmatched"] for event in events)


def test_transition_reports_conserving_split() -> None:
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


def test_property_change_is_confirmed_when_lineage_persists() -> None:
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
        for item in detect_sequence_events(
            sequence, event_config=EventDetectionConfig(min_persistence=2)
        )
        if item.event_type is EventType.PROPERTY_CHANGE
    )

    assert event.persistence == 2
    assert event.metadata["persistence_status"] == "confirmed"
    assert event.evidence["descriptor_changes"]["icohp"]["delta"] == pytest.approx(-1.0)


def test_one_frame_birth_is_transient() -> None:
    sequence = Sequence(
        (
            frame(0),
            frame(1, Feature("noise", "nna", (0.5, 0.5, 0.5))),
            frame(2),
        )
    )

    birth = next(
        event for event in detect_sequence_events(sequence) if event.event_type is EventType.BIRTH
    )

    assert birth.persistence == 1
    assert birth.metadata["persistence_status"] == "transient"


def test_death_persistence_is_invariant_to_later_local_id_reuse() -> None:
    def death_status(later_id: str) -> tuple[int, str]:
        sequence = Sequence(
            (
                frame(0, Feature("local", "nna", (0.1, 0.1, 0.1))),
                frame(1),
                frame(2, Feature(later_id, "nna", (0.8, 0.8, 0.8))),
            )
        )
        death = next(
            event
            for event in detect_sequence_events(sequence)
            if event.event_type is EventType.DEATH and event.frame_before == 0
        )
        return death.persistence, str(death.metadata["persistence_status"])

    assert death_status("local") == death_status("different") == (2, "confirmed")
