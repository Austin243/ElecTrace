from electrace.events import detect_transition_events
from electrace.models import Atom, Feature, Frame, MatchEdge, MatchResult

CELL = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))


def frames_and_matches(before=(0, 2), after=(1, 2), atom_pairs=((0, 0), (1, 1), (2, 2))):
    atoms = (
        Atom(0, "C", (0.3, 0.5, 0.5)),
        Atom(1, "N", (0.4, 0.5, 0.5)),
        Atom(2, "H", (0.5, 0.5, 0.5)),
    )
    previous = Frame(
        2,
        "02",
        CELL,
        atoms,
        (Feature("old", "bond_critical_point", (0.5, 0.5, 0.5), neighbor_atom_indices=before),),
    )
    current = Frame(
        3,
        "03",
        CELL,
        atoms,
        (Feature("new", "bond_critical_point", (0.5, 0.5, 0.5), neighbor_atom_indices=after),),
    )
    features = MatchResult(2, 3, (MatchEdge(("old",), ("new",), 0.0, 0.8),))
    matches = MatchResult(2, 3, tuple(MatchEdge((a,), (b,), 0.0, 0.7) for a, b in atom_pairs))
    return previous, current, features, matches


def connectivity(previous, current, features, atoms):
    return [
        e
        for e in detect_transition_events(previous, current, features, atom_matches=atoms)
        if e.type == "connectivity_change"
    ]


def test_candidate_includes_both_neighbor_sets_and_actual_transition():
    events = connectivity(*frames_and_matches())
    assert len(events) == 1
    e = events[0]
    assert (e.frame_before, e.frame_after) == (2, 3)
    assert e.evidence["neighbors_before"] == [
        {"index": 0, "element": "C"},
        {"index": 2, "element": "H"},
    ]
    assert e.evidence["neighbors_after"] == [
        {"index": 1, "element": "N"},
        {"index": 2, "element": "H"},
    ]
    assert e.confidence <= 0.7


def test_atom_renumbering_is_not_a_change():
    old_atoms = (
        Atom(0, "C", (0.3, 0.5, 0.5)),
        Atom(1, "N", (0.4, 0.5, 0.5)),
        Atom(2, "H", (0.5, 0.5, 0.5)),
    )
    new_atoms = (
        Atom(10, "C", (0.3, 0.5, 0.5)),
        Atom(11, "N", (0.4, 0.5, 0.5)),
        Atom(12, "H", (0.5, 0.5, 0.5)),
    )
    p = Frame(
        0,
        "00",
        CELL,
        old_atoms,
        (Feature("a", "bond_critical_point", (0.5, 0.5, 0.5), neighbor_atom_indices=(0, 2)),),
    )
    c = Frame(
        1,
        "01",
        CELL,
        new_atoms,
        (Feature("b", "bond_critical_point", (0.5, 0.5, 0.5), neighbor_atom_indices=(10, 12)),),
    )
    feats = MatchResult(0, 1, (MatchEdge(("a",), ("b",), 0, 1),))
    atoms = MatchResult(
        0, 1, tuple(MatchEdge((a,), (b,), 0, 1) for a, b in ((0, 10), (1, 11), (2, 12)))
    )
    assert connectivity(p, c, feats, atoms) == []


def test_incomplete_mapping_or_missing_neighbors_does_not_assert_change():
    assert connectivity(*frames_and_matches(atom_pairs=((0, 0), (2, 2)))) == []
    assert connectivity(*frames_and_matches(before=())) == []
    assert connectivity(*frames_and_matches(after=())) == []


def test_same_element_neighbor_replacement_is_still_a_change():
    atoms = (
        Atom(0, "C", (0.3, 0.5, 0.5)),
        Atom(1, "C", (0.4, 0.5, 0.5)),
        Atom(2, "H", (0.5, 0.5, 0.5)),
    )
    p = Frame(
        0,
        "00",
        CELL,
        atoms,
        (Feature("a", "bond_critical_point", (0.5, 0.5, 0.5), neighbor_atom_indices=(0, 2)),),
    )
    c = Frame(
        1,
        "01",
        CELL,
        atoms,
        (Feature("b", "bond_critical_point", (0.5, 0.5, 0.5), neighbor_atom_indices=(1, 2)),),
    )
    feats = MatchResult(0, 1, (MatchEdge(("a",), ("b",), 0, 1),))
    mapping = MatchResult(0, 1, tuple(MatchEdge((i,), (i,), 0, 1) for i in range(3)))
    assert len(connectivity(p, c, feats, mapping)) == 1
