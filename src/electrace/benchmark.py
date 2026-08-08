"""Deterministic synthetic regression benchmark for ElecTrace correspondence.

This suite exercises the production matchers and transition event detector on
small, labeled constructions.  It is a software/scientific-assumption regression
benchmark, not validation against independent electronic-structure calculations.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .events import EventDetectionConfig, detect_transition_events
from .export import write_json
from .matching import AtomMatchConfig, FeatureMatchConfig, match_atoms, match_features
from .models import Atom, Event, Feature, Frame, Identifier, MatchResult, Matrix3

BENCHMARK_SCHEMA_VERSION = "electrace.benchmark.v0.1"
BENCHMARK_SUITE_VERSION = "0.1.0"

_CELL: Matrix3 = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))
_DEFORMED_CELL: Matrix3 = ((12.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 10.0))


@dataclass(frozen=True, slots=True)
class EventLabel:
    """Identity-only event label used for exact synthetic scoring."""

    type: str
    source_ids: tuple[str, ...] = ()
    target_ids: tuple[str, ...] = ()

    def key(self) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        return (self.type, tuple(sorted(self.source_ids)), tuple(sorted(self.target_ids)))

    def to_dict(self) -> dict[str, Any]:
        event_type, source_ids, target_ids = self.key()
        return {
            "type": event_type,
            "source_ids": list(source_ids),
            "target_ids": list(target_ids),
        }


@dataclass(frozen=True, slots=True)
class SyntheticCase:
    """One controlled two-frame perturbation and its exact expected outcome."""

    id: str
    category: str
    description: str
    perturbation: str
    previous: Frame
    current: Frame
    events: tuple[EventLabel, ...] = ()
    atom_mapping: tuple[tuple[int, int], ...] = ()
    feature_mapping: tuple[tuple[str, str], ...] = ()
    atom_ambiguous: bool = False
    feature_ambiguous: bool = False
    atom_config: AtomMatchConfig = field(default_factory=AtomMatchConfig)
    feature_config: FeatureMatchConfig = field(default_factory=FeatureMatchConfig)
    event_config: EventDetectionConfig = field(default_factory=EventDetectionConfig)


def _feature(
    identifier: str,
    position: tuple[float, float, float],
    *,
    kind: str = "elf_basin",
    charge: float | None = None,
    volume: float | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Feature:
    return Feature(
        identifier,
        kind,
        position,
        charge=charge,
        volume=volume,
        attributes=dict(attributes or {}),
    )


def _frame(
    index: int,
    *,
    atoms: Sequence[Atom] = (),
    features: Sequence[Feature] = (),
    cell: Matrix3 = _CELL,
) -> Frame:
    return Frame(
        index=index,
        label=str(index),
        cell=cell,
        atoms=tuple(atoms),
        features=tuple(features),
    )


def _synthetic_cases() -> tuple[SyntheticCase, ...]:
    descriptor_config = FeatureMatchConfig(descriptor_scales={"icohp": 1.0, "icobi": 1.0})
    property_config = FeatureMatchConfig(descriptor_scales={"icohp": 2.0})
    hp = {"descriptor_family": "icohp", "icohp": -1.0}
    bi = {"descriptor_family": "icobi", "icobi": 0.5}
    return (
        SyntheticCase(
            id="atom-periodic-wrap",
            category="periodic_boundary",
            description="An atom crossing a periodic face retains its lineage.",
            perturbation="fractional x coordinate 0.99 to 0.01",
            previous=_frame(0, atoms=(Atom(7, "C", (0.99, 0.5, 0.5)),)),
            current=_frame(1, atoms=(Atom(12, "C", (0.01, 0.5, 0.5)),)),
            atom_mapping=((7, 12),),
        ),
        SyntheticCase(
            id="feature-periodic-wrap",
            category="periodic_boundary",
            description="A feature crossing a periodic face retains its lineage.",
            perturbation="fractional x coordinate 0.99 to 0.01",
            previous=_frame(0, features=(_feature("p", (0.99, 0.5, 0.5)),)),
            current=_frame(1, features=(_feature("q", (0.01, 0.5, 0.5)),)),
            feature_mapping=(("p", "q"),),
        ),
        SyntheticCase(
            id="feature-position-jitter",
            category="coordinate_noise",
            description="Small coordinate noise does not create a false event.",
            perturbation="fractional displacement (0.002, -0.001, 0.001)",
            previous=_frame(0, features=(_feature("p", (0.4, 0.4, 0.4)),)),
            current=_frame(1, features=(_feature("q", (0.402, 0.399, 0.401)),)),
            feature_mapping=(("p", "q"),),
        ),
        SyntheticCase(
            id="variable-cell-stability",
            category="cell_deformation",
            description="A stable fractional feature survives an anisotropic cell change.",
            perturbation="cell axes change from (10, 10, 10) to (12, 8, 10) angstrom",
            previous=_frame(0, features=(_feature("p", (0.3, 0.4, 0.5)),)),
            current=_frame(
                1,
                features=(_feature("q", (0.3, 0.4, 0.5)),),
                cell=_DEFORMED_CELL,
            ),
            feature_mapping=(("p", "q"),),
        ),
        SyntheticCase(
            id="descriptor-order-reversal",
            category="input_order",
            description="Semantic descriptor families prevent order-dependent lineage swaps.",
            perturbation="target feature array is reversed",
            previous=_frame(
                0,
                features=(
                    _feature("p-hp", (0.5, 0.5, 0.5), kind="bond_descriptor", attributes=hp),
                    _feature("p-bi", (0.5, 0.5, 0.5), kind="bond_descriptor", attributes=bi),
                ),
            ),
            current=_frame(
                1,
                features=(
                    _feature("q-bi", (0.5, 0.5, 0.5), kind="bond_descriptor", attributes=bi),
                    _feature("q-hp", (0.5, 0.5, 0.5), kind="bond_descriptor", attributes=hp),
                ),
            ),
            feature_mapping=(("p-bi", "q-bi"), ("p-hp", "q-hp")),
            feature_config=descriptor_config,
        ),
        SyntheticCase(
            id="feature-birth-and-death",
            category="topology_event",
            description="Incompatible disappearing and appearing kinds produce death and birth labels.",
            perturbation="one stable feature plus one removed and one added feature",
            previous=_frame(
                0,
                features=(
                    _feature("p-stable", (0.5, 0.5, 0.5)),
                    _feature("p-dead", (0.2, 0.2, 0.2), kind="bond_critical_point"),
                ),
            ),
            current=_frame(
                1,
                features=(
                    _feature("q-stable", (0.501, 0.5, 0.5)),
                    _feature("q-born", (0.2, 0.2, 0.2), kind="non_nuclear_attractor"),
                ),
            ),
            events=(
                EventLabel("birth", target_ids=("q-born",)),
                EventLabel("death", source_ids=("p-dead",)),
            ),
            feature_mapping=(("p-stable", "q-stable"),),
        ),
        SyntheticCase(
            id="descriptor-property-change",
            category="property_event",
            description="A threshold-crossing ICOHP change produces a property-change label.",
            perturbation="ICOHP changes from -0.2 to -1.2 eV",
            previous=_frame(
                0,
                features=(
                    _feature(
                        "p",
                        (0.5, 0.5, 0.5),
                        kind="bond_descriptor",
                        attributes={"descriptor_family": "icohp", "icohp": -0.2},
                    ),
                ),
            ),
            current=_frame(
                1,
                features=(
                    _feature(
                        "q",
                        (0.501, 0.5, 0.5),
                        kind="bond_descriptor",
                        attributes={"descriptor_family": "icohp", "icohp": -1.2},
                    ),
                ),
            ),
            events=(EventLabel("property_change", ("p",), ("q",)),),
            feature_mapping=(("p", "q"),),
            feature_config=property_config,
        ),
        SyntheticCase(
            id="conserving-split",
            category="topology_event",
            description="A volume-conserving one-to-two transition produces a split.",
            perturbation="volume 10 becomes volumes 4 and 6",
            previous=_frame(
                0,
                features=(_feature("p", (0.5, 0.5, 0.5), volume=10.0),),
            ),
            current=_frame(
                1,
                features=(
                    _feature("q1", (0.48, 0.5, 0.5), volume=4.0),
                    _feature("q2", (0.54, 0.5, 0.5), volume=6.0),
                ),
            ),
            events=(EventLabel("split", ("p",), ("q1", "q2")),),
        ),
        SyntheticCase(
            id="conserving-merge",
            category="topology_event",
            description="A volume-conserving two-to-one transition produces a merge.",
            perturbation="volumes 4 and 6 become volume 10",
            previous=_frame(
                0,
                features=(
                    _feature("p1", (0.48, 0.5, 0.5), volume=4.0),
                    _feature("p2", (0.54, 0.5, 0.5), volume=6.0),
                ),
            ),
            current=_frame(
                1,
                features=(_feature("q", (0.5, 0.5, 0.5), volume=10.0),),
            ),
            events=(EventLabel("merge", ("p1", "p2"), ("q",)),),
        ),
        SyntheticCase(
            id="proximity-without-conservation",
            category="negative_control",
            description="Nearby unquantified children are not promoted to a split.",
            perturbation="one unquantified feature becomes two nearby unquantified features",
            previous=_frame(0, features=(_feature("p", (0.5, 0.5, 0.5)),)),
            current=_frame(
                1,
                features=(
                    _feature("q1", (0.48, 0.5, 0.5)),
                    _feature("q2", (0.54, 0.5, 0.5)),
                ),
            ),
            events=(EventLabel("birth", target_ids=("q2",)),),
            feature_mapping=(("p", "q1"),),
        ),
        SyntheticCase(
            id="failed-conservation",
            category="negative_control",
            description="A one-to-two candidate that violates volume conservation is not a split.",
            perturbation="volume 10 becomes volumes 9 and 9",
            previous=_frame(
                0,
                features=(_feature("p", (0.5, 0.5, 0.5), volume=10.0),),
            ),
            current=_frame(
                1,
                features=(
                    _feature("q1", (0.48, 0.5, 0.5), volume=9.0),
                    _feature("q2", (0.54, 0.5, 0.5), volume=9.0),
                ),
            ),
            events=(EventLabel("birth", target_ids=("q2",)),),
            feature_mapping=(("p", "q1"),),
        ),
        SyntheticCase(
            id="exact-assignment-tie",
            category="ambiguity",
            description="An exact target tie is surfaced and affected event claims are suppressed.",
            perturbation="two targets have identical positions and missing descriptors",
            previous=_frame(0, features=(_feature("p", (0.5, 0.5, 0.5)),)),
            current=_frame(
                1,
                features=(
                    _feature("q1", (0.5, 0.5, 0.5)),
                    _feature("q2", (0.5, 0.5, 0.5)),
                ),
            ),
            feature_ambiguous=True,
        ),
        SyntheticCase(
            id="near-assignment-tie",
            category="ambiguity",
            description="A near-optimal target tie within tolerance is surfaced.",
            perturbation="alternative target is displaced by 0.001 angstrom",
            previous=_frame(0, features=(_feature("p", (0.5, 0.5, 0.5)),)),
            current=_frame(
                1,
                features=(
                    _feature("q1", (0.5, 0.5, 0.5)),
                    _feature("q2", (0.5001, 0.5, 0.5)),
                ),
            ),
            feature_ambiguous=True,
        ),
        SyntheticCase(
            id="incompatible-charge-convention",
            category="semantic_gate",
            description="Explicitly incompatible charge conventions cannot share a lineage.",
            perturbation="charge convention changes from convention-a to convention-b",
            previous=_frame(
                0,
                features=(
                    _feature(
                        "p",
                        (0.5, 0.5, 0.5),
                        charge=0.5,
                        attributes={"charge_convention": "convention-a"},
                    ),
                ),
            ),
            current=_frame(
                1,
                features=(
                    _feature(
                        "q",
                        (0.5, 0.5, 0.5),
                        charge=0.5,
                        attributes={"charge_convention": "convention-b"},
                    ),
                ),
            ),
            events=(
                EventLabel("birth", target_ids=("q",)),
                EventLabel("death", source_ids=("p",)),
            ),
        ),
        SyntheticCase(
            id="hyperedge-resolves-backbone-tie",
            category="ambiguity_resolution",
            description="A conserving split resolves an arbitrary one-to-one backbone tie.",
            perturbation="symmetric children each carry half the parent volume",
            previous=_frame(
                0,
                features=(_feature("p", (0.5, 0.5, 0.5), volume=10.0),),
            ),
            current=_frame(
                1,
                features=(
                    _feature("q1", (0.49, 0.5, 0.5), volume=5.0),
                    _feature("q2", (0.51, 0.5, 0.5), volume=5.0),
                ),
            ),
            events=(EventLabel("split", ("p",), ("q1", "q2")),),
        ),
    )


def _event_label(event: Event) -> EventLabel:
    return EventLabel(
        event.event_type.value,
        tuple(str(value) for value in event.source_ids),
        tuple(str(value) for value in event.target_ids),
    )


def _event_scores(
    expected: Sequence[EventLabel], observed: Sequence[Event]
) -> tuple[int, int, int]:
    expected_counts = Counter(label.key() for label in expected)
    observed_counts = Counter(_event_label(event).key() for event in observed)
    true_positives = sum((expected_counts & observed_counts).values())
    false_positives = sum((observed_counts - expected_counts).values())
    false_negatives = sum((expected_counts - observed_counts).values())
    return true_positives, false_positives, false_negatives


def _mapping_pairs(
    mapping: Mapping[Identifier, Identifier],
) -> tuple[tuple[Identifier, Identifier], ...]:
    return tuple(sorted(mapping.items(), key=lambda item: (str(item[0]), str(item[1]))))


def _mapping_records(
    pairs: Sequence[tuple[Identifier, Identifier]],
) -> list[dict[str, Identifier]]:
    return [
        {"source_id": source, "target_id": target}
        for source, target in sorted(pairs, key=lambda item: (str(item[0]), str(item[1])))
    ]


def _relations(result: MatchResult) -> list[dict[str, Any]]:
    return [
        {
            "relation": edge.relation.value,
            "source_ids": sorted(str(value) for value in edge.source_ids),
            "target_ids": sorted(str(value) for value in edge.target_ids),
            "confidence": edge.confidence,
            "ambiguous": edge.ambiguous,
        }
        for edge in sorted(
            result.edges,
            key=lambda item: (
                item.relation.value,
                tuple(str(value) for value in item.source_ids),
                tuple(str(value) for value in item.target_ids),
            ),
        )
    ]


def _rate(numerator: int | float, denominator: int | float, *, empty: float = 1.0) -> float:
    return numerator / denominator if denominator else empty


def _evaluate_case(case: SyntheticCase) -> dict[str, Any]:
    atom_result = match_atoms(case.previous, case.current, case.atom_config)
    feature_result = match_features(
        case.previous,
        case.current,
        atom_result,
        case.feature_config,
    )
    events = detect_transition_events(
        case.previous,
        case.current,
        feature_result,
        case.event_config,
    )
    event_tp, event_fp, event_fn = _event_scores(case.events, events)
    observed_atom_mapping = _mapping_pairs(atom_result.mapping)
    observed_feature_mapping = _mapping_pairs(feature_result.mapping)
    expected_atom_mapping = _mapping_pairs(dict(case.atom_mapping))
    expected_feature_mapping = _mapping_pairs(dict(case.feature_mapping))
    observed_atom_ambiguous = bool(atom_result.diagnostics.get("ambiguous"))
    observed_feature_ambiguous = bool(feature_result.diagnostics.get("ambiguous"))
    atom_mapping_correct = observed_atom_mapping == expected_atom_mapping
    feature_mapping_correct = observed_feature_mapping == expected_feature_mapping
    atom_ambiguity_correct = observed_atom_ambiguous is case.atom_ambiguous
    feature_ambiguity_correct = observed_feature_ambiguous is case.feature_ambiguous
    failures: list[str] = []
    if event_fp or event_fn:
        failures.append("event labels differ")
    if not atom_mapping_correct:
        failures.append("atom mapping differs")
    if not feature_mapping_correct:
        failures.append("feature mapping differs")
    if not atom_ambiguity_correct:
        failures.append("atom ambiguity decision differs")
    if not feature_ambiguity_correct:
        failures.append("feature ambiguity decision differs")
    return {
        "id": case.id,
        "category": case.category,
        "description": case.description,
        "perturbation": case.perturbation,
        "passed": not failures,
        "failures": failures,
        "expected": {
            "events": [label.to_dict() for label in sorted(case.events, key=EventLabel.key)],
            "atom_mapping": _mapping_records(expected_atom_mapping),
            "feature_mapping": _mapping_records(expected_feature_mapping),
            "atom_ambiguous": case.atom_ambiguous,
            "feature_ambiguous": case.feature_ambiguous,
        },
        "observed": {
            "events": [
                {**_event_label(event).to_dict(), "confidence": event.confidence}
                for event in events
            ],
            "atom_mapping": _mapping_records(observed_atom_mapping),
            "feature_mapping": _mapping_records(observed_feature_mapping),
            "atom_ambiguous": observed_atom_ambiguous,
            "feature_ambiguous": observed_feature_ambiguous,
            "atom_ambiguity_count": len(atom_result.diagnostics.get("ambiguities", [])),
            "feature_ambiguity_count": len(feature_result.diagnostics.get("ambiguities", [])),
            "relations": _relations(feature_result),
            "hyperedge_rejections": feature_result.diagnostics.get("hyperedge_rejections", []),
        },
        "metrics": {
            "event_true_positives": event_tp,
            "event_false_positives": event_fp,
            "event_false_negatives": event_fn,
            "mapping_assertions_correct": int(atom_mapping_correct) + int(feature_mapping_correct),
            "mapping_assertions_total": 2,
            "ambiguity_assertions_correct": int(atom_ambiguity_correct)
            + int(feature_ambiguity_correct),
            "ambiguity_assertions_total": 2,
        },
    }


def run_synthetic_benchmark() -> dict[str, Any]:
    """Run the versioned offline suite and return a deterministic JSON-ready report."""

    cases = [_evaluate_case(case) for case in _synthetic_cases()]
    event_tp = sum(int(case["metrics"]["event_true_positives"]) for case in cases)
    event_fp = sum(int(case["metrics"]["event_false_positives"]) for case in cases)
    event_fn = sum(int(case["metrics"]["event_false_negatives"]) for case in cases)
    mapping_correct = sum(int(case["metrics"]["mapping_assertions_correct"]) for case in cases)
    mapping_total = sum(int(case["metrics"]["mapping_assertions_total"]) for case in cases)
    ambiguity_correct = sum(int(case["metrics"]["ambiguity_assertions_correct"]) for case in cases)
    ambiguity_total = sum(int(case["metrics"]["ambiguity_assertions_total"]) for case in cases)
    passed = sum(bool(case["passed"]) for case in cases)
    precision = _rate(event_tp, event_tp + event_fp)
    recall = _rate(event_tp, event_tp + event_fn)
    f1 = _rate(2 * precision * recall, precision + recall)
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "software_version": __version__,
        "suite": {
            "name": "electrace-synthetic-lineage",
            "version": BENCHMARK_SUITE_VERSION,
            "synthetic": True,
            "scientific_validation": False,
            "description": (
                "Deterministic regression cases for periodic matching, semantic gates, "
                "conservation-backed hyperedges, event labels, and ambiguity handling."
            ),
            "limitations": [
                "Cases are constructed inputs, not independent electronic-structure results.",
                "Perfect synthetic scores do not establish accuracy on a physical domain.",
                "Confidence values are deterministic evidence scores, not calibrated probabilities.",
            ],
        },
        "summary": {
            "cases": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "event_true_positives": event_tp,
            "event_false_positives": event_fp,
            "event_false_negatives": event_fn,
            "event_precision": precision,
            "event_recall": recall,
            "event_f1": f1,
            "mapping_assertions_correct": mapping_correct,
            "mapping_assertions_total": mapping_total,
            "mapping_accuracy": _rate(mapping_correct, mapping_total),
            "ambiguity_assertions_correct": ambiguity_correct,
            "ambiguity_assertions_total": ambiguity_total,
            "ambiguity_accuracy": _rate(ambiguity_correct, ambiguity_total),
        },
        "cases": cases,
    }


def write_synthetic_benchmark(
    path: str | Path = "electrace-benchmark.json", *, overwrite: bool = False
) -> Path:
    """Run and atomically write the synthetic benchmark report."""

    output = Path(path)
    if (output.exists() or output.is_symlink()) and not overwrite:
        raise FileExistsError(
            f"benchmark output already exists: {output}; use --force to replace it"
        )
    return write_json(run_synthetic_benchmark(), output)


def benchmark_passed(report: Mapping[str, Any]) -> bool:
    """Return whether a benchmark report has no labeled-case failures."""

    summary = report.get("summary")
    return (
        isinstance(summary, Mapping)
        and summary.get("failed") == 0
        and summary.get("cases") == summary.get("passed")
        and isinstance(summary.get("cases"), int)
        and int(summary["cases"]) > 0
        and all(
            math.isfinite(float(summary.get(key, math.nan)))
            for key in ("event_precision", "event_recall", "event_f1")
        )
    )
