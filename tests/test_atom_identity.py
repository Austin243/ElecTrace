from __future__ import annotations

from pathlib import Path

import pytest

from electrace.config import load_config, write_default_config
from electrace.events import detect_transition_events
from electrace.matching import AtomMatchConfig, match_atoms, match_features
from electrace.models import Atom, Feature, Frame
from electrace.pipeline import _matching_configs

CELL = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))
INDEX = AtomMatchConfig(identity="index")


def frame(index: int, atoms: tuple[Atom, ...], features: tuple[Feature, ...] = ()) -> Frame:
    return Frame(index, f"{index:02d}", CELL, atoms, features)


def test_index_identity_keeps_file_order_where_geometry_disagrees() -> None:
    previous = frame(
        0,
        (
            Atom(0, "H", (0.40, 0.5, 0.5)),
            Atom(1, "H", (0.45, 0.5, 0.5)),
            Atom(2, "C", (0.99, 0.5, 0.5)),
            Atom(3, "N", (0.20, 0.2, 0.2)),
        ),
    )
    # The H atoms pass each other, C wraps across the cell boundary, and N moves
    # beyond the geometric cutoff. Atom indices are renumbered.
    current = frame(
        1,
        (
            Atom(10, "H", (0.46, 0.5, 0.5)),
            Atom(11, "H", (0.39, 0.5, 0.5)),
            Atom(12, "C", (0.01, 0.5, 0.5)),
            Atom(13, "N", (0.45, 0.2, 0.2)),
        ),
    )

    geometric = match_atoms(previous, current)
    indexed = match_atoms(previous, current, INDEX)

    assert geometric.mapping == {0: 11, 1: 10, 2: 12}
    assert geometric.unmatched_previous == (3,)
    assert indexed.mapping == {0: 10, 1: 11, 2: 12, 3: 13}
    assert not indexed.unmatched_previous and not indexed.unmatched_current
    assert [edge.confidence for edge in indexed.edges] == [1.0] * 4
    assert [edge.evidence["distance"] for edge in indexed.edges] == pytest.approx(
        [0.6, 0.6, 0.2, 2.5]
    )
    assert {edge.evidence["identity"] for edge in indexed.edges} == {"index"}
    assert indexed.total_cost == 0.0


@pytest.mark.parametrize(
    ("elements", "message"),
    [
        (("C", "H", "N"), "atom 1 is N in frame '00' and H in frame '01'"),
        (("C", "N", "H", "H"), "3 and 4 atoms"),
    ],
)
def test_index_identity_rejects_different_element_sequences(
    elements: tuple[str, ...], message: str
) -> None:
    previous = frame(
        0, tuple(Atom(i, e, (0.1 * i, 0.5, 0.5)) for i, e in enumerate(("C", "N", "H")))
    )
    current = frame(1, tuple(Atom(i, e, (0.1 * i, 0.5, 0.5)) for i, e in enumerate(elements)))

    with pytest.raises(ValueError, match=message):
        match_atoms(previous, current, INDEX)


def test_atom_identity_reaches_matching_and_rejects_unknown_modes(tmp_path: Path) -> None:
    default = load_config(write_default_config(tmp_path / "default.toml"))
    path = tmp_path / "index.toml"
    path.write_text('[matching]\natom_identity = "Index"\n', encoding="utf-8")

    assert _matching_configs(default)[0].identity == "geometric"
    assert _matching_configs(load_config(path))[0].identity == "index"
    path.write_text('[matching]\natom_identity = "order"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="atom_identity must be one of geometric, index"):
        load_config(path)
    with pytest.raises(ValueError, match="identity"):
        AtomMatchConfig(identity="order")


def test_index_identity_keeps_displacement_out_of_connectivity_confidence() -> None:
    previous = frame(
        2,
        (
            Atom(0, "C", (0.46, 0.5, 0.5)),
            Atom(1, "N", (0.54, 0.5, 0.5)),
            Atom(2, "H", (0.45, 0.56, 0.5)),
        ),
        (Feature("cp", "bond_critical_point", (0.455, 0.53, 0.5), neighbor_atom_indices=(0, 2)),),
    )
    current = frame(
        3,
        (
            Atom(0, "C", (0.46, 0.5, 0.5)),
            Atom(1, "N", (0.54, 0.5, 0.5)),
            Atom(2, "H", (0.50, 0.57, 0.5)),
        ),
        (Feature("cp", "bond_critical_point", (0.52, 0.535, 0.5), neighbor_atom_indices=(1, 2)),),
    )

    def connectivity(config: AtomMatchConfig) -> tuple[float, float, dict[str, object]]:
        atoms = match_atoms(previous, current, config)
        features = match_features(previous, current, atoms)
        event = next(
            item
            for item in detect_transition_events(previous, current, features, atom_matches=atoms)
            if item.type == "connectivity_change"
        )
        hydrogen = next(item for item in event.evidence["atom_identity"] if item["element"] == "H")
        return event.confidence, features.edges[0].confidence, hydrogen

    geometric, geometric_feature, _ = connectivity(AtomMatchConfig())
    indexed, indexed_feature, hydrogen = connectivity(INDEX)

    assert geometric < geometric_feature
    assert indexed == indexed_feature == pytest.approx(geometric_feature)
    assert hydrogen["displacement_angstrom"] == pytest.approx(0.26**0.5)
    assert hydrogen["motion_similarity"] is None
