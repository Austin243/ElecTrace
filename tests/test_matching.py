from __future__ import annotations

import numpy as np
import pytest

from electrace.matching import (
    FeatureMatchConfig,
    _assignment_ambiguities,
    _hungarian_fallback,
    match_atoms,
    match_features,
    solve_assignment,
)
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
    costs = np.array([[4.0, 1.0, 3.0], [2.0, 0.0, 5.0]])
    rows, columns = _hungarian_fallback(costs)
    assert list(rows) == [0, 1]
    assert list(columns) == [1, 0]
    scipy_rows, scipy_columns = solve_assignment(costs)
    assert costs[rows, columns].sum() == costs[scipy_rows, scipy_columns].sum()


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan")])
def test_conservation_tolerance_is_a_probability_like_fraction(value: float) -> None:
    with pytest.raises(ValueError, match="conservation_tolerance"):
        FeatureMatchConfig(conservation_tolerance=value)


def test_local_tie_is_not_reported_when_global_assignment_resolves_it() -> None:
    costs = np.array([[0.0, 0.0], [0.0, 10.0]])

    ambiguities, ambiguous_pairs = _assignment_ambiguities(
        costs,
        100.0,
        [(0, 1), (1, 0)],
        set(),
        set(),
        0.0,
        ["source-a", "source-b"],
        ["target-a", "target-b"],
        0.05,
    )

    assert ambiguities == []
    assert ambiguous_pairs == {}


def test_exact_feature_tie_is_auditable_and_reorder_safe() -> None:
    previous = frame(
        0,
        features=(Feature("source", "bond_critical_point", (0.5, 0.5, 0.5)),),
    )
    targets = (
        Feature("a", "bond_critical_point", (0.5, 0.5, 0.5)),
        Feature("b", "bond_critical_point", (0.5, 0.5, 0.5)),
    )

    results = [
        match_features(previous, frame(1, features=ordered))
        for ordered in (targets, tuple(reversed(targets)))
    ]

    assert all(result.mapping == {} for result in results)
    assert all(result.diagnostics["ambiguous"] is True for result in results)
    assert all(result.edges[0].ambiguous for result in results)
    assert all(
        {
            str(item)
            for record in result.diagnostics["ambiguities"]
            for item in record["selected"].values()
        }
        == {"source", "a", "b"}
        for result in results
    )


def test_near_tie_uses_configured_complete_assignment_gap() -> None:
    previous = frame(
        0,
        features=(Feature("source", "bond_critical_point", (0.5, 0.5, 0.5)),),
    )
    current = frame(
        1,
        features=(
            Feature("best", "bond_critical_point", (0.5, 0.5, 0.5)),
            Feature("near", "bond_critical_point", (0.5001, 0.5, 0.5)),
        ),
    )

    ambiguous = match_features(
        previous,
        current,
        config=FeatureMatchConfig(ambiguity_tolerance=0.002),
    )
    separated = match_features(
        previous,
        current,
        config=FeatureMatchConfig(ambiguity_tolerance=0.001),
    )

    assert ambiguous.diagnostics["ambiguous"] is True
    assert ambiguous.diagnostics["ambiguities"][0]["assignment_cost_gap"] == pytest.approx(
        0.0013333333333333333
    )
    assert separated.diagnostics["ambiguous"] is False
    assert separated.mapping == {"source": "best"}


def test_exact_atom_tie_is_excluded_from_unambiguous_mapping() -> None:
    previous = frame(
        0,
        atoms=(
            Atom(0, "C", (0.5, 0.5, 0.5)),
            Atom(1, "C", (0.5, 0.5, 0.5)),
        ),
    )
    targets = (
        Atom(10, "C", (0.5, 0.5, 0.5)),
        Atom(11, "C", (0.5, 0.5, 0.5)),
    )

    results = [
        match_atoms(previous, frame(1, atoms=ordered))
        for ordered in (targets, tuple(reversed(targets)))
    ]

    assert all(result.mapping == {} for result in results)
    assert all(result.diagnostics["ambiguous"] is True for result in results)
    assert all(len(result.diagnostics["ambiguities"]) == 2 for result in results)
    assert all(edge.ambiguous for result in results for edge in result.edges)


def test_symmetric_backbone_tie_is_resolved_by_unique_conserving_hyperedge() -> None:
    previous = frame(
        0,
        features=(Feature("parent", "elf_basin", (0.5, 0.5, 0.5), volume=10.0),),
    )
    current = frame(
        1,
        features=(
            Feature("left", "elf_basin", (0.48, 0.5, 0.5), volume=5.0),
            Feature("right", "elf_basin", (0.52, 0.5, 0.5), volume=5.0),
        ),
    )

    result = match_features(previous, current)

    assert result.diagnostics["ambiguous"] is False
    assert result.diagnostics["ambiguities"] == []
    assert result.edges[0].relation is MatchRelation.SPLIT
    assert len(result.edges[0].evidence["resolved_assignment_ambiguities"]) == 2


def test_atom_matching_is_periodic_element_aware_and_variable_cell_safe() -> None:
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
    assert all(edge.evidence["element_match"] for edge in result.edges)


def test_atom_matching_reuses_one_pairwise_geometry_factorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    atoms = tuple(Atom(index, "C", (index / 20.0, 0.2, 0.3)) for index in range(8))
    shifted = tuple(
        Atom(index + 20, "C", ((index / 20.0 + 0.01) % 1.0, 0.2, 0.3)) for index in range(8)
    )
    real_svd = np.linalg.svd
    calls = 0

    def counted_svd(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real_svd(*args, **kwargs)

    monkeypatch.setattr(np.linalg, "svd", counted_svd)
    result = match_atoms(frame(0, atoms=atoms), frame(1, atoms=shifted))

    assert len(result.edges) == len(atoms)
    # Two endpoint validations plus one validation of the mean-metric cell;
    # the count is independent of the 8x8 candidate-pair count.
    assert calls == 3


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
    atoms = match_atoms(previous, current)
    result = match_features(previous, current, atoms)
    assert result.mapping == {"old": "new"}
    assert result.edges[0].evidence["neighbor_similarity"] == 1.0


def test_invalid_grid_shape_iterables_are_consumed_boundedly() -> None:
    grid_shape = iter(range(1, 100))
    previous = frame(
        0,
        features=(
            Feature(
                "old",
                "elf_basin",
                (0.5, 0.5, 0.5),
                attributes={"grid_shape": grid_shape},
                voxel_indices=((0, 0, 0),),
            ),
        ),
    )
    current = frame(
        1,
        features=(
            Feature(
                "new",
                "elf_basin",
                (0.5, 0.5, 0.5),
                attributes={"grid_shape": (4, 4, 4)},
                voxel_indices=((0, 0, 0),),
            ),
        ),
    )

    result = match_features(previous, current)

    assert result.mapping == {"old": "new"}
    assert "voxel_similarity" not in result.edges[0].evidence
    assert next(grid_shape) == 5


def test_split_is_promoted_to_a_conservation_backed_hyperedge() -> None:
    previous = frame(
        0,
        features=(Feature("parent", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0),),
    )
    current = frame(
        1,
        features=(
            Feature("left", "elf_basin", (0.48, 0.5, 0.5), 0.4, 4.0),
            Feature("right", "elf_basin", (0.52, 0.5, 0.5), 0.6, 6.0),
        ),
    )
    result = match_features(previous, current)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.relation is MatchRelation.SPLIT
    assert edge.source_ids == ("parent",)
    assert set(edge.target_ids) == {"left", "right"}
    assert edge.evidence["conservation"]["volume_relative_error"] == pytest.approx(0)
    assert edge.confidence > 0.5
    assert not result.unmatched_current


def test_merge_is_promoted_to_a_conservation_backed_hyperedge() -> None:
    previous = frame(
        0,
        features=(
            Feature("left", "elf_basin", (0.48, 0.5, 0.5), 0.4, 4.0),
            Feature("right", "elf_basin", (0.52, 0.5, 0.5), 0.6, 6.0),
        ),
    )
    current = frame(
        1,
        features=(Feature("child", "elf_basin", (0.5, 0.5, 0.5), 1.0, 10.0),),
    )
    result = match_features(previous, current)
    assert result.edges[0].relation is MatchRelation.MERGE
    assert set(result.edges[0].source_ids) == {"left", "right"}
    assert result.edges[0].target_ids == ("child",)


@pytest.mark.parametrize("relation", ["split", "merge"])
@pytest.mark.parametrize(
    ("case", "parent_values", "child_values", "accepted", "available", "failed"),
    [
        ("no evidence", {}, ({}, {}), False, (), ()),
        (
            "partial volume",
            {"volume": 10.0},
            ({"volume": 4.0}, {}),
            False,
            (),
            (),
        ),
        (
            "charge only",
            {"charge": 1.0},
            ({"charge": 0.4}, {"charge": 0.6}),
            True,
            ("charge",),
            (),
        ),
        (
            "volume only",
            {"volume": 10.0},
            ({"volume": 4.0}, {"volume": 6.0}),
            True,
            ("volume",),
            (),
        ),
        (
            "charge and volume",
            {"charge": 1.0, "volume": 10.0},
            (
                {"charge": 0.4, "volume": 4.0},
                {"charge": 0.6, "volume": 6.0},
            ),
            True,
            ("charge", "volume"),
            (),
        ),
        (
            "tolerance boundary",
            {"volume": 10.0},
            ({"volume": 3.0}, {"volume": 3.5}),
            True,
            ("volume",),
            (),
        ),
        (
            "one failed channel",
            {"charge": 1.0, "volume": 10.0},
            (
                {"charge": 0.4, "volume": 2.0},
                {"charge": 0.6, "volume": 2.0},
            ),
            False,
            ("charge", "volume"),
            ("volume",),
        ),
    ],
)
def test_hyperedges_require_a_complete_conserving_channel(
    relation: str,
    case: str,
    parent_values: dict[str, float],
    child_values: tuple[dict[str, float], dict[str, float]],
    accepted: bool,
    available: tuple[str, ...],
    failed: tuple[str, ...],
) -> None:
    parent = Feature("parent", "elf_basin", (0.5, 0.5, 0.5), **parent_values)
    children = (
        Feature("left", "elf_basin", (0.48, 0.5, 0.5), **child_values[0]),
        Feature("right", "elf_basin", (0.53, 0.5, 0.5), **child_values[1]),
    )
    previous_features = (parent,) if relation == "split" else children
    current_features = children if relation == "split" else (parent,)

    result = match_features(
        frame(0, features=previous_features),
        frame(1, features=current_features),
        config=FeatureMatchConfig(ambiguity_tolerance=0.0),
    )

    hyperedges = [edge for edge in result.edges if edge.relation is not MatchRelation.ONE_TO_ONE]
    if accepted:
        assert len(hyperedges) == 1, case
        conservation = hyperedges[0].evidence["conservation"]
        assert tuple(conservation["available_channels"]) == available
        assert tuple(conservation["failed_channels"]) == failed
        assert not result.diagnostics["hyperedge_rejections"]
    else:
        assert not hyperedges, case
        rejection = result.diagnostics["hyperedge_rejections"][0]
        assert rejection["relation"] == relation
        conservation = rejection["conservation"]
        assert tuple(conservation["available_channels"]) == available
        assert tuple(conservation["failed_channels"]) == failed
        expected_reason = (
            "missing_complete_conservation_channel"
            if not available
            else "conservation_tolerance_exceeded"
        )
        assert rejection["reason"] == expected_reason


@pytest.mark.parametrize("relation", ["split", "merge"])
@pytest.mark.parametrize("with_volume", [False, True])
def test_hyperedge_charge_conservation_requires_compatible_semantics(
    relation: str,
    with_volume: bool,
) -> None:
    parent = Feature(
        "parent",
        "elf_basin",
        (0.5, 0.5, 0.5),
        charge=1.0,
        volume=10.0 if with_volume else None,
    )
    children = (
        Feature(
            "left",
            "elf_basin",
            (0.48, 0.5, 0.5),
            charge=0.4,
            volume=4.0 if with_volume else None,
            attributes={"charge_convention": "electron population"},
        ),
        Feature(
            "right",
            "elf_basin",
            (0.52, 0.5, 0.5),
            charge=0.6,
            volume=6.0 if with_volume else None,
            attributes={"charge_convention": "signed net charge"},
        ),
    )
    previous_features = (parent,) if relation == "split" else children
    current_features = children if relation == "split" else (parent,)

    result = match_features(
        frame(0, features=previous_features),
        frame(1, features=current_features),
    )

    hyperedges = [edge for edge in result.edges if edge.relation is not MatchRelation.ONE_TO_ONE]
    if with_volume:
        assert len(hyperedges) == 1
        conservation = hyperedges[0].evidence["conservation"]
        assert conservation["available_channels"] == ["volume"]
        assert conservation["incompatible_channels"] == ["charge"]
    else:
        assert not hyperedges
        rejection = result.diagnostics["hyperedge_rejections"][0]
        assert rejection["reason"] == "incompatible_conservation_semantics"
        assert rejection["conservation"]["incompatible_channels"] == ["charge"]


def test_conservation_backed_split_is_periodic_and_input_order_invariant() -> None:
    parent = Feature("parent", "elf_basin", (0.99, 0.5, 0.5), volume=10.0)
    left = Feature("left", "elf_basin", (0.97, 0.5, 0.5), volume=4.0)
    wrapped = Feature("wrapped", "elf_basin", (0.01, 0.5, 0.5), volume=6.0)

    results = [
        match_features(
            frame(0, features=(parent,)),
            frame(1, features=children),
        )
        for children in ((left, wrapped), (wrapped, left))
    ]

    assert all(result.edges[0].relation is MatchRelation.SPLIT for result in results)
    assert all(set(result.edges[0].target_ids) == {"left", "wrapped"} for result in results)
    assert results[0].edges[0].confidence == pytest.approx(results[1].edges[0].confidence)


def test_unknown_kinds_are_allowed_but_do_not_cross_match_by_default() -> None:
    previous = frame(0, features=(Feature("a", "plugin_custom", (0.2, 0.2, 0.2)),))
    current = frame(1, features=(Feature("b", "other_custom", (0.2, 0.2, 0.2)),))
    result = match_features(previous, current)
    assert result.unmatched_previous == ("a",)
    assert result.unmatched_current == ("b",)
    permissive = match_features(
        previous,
        current,
        config=FeatureMatchConfig(allow_kind_mismatch=True),
    )
    # Permissive matching makes the mismatch finite and explicitly maps it.
    assert permissive.mapping == {"a": "b"}


def test_configured_descriptor_scales_disambiguate_feature_identity() -> None:
    previous = frame(
        0,
        features=(
            Feature(
                "source",
                "bond_critical_point",
                (0.5, 0.5, 0.5),
                attributes={"icohp": -1.0},
            ),
        ),
    )
    current = frame(
        1,
        features=(
            Feature(
                "wrong",
                "bond_critical_point",
                (0.5, 0.5, 0.5),
                attributes={"icohp": 1.0},
            ),
            Feature(
                "right",
                "bond_critical_point",
                (0.5, 0.5, 0.5),
                attributes={"icohp": -1.0},
            ),
        ),
    )
    result = match_features(
        previous,
        current,
        config=FeatureMatchConfig(
            descriptor_scales={"icohp": 0.5},
            descriptor_weight=1.0,
            detect_splits_merges=False,
        ),
    )

    assert result.mapping == {"source": "right"}
    assert result.edges[0].evidence["descriptor_normalized_deltas"] == {"icohp": 0.0}


def test_incompatible_lobster_families_cannot_cross_match_when_colocated() -> None:
    previous = frame(
        0,
        features=(
            Feature(
                "p-hp",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={
                    "descriptor_family": "ICOHP",
                    "descriptor_producer": "LOBSTER",
                    "icohp": -1.0,
                },
            ),
            Feature(
                "p-bi",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={
                    "descriptor_family": "ICOBI",
                    "descriptor_producer": "LOBSTER",
                    "icobi": 0.2,
                },
            ),
        ),
    )
    # Put each incompatible family first.  The former missing-key behavior
    # assigned both cross-family pairs at zero cost.
    current = frame(
        1,
        features=(
            Feature(
                "q-bi",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={
                    "descriptor_family": "icobi",
                    "descriptor_producer": "lobster",
                    "icobi": 0.3,
                },
            ),
            Feature(
                "q-hp",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={
                    "descriptor_family": "icohp",
                    "descriptor_producer": "lobster",
                    "icohp": -1.5,
                },
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
    assert all(edge.evidence["semantic_compatibility"]["compatible"] for edge in result.edges)


def test_multifamily_descriptor_declaration_matches_on_shared_family() -> None:
    result = match_features(
        frame(
            0,
            features=(
                Feature(
                    "multi",
                    "bond_descriptor",
                    (0.5, 0.5, 0.5),
                    attributes={
                        "descriptor_family": "ICOHP+ICOBI",
                        "descriptor_producer": "LOBSTER",
                        "icohp": -1.0,
                        "icobi": 0.2,
                    },
                ),
            ),
        ),
        frame(
            1,
            features=(
                Feature(
                    "bi",
                    "bond_descriptor",
                    (0.5, 0.5, 0.5),
                    attributes={
                        "descriptor_family": "icobi",
                        "descriptor_producer": "lobster",
                        "icobi": 0.3,
                    },
                ),
            ),
        ),
        config=FeatureMatchConfig(descriptor_scales={"icohp": 0.5, "icobi": 0.1}),
    )

    assert result.mapping == {"multi": "bi"}
    assert result.diagnostics["semantic_rejections"] == 0


def test_disjoint_configured_key_families_gate_untagged_legacy_features() -> None:
    previous = frame(
        0,
        features=(
            Feature(
                "p-hp",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={"icohp_ev": -1.0},
            ),
            Feature(
                "p-bi",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={"icobi": 0.2},
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
                attributes={"icobi": 0.3},
            ),
            Feature(
                "q-hp",
                "bond_descriptor",
                (0.5, 0.5, 0.5),
                attributes={"icohp": -1.5},
            ),
        ),
    )

    result = match_features(
        previous,
        current,
        config=FeatureMatchConfig(
            descriptor_scales={"icohp": 0.5, "icohp_ev": 0.5, "icobi": 0.1},
            descriptor_weight=1.0,
            detect_splits_merges=False,
        ),
    )

    assert result.mapping == {"p-hp": "q-hp", "p-bi": "q-bi"}


def test_incompatible_charge_definitions_are_forbidden() -> None:
    previous = frame(
        0,
        features=(
            Feature(
                "ddec",
                "atomic_charge",
                (0.5, 0.5, 0.5),
                charge=0.2,
                attributes={"charge_definition": "DDEC net atomic charge"},
            ),
        ),
    )
    current = frame(
        1,
        features=(
            Feature(
                "bader",
                "atomic_charge",
                (0.5, 0.5, 0.5),
                charge=0.2,
                attributes={"charge_definition": "bader-electron-population"},
            ),
        ),
    )

    result = match_features(previous, current)

    assert not result.edges
    assert result.unmatched_previous == ("ddec",)
    assert result.unmatched_current == ("bader",)
    assert result.diagnostics["semantic_rejections"] == 1


def test_incompatible_charge_conventions_are_gated_only_for_charged_features() -> None:
    charged = match_features(
        frame(
            0,
            features=(
                Feature(
                    "population",
                    "atomic_basin",
                    (0.5, 0.5, 0.5),
                    charge=1.2,
                    attributes={
                        "descriptor_producer": "same-tool",
                        "charge_convention": "integrated electron population",
                    },
                ),
            ),
        ),
        frame(
            1,
            features=(
                Feature(
                    "net-charge",
                    "atomic_basin",
                    (0.5, 0.5, 0.5),
                    charge=1.2,
                    attributes={
                        "descriptor_producer": "same-tool",
                        "charge_convention": "signed net atomic charge",
                    },
                ),
            ),
        ),
    )
    uncharged = match_features(
        frame(
            0,
            features=(
                Feature(
                    "bcp-a",
                    "bond_critical_point",
                    (0.5, 0.5, 0.5),
                    attributes={
                        "descriptor_producer": "same-tool",
                        "charge_convention": "integrated electron population",
                    },
                ),
            ),
        ),
        frame(
            1,
            features=(
                Feature(
                    "bcp-b",
                    "bond_critical_point",
                    (0.5, 0.5, 0.5),
                    attributes={
                        "descriptor_producer": "same-tool",
                        "charge_convention": "signed net atomic charge",
                    },
                ),
            ),
        ),
    )

    assert not charged.edges
    assert charged.diagnostics["semantic_rejections"] == 1
    assert uncharged.mapping == {"bcp-a": "bcp-b"}


def test_incompatible_declared_descriptor_producers_are_forbidden() -> None:
    previous = frame(
        0,
        features=(
            Feature(
                "critic",
                "bond_critical_point",
                (0.5, 0.5, 0.5),
                attributes={"descriptor_producer": "Critic2"},
            ),
        ),
    )
    current = frame(
        1,
        features=(
            Feature(
                "other",
                "bond_critical_point",
                (0.5, 0.5, 0.5),
                attributes={"descriptor_producer": "different-qtaim"},
            ),
        ),
    )

    result = match_features(previous, current)

    assert not result.edges
    assert result.unmatched_previous == ("critic",)
    assert result.unmatched_current == ("other",)


def test_missing_optional_semantic_evidence_does_not_forbid_matching() -> None:
    descriptor_result = match_features(
        frame(
            0,
            features=(
                Feature(
                    "old",
                    "bond_descriptor",
                    (0.5, 0.5, 0.5),
                    attributes={"icohp": -1.0, "descriptor_producer": "lobster"},
                ),
            ),
        ),
        frame(
            1,
            features=(Feature("new", "bond_descriptor", (0.5, 0.5, 0.5)),),
        ),
        config=FeatureMatchConfig(descriptor_scales={"icohp": 0.5}),
    )
    charge_result = match_features(
        frame(
            0,
            features=(
                Feature(
                    "old-charge",
                    "atomic_charge",
                    (0.5, 0.5, 0.5),
                    charge=0.1,
                    attributes={"charge_definition": "ddec_net_atomic_charge"},
                ),
            ),
        ),
        frame(
            1,
            features=(Feature("new-charge", "atomic_charge", (0.5, 0.5, 0.5), charge=0.1),),
        ),
    )

    assert descriptor_result.mapping == {"old": "new"}
    assert charge_result.mapping == {"old-charge": "new-charge"}


def test_models_round_trip_through_json_compatible_dicts() -> None:
    original = frame(
        7,
        atoms=(Atom(0, "H", (0.1, 0.2, 0.3), {"tag": np.int64(2)}),),
        features=(Feature("f", "nna", (0.9, 0.2, 0.3), -0.2, 1.1, (0,)),),
    )
    restored = Frame.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()
