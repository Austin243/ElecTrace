from __future__ import annotations

import numpy as np
import pytest

from electrace.matching import FeatureMatchConfig, _hungarian_fallback, match_atoms, match_features
from electrace.models import Atom, Feature, Frame, MatchRelation

CELL = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))


def frame(
    index: int,
    *,
    atoms: tuple[Atom, ...] = (),
    features: tuple[Feature, ...] = (),
    cell: tuple[tuple[float, float, float], ...] = CELL,
) -> Frame:
    return Frame(index, str(index), cell, atoms, features)


def test_hungarian_fallback_finds_global_rectangular_optimum() -> None:
    rows, columns = _hungarian_fallback(np.array([[4.0, 1.0, 3.0], [2.0, 0.0, 5.0]]))
    assert list(rows) == [0, 1]
    assert list(columns) == [1, 0]


def test_atom_matching_is_periodic_element_aware_across_changing_cells() -> None:
    previous = frame(
        0,
        atoms=(
            Atom(10, "C", (0.98, 0.1, 0.1)),
            Atom(20, "N", (0.40, 0.4, 0.4)),
        ),
    )
    current = frame(
        1,
        atoms=(
            Atom(2, "N", (0.41, 0.4, 0.4)),
            Atom(1, "C", (0.02, 0.1, 0.1)),
        ),
        cell=((10.2, 0.0, 0.0), (0.0, 10.2, 0.0), (0.0, 0.0, 10.2)),
    )

    result = match_atoms(previous, current)

    assert result.mapping == {10: 1, 20: 2}
    assert not result.unmatched_previous


def test_feature_matching_uses_atom_neighbor_mapping() -> None:
    previous = frame(
        0,
        atoms=(Atom(0, "C", (0.2, 0.2, 0.2)), Atom(1, "N", (0.8, 0.2, 0.2))),
        features=(Feature("old", "elf_basin", (0.25, 0.2, 0.2), 1.0, 2.0, (0,)),),
    )
    current = frame(
        1,
        atoms=(Atom(10, "N", (0.8, 0.2, 0.2)), Atom(11, "C", (0.22, 0.2, 0.2))),
        features=(Feature("new", "elf_basin", (0.27, 0.2, 0.2), 1.02, 2.05, (11,)),),
    )

    result = match_features(previous, current, match_atoms(previous, current))

    assert result.mapping == {"old": "new"}
    assert result.edges[0].evidence["neighbor_similarity"] == 1.0


def test_split_requires_complete_conservation_evidence() -> None:
    previous = frame(
        0,
        features=(Feature("parent", "elf_basin", (0.5, 0.5, 0.5), volume=10.0),),
    )
    conserving = frame(
        1,
        features=(
            Feature("left", "elf_basin", (0.48, 0.5, 0.5), volume=4.0),
            Feature("right", "elf_basin", (0.52, 0.5, 0.5), volume=6.0),
        ),
    )
    missing = frame(
        1,
        features=(
            Feature("left", "elf_basin", (0.48, 0.5, 0.5)),
            Feature("right", "elf_basin", (0.52, 0.5, 0.5)),
        ),
    )

    accepted = match_features(previous, conserving)
    rejected = match_features(previous, missing)

    assert accepted.edges[0].relation is MatchRelation.SPLIT
    assert accepted.edges[0].evidence["conservation"]["volume_relative_error"] == 0.0
    assert all(edge.relation is MatchRelation.ONE_TO_ONE for edge in rejected.edges)
    assert rejected.diagnostics["hyperedge_rejections"][0]["reason"] == (
        "missing_complete_conservation_channel"
    )


def test_incompatible_lobster_families_do_not_cross_match() -> None:
    previous = frame(
        0,
        features=(
            Feature(
                "p-hp",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={"descriptor_family": "ICOHP", "icohp": -1.0},
            ),
            Feature(
                "p-bi",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={"descriptor_family": "ICOBI", "icobi": 0.2},
            ),
        ),
    )
    current = frame(
        1,
        features=(
            Feature(
                "q-bi",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={"descriptor_family": "icobi", "icobi": 0.3},
            ),
            Feature(
                "q-hp",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={"descriptor_family": "icohp", "icohp": -1.5},
            ),
        ),
    )

    result = match_features(
        previous,
        current,
        config=FeatureMatchConfig(
            descriptor_scales={"icohp": 0.5, "icobi": 0.1},
            descriptor_weight=1.0,
            detect_splits_merges=False,
        ),
    )

    assert result.mapping == {"p-hp": "q-hp", "p-bi": "q-bi"}
    assert result.diagnostics["semantic_rejections"] == 2


@pytest.mark.parametrize(
    ("key", "left", "right"),
    [
        ("descriptor_producer", "critic2", "badelf"),
        ("charge_definition", "bader", "ddec6"),
        ("charge_convention", "electron_count", "net_atomic_charge"),
    ],
)
def test_explicit_semantic_conflicts_prevent_matching(key: str, left: str, right: str) -> None:
    previous = frame(
        0, features=(Feature("p", "basin", (0.5, 0.5, 0.5), 1.0, attributes={key: left}),)
    )
    current = frame(
        1, features=(Feature("q", "basin", (0.5, 0.5, 0.5), 1.0, attributes={key: right}),)
    )

    assert not match_features(previous, current).edges


def test_frame_models_round_trip() -> None:
    original = frame(
        7,
        atoms=(Atom(0, "H", (0.1, 0.2, 0.3), {"tag": np.int64(2)}),),
        features=(Feature("f", "nna", (0.9, 0.2, 0.3), -0.2, 1.1, (0,)),),
    )
    assert Frame.from_dict(original.to_dict()).to_dict() == original.to_dict()
