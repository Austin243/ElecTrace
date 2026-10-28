from dataclasses import replace

import numpy as np

from electrace.events import detect_transition_events
from electrace.matching import FeatureMatchConfig, _feature_pair
from electrace.models import Atom, Feature, Frame, MatchEdge, MatchResult

CELL = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))


def make_frames(old_h=0.99, new_h=0.02, old_cp=0.01, new_cp=0.04):
    old_atoms = (
        Atom(0, "C", (0.3, 0.5, 0.5)),
        Atom(1, "N", (0.4, 0.5, 0.5)),
        Atom(2, "H", (old_h, 0.5, 0.5)),
    )
    new_atoms = (
        Atom(0, "C", (0.3, 0.5, 0.5)),
        Atom(1, "N", (0.4, 0.5, 0.5)),
        Atom(2, "H", (new_h, 0.5, 0.5)),
    )
    old = Feature("old", "bond_critical_point", (old_cp, 0.5, 0.5), neighbor_atom_indices=(0, 2))
    new = Feature("new", "bond_critical_point", (new_cp, 0.5, 0.5), neighbor_atom_indices=(1, 2))
    return Frame(0, "00", CELL, old_atoms, (old,)), Frame(1, "01", CELL, new_atoms, (new,))


def evidence(previous, current, mapping, pbc=(True, True, True)):
    _, result = _feature_pair(
        previous.features[0],
        current.features[0],
        previous,
        current,
        mapping,
        FeatureMatchConfig(pbc=pbc),
        distance=0.3,
    )
    return result


def test_anchor_relative_motion_respects_periodicity_and_nonperiodicity():
    previous, current = make_frames()
    periodic = evidence(previous, current, {0: 0, 1: 1, 2: 2})["shared_neighbor_motion"]
    assert len(periodic) == 1 and periodic[0]["element"] == "H"
    assert np.isclose(periodic[0]["relative_displacement_angstrom"], 0.0, atol=1e-10)
    unwrapped = evidence(previous, current, {0: 0, 1: 1, 2: 2}, pbc=(False, True, True))[
        "shared_neighbor_motion"
    ]
    assert np.isclose(unwrapped[0]["relative_displacement_angstrom"], 10.0, atol=1e-10)


def test_missing_mapping_is_unavailable_not_zero():
    previous, current = make_frames()
    assert evidence(previous, current, {})["shared_neighbor_motion"] == []
    assert evidence(previous, current, {0: 0, 1: 1})["shared_neighbor_motion"] == []


def test_changing_cell_uses_strain_factored_metric():
    previous, current = make_frames(old_h=0.2, new_h=0.3, old_cp=0.22, new_cp=0.32)
    current = replace(current, cell=((12.0, 0.0, 0.0), (0.0, 12.0, 0.0), (0.0, 0.0, 12.0)))
    motion = evidence(previous, current, {0: 0, 1: 1, 2: 2})["shared_neighbor_motion"][0]
    assert np.isclose(motion["relative_displacement_angstrom"], 0.0, atol=1e-10)


def test_all_shared_neighbors_are_retained_and_atom_reindexing_works():
    previous, current = make_frames(old_h=0.2, new_h=0.3, old_cp=0.22, new_cp=0.32)
    atoms = (
        Atom(10, "C", (0.3, 0.5, 0.5)),
        Atom(11, "N", (0.4, 0.5, 0.5)),
        Atom(12, "H", (0.3, 0.5, 0.5)),
    )
    feature = replace(current.features[0], neighbor_atom_indices=(10, 12))
    current = replace(current, atoms=atoms, features=(feature,))
    motion = evidence(previous, current, {0: 10, 1: 11, 2: 12})["shared_neighbor_motion"]
    assert {(item["source_atom_index"], item["target_atom_index"]) for item in motion} == {
        (0, 10),
        (2, 12),
    }


def test_event_reports_species_counts_and_preserves_distinct_scores():
    previous, current = make_frames()
    feature_evidence = evidence(previous, current, {0: 0, 1: 1, 2: 2})
    feature_matches = MatchResult(
        0, 1, (MatchEdge(("old",), ("new",), 0.0, 0.35, feature_evidence),)
    )
    atom_matches = MatchResult(
        0,
        1,
        tuple(
            MatchEdge(
                (i,), (i,), 0.0, 0.19 if i == 2 else 1.0, {"distance": 0.83 if i == 2 else 0.0}
            )
            for i in range(3)
        ),
    )
    event = next(
        e
        for e in detect_transition_events(
            previous, current, feature_matches, atom_matches=atom_matches
        )
        if e.type == "connectivity_change"
    )
    hydrogen = next(item for item in event.evidence["atom_identity"] if item["element"] == "H")
    assert (hydrogen["same_element_count_before"], hydrogen["same_element_count_after"]) == (1, 1)
    assert hydrogen["displacement_angstrom"] == 0.83
    assert event.evidence["feature_match"]["confidence"] == 0.35
    assert event.confidence == 0.19
