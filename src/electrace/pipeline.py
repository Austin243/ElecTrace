"""End-to-end ElecTrace analysis orchestration."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count, pairwise
from pathlib import Path
from typing import Any

import numpy as np

from electrace import __version__
from electrace._files import atomic_write_text
from electrace.bonds import bond_records
from electrace.config import ElecTraceConfig, load_config
from electrace.events import (
    EventDetectionConfig,
    critical_point_fields,
    track_sequence,
    weak_critical_point_kind,
)
from electrace.export import (
    write_atom_groups_csv,
    write_bonds_csv,
    write_events_csv,
    write_json,
    write_track_summary_csv,
    write_tracks_csv,
)
from electrace.focus import (
    annotate_events,
    auto_focus_record,
    check_atom_order,
    noise_floor,
    ratio_to_floor,
    select_focus_atoms,
    track_lineages,
    track_value,
)
from electrace.groups import GROUP_QUANTITIES, atom_groups
from electrace.io import load_sequence
from electrace.matching import AtomMatchConfig, FeatureMatchConfig, _as_finite_float
from electrace.models import (
    Event,
    Feature,
    Frame,
    Identifier,
    MatchRelation,
    MatchResult,
    Sequence,
    _json_value,
)
from electrace.provenance import environment_record, fingerprint_files, sha256_file
from electrace.report import render_html_report


@dataclass(frozen=True, slots=True)
class AnalysisArtifacts:
    """A completed in-memory analysis and its written products."""

    analysis: dict[str, Any]
    output_directory: Path
    files: tuple[Path, ...]


def _resolve(base: Path, value: str | Path) -> Path:
    # Joining an absolute path replaces the base.
    return (base / Path(value).expanduser()).resolve()


def _matching_configs(config: ElecTraceConfig) -> tuple[AtomMatchConfig, FeatureMatchConfig]:
    settings = config.matching
    atom = AtomMatchConfig(
        max_distance=settings.max_atom_displacement, identity=settings.atom_identity
    )
    feature = FeatureMatchConfig(
        max_distance=settings.max_feature_displacement,
        max_cost=settings.match_threshold,
        distance_weight=settings.position_weight,
        charge_weight=settings.charge_weight,
        volume_weight=settings.volume_weight,
        neighbor_weight=settings.neighbor_weight,
        voxel_weight=settings.overlap_weight,
        descriptor_weight=settings.descriptor_weight,
        descriptor_scales=settings.descriptor_scales,
        conservation_tolerance=settings.conservation_tolerance,
        min_split_merge_confidence=settings.split_merge_threshold,
    )
    return atom, feature


def _event_config(config: ElecTraceConfig) -> EventDetectionConfig:
    settings = config.events
    return EventDetectionConfig(
        charge_change_threshold=settings.charge_change_threshold,
        volume_relative_threshold=settings.volume_change_fraction,
        descriptor_thresholds=settings.descriptor_thresholds,
        min_event_confidence=settings.minimum_event_confidence,
        min_persistence=settings.minimum_persistence,
    )


def _frame_record(frame: Frame) -> dict[str, Any]:
    kinds = Counter(feature.kind for feature in frame.features)
    return {
        **frame.to_dict(),
        "atom_count": len(frame.atoms),
        "feature_count": len(frame.features),
        "feature_kinds": dict(sorted(kinds.items())),
        "cell_volume": abs(float(np.linalg.det(np.asarray(frame.cell, dtype=float)))),
    }


def _source_files(sequence: Sequence, config_path: Path) -> list[Path]:
    files = [config_path]
    for frame in sequence.frames:
        files.append(Path(frame.metadata["structure_source"]))
        files.extend(Path(descriptor) for descriptor in frame.metadata["descriptor_sources"])
        if "energy_source" in frame.metadata:
            files.append(Path(frame.metadata["frame_directory"]) / frame.metadata["energy_source"])
    return files


def _descriptor_provenance(sequence: Sequence) -> tuple[list[dict[str, Any]], list[str]]:
    """Collect deterministic per-frame descriptor records and parser warnings."""

    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for frame in sequence.frames:
        for document in frame.metadata.get("descriptor_documents", []):
            records.append({"frame": {"index": frame.index, "label": frame.label}, **document})
            # Canonical JSON can carry any metadata, so its warnings are checked.
            raw_warnings = document["metadata"].get("warnings", [])
            if not isinstance(raw_warnings, list):
                continue
            warnings.extend(
                f"Descriptor warning ({frame.label}, {document['producer']['name']}): {warning}"
                for warning in raw_warnings
                if isinstance(warning, str)
            )
    return records, warnings


_COUNT_KEYS = ("n", "b", "r", "c", "unclassified")
_NO_CRITIC2 = "no critic2 document"


def _critic2_counts(document: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a critic2 document's counts, or None when it records no valid counts.

    Canonical JSON can carry any metadata, so the counts are checked and the sum is
    recomputed rather than read.
    """

    record = document["metadata"].get("critical_point_counts")
    if not isinstance(record, Mapping):
        return None
    counts: dict[str, int] = {}
    for key in _COUNT_KEYS:
        value = record.get(key)
        if type(value) is not int or value < 0:
            return None
        counts[key] = value
    expected = record.get("expected_morse_sum")
    if type(expected) is not int or expected not in (0, 1):
        return None
    morse_sum = (
        None if counts["unclassified"] else counts["n"] - counts["b"] + counts["r"] - counts["c"]
    )
    return {**counts, "morse_sum": morse_sum, "expected_morse_sum": expected}


def _critical_point_counts(sequence: Sequence) -> tuple[list[dict[str, Any]], list[str]]:
    """Return each frame's critic2 critical-point counts and warnings about them.

    A complete search of a periodic cell has n - b + r - c = 0, and of a molecule 1.
    Points that the search missed or that DISCARD removed, such as the vacuum points
    around a molecule in a large cell, change the sum, so a different sum says points
    are missing, not which. A missed pair of points can also leave it unchanged. The
    counts cover every cell critical point, before any density cutoff or focus.
    """

    entries: list[dict[str, Any]] = []
    for frame in sequence.frames:
        documents = [
            document
            for document in frame.metadata.get("descriptor_documents", [])
            if document["producer"]["name"] == "critic2"
        ]
        frame_key = {"frame": frame.index, "frame_label": frame.label}
        blank = dict.fromkeys((*_COUNT_KEYS, "morse_sum", "expected_morse_sum"))
        if not documents:
            entries.append({**frame_key, **blank, "reason": _NO_CRITIC2})
        for document in documents:
            counts = _critic2_counts(document)
            entry = {**frame_key, **(counts or blank)}
            if counts is None:
                entry["reason"] = "the critic2 document records no counts"
            elif counts["morse_sum"] is None:
                entry["reason"] = "critical points without an n, b, r, or c signature"
            entries.append(entry)
    if all(entry.get("reason") == _NO_CRITIC2 for entry in entries):
        return [], []

    off = [
        f"{entry['frame_label']} ({entry['morse_sum']:+d}"
        + (", molecule)" if entry["expected_morse_sum"] else ")")
        for entry in entries
        if entry["morse_sum"] is not None and entry["morse_sum"] != entry["expected_morse_sum"]
    ]
    unknown: dict[str, list[str]] = {}
    for entry in entries:
        if "reason" in entry:
            unknown.setdefault(entry["reason"], []).append(entry["frame_label"])
    warnings: list[str] = []
    if off:
        warnings.append(
            "critic2 Morse sum n - b + r - c differs from a complete search's (0 for a periodic "
            f"cell, 1 for a molecule) in frame(s) {', '.join(off)}: critical points are missing "
            "there, missed by the search or removed by DISCARD (as a molecule's vacuum points "
            "are), so critical-point births, deaths, and connectivity changes at those frames "
            "may not be chemistry. The counts use every critical point, before any density "
            "cutoff or focus."
        )
    missing = unknown.pop(_NO_CRITIC2, [])
    if missing:
        warnings.append(
            "No critic2 critical points were loaded for frame(s): "
            + ", ".join(missing)
            + "; they are missing there, not absent, so critical-point births and deaths at "
            "those frames are not chemistry."
        )
    warnings.extend(
        f"critic2 Morse sum unknown for frame(s) {', '.join(labels)} ({reason}); those "
        "searches cannot be checked for missing critical points."
        for reason, labels in unknown.items()
    )
    return entries, warnings


def _observation(
    track_id: str,
    frame: Frame,
    feature: Feature,
    *,
    confidence: float,
    parent_track_ids: Iterable[str] = (),
) -> dict[str, Any]:
    position = feature.fractional_position
    observation = {
        "track_id": track_id,
        "frame": frame.index,
        "frame_label": frame.label,
        "feature_id": feature.id,
        "kind": feature.kind,
        "fractional_x": position[0],
        "fractional_y": position[1],
        "fractional_z": position[2],
        "charge": feature.charge,
        "volume": feature.volume,
        "confidence": confidence,
        "parent_track_ids": sorted(set(parent_track_ids)),
        "neighbor_atom_indices": list(feature.neighbor_atom_indices),
        "attributes": dict(feature.attributes),
    }
    if "net_charge" in feature.attributes:
        # A Bader charge is a population; its net charge gets its own CSV column.
        observation["net_charge"] = feature.attributes["net_charge"]
    return observation


def _build_tracks(
    sequence: Sequence, feature_matches: Iterable[MatchResult]
) -> list[dict[str, Any]]:
    """Build deterministic lineage observations from adjacent-frame hyperedges.

    The matcher gives each target feature at most one incoming edge, and every source
    of an edge is a feature of the previous frame, so all of them have active tracks.
    """

    frames = sequence.frames
    numbers = count(1)
    active: dict[Identifier, str] = {}
    observations: list[dict[str, Any]] = []
    for feature in sorted(frames[0].features, key=lambda item: item.id):
        track = f"T{next(numbers):05d}"
        active[feature.id] = track
        observations.append(_observation(track, frames[0], feature, confidence=1.0))

    for transition_index, result in enumerate(feature_matches):
        current = frames[transition_index + 1]
        next_active: dict[Identifier, str] = {}
        incoming: dict[Identifier, tuple[float, str, tuple[str, ...]]] = {}
        for edge in result.edges:
            parent_tracks = tuple(sorted({active[source] for source in edge.source_ids}))
            for target in edge.target_ids:
                if edge.relation is MatchRelation.ONE_TO_ONE:
                    incoming[target] = (edge.confidence, parent_tracks[0], ())
                else:
                    # A split or merge starts a new lineage node and retains
                    # explicit parentage instead of pretending identity is unique.
                    incoming[target] = (edge.confidence, "", parent_tracks)

        for feature in sorted(current.features, key=lambda item: item.id):
            confidence, candidate_track, parents = incoming.get(feature.id, (0.75, "", ()))
            track = candidate_track or f"T{next(numbers):05d}"
            next_active[feature.id] = track
            observations.append(
                _observation(
                    track, current, feature, confidence=confidence, parent_track_ids=parents
                )
            )
        active = next_active
    return observations


def _change_summary(values: list[tuple[int, float | None]]) -> dict[str, Any]:
    """Summarize one quantity along a lineage without thresholds or imputation."""

    observed = [value for _, value in values if value is not None]
    first, last = values[0][1], values[-1][1]
    steps = [
        (frame_before, frame_after, after - before)
        for (frame_before, before), (frame_after, after) in pairwise(values)
        if before is not None and after is not None
    ]
    largest = max(steps, key=lambda step: abs(step[2]), default=None)
    return {
        "observed": len(observed),
        "net_change": (
            last - first if len(values) > 1 and first is not None and last is not None else None
        ),
        "range": max(observed) - min(observed) if len(observed) > 1 else None,
        "largest_step": (
            None
            if largest is None
            else {"frame_before": largest[0], "frame_after": largest[1], "delta": largest[2]}
        ),
    }


def _summarize_tracks(
    sequence: Sequence,
    tracks: Iterable[Mapping[str, Any]],
    quantities: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Compare each lineage's first and last frames, which adjacent-frame events cannot.

    This is reporting only: it applies no thresholds and creates no events.
    """

    path = (sequence.frames[0].index, sequence.frames[-1].index)
    summary: list[dict[str, Any]] = []
    for track_id, observations in track_lineages(tracks).items():
        first, last = observations[0], observations[-1]
        summary.append(
            {
                "track_id": track_id,
                "kind": first["kind"],
                "first_frame": first["frame"],
                "last_frame": last["frame"],
                "observations": len(observations),
                "spans_path": (first["frame"], last["frame"]) == path,
                "parent_track_ids": list(first["parent_track_ids"]),
                "changes": {
                    name: _change_summary(
                        [(int(item["frame"]), track_value(item, name)) for item in observations]
                    )
                    for name in quantities
                },
            }
        )
    return summary


def _summarize_groups(report: Mapping[str, Any]) -> None:
    """Add each group's whole-path changes, with its largest step against the floor.

    Spin steps are compared with the spin floor; charge and net-charge steps, which
    are equal for a fixed group, with the charge floor.
    """

    floor = {(item["producer"], item["quantity"]): item for item in report["noise_floor"]}
    for group in report["groups"]:
        changes: dict[str, Any] = {}
        for quantity in GROUP_QUANTITIES:
            values = [(int(item["frame"]), item[quantity]) for item in group["values"]]
            if all(value is None for _, value in values):
                continue
            summary = _change_summary(values)
            largest = summary["largest_step"]
            entry = floor.get((group["producer"], "spin" if quantity == "spin" else "charge"))
            if largest is not None and entry is not None:
                largest["ratio_to_floor"] = ratio_to_floor(largest["delta"], entry)
            changes[quantity] = summary
        group["changes"] = changes


def _locate_barrier(sequence: Sequence, series_kind: str) -> dict[str, Any]:
    """Find the highest-energy frame(s) for placing events; reporting only.

    Frames that tie exactly at the maximum are all kept; no tolerance is applied.
    """

    if series_kind == "pressure":
        return {
            "frames": [],
            "reason": "Events are not placed: a pressure series is not a reaction path.",
        }
    energies = [frame.energy for frame in sequence.frames if frame.energy is not None]
    if len(energies) < len(sequence.frames):
        missing = ", ".join(frame.label for frame in sequence.frames if frame.energy is None)
        return {"frames": [], "reason": f"Events are not placed: no energy for frame(s) {missing}."}
    peak = max(energies)
    return {
        "frames": [frame.index for frame in sequence.frames if frame.energy == peak],
        "rise_from_first": peak - energies[0],
        "rise_from_last": peak - energies[-1],
    }


def _barrier_position(event: Mapping[str, Any], peak_frames: list[int]) -> str | None:
    if not peak_frames:
        return None
    if event["frame_after"] < peak_frames[0]:
        return "before"
    if event["frame_before"] > peak_frames[-1]:
        return "after"
    # The step starts or ends at a highest-energy frame, so the frame spacing
    # cannot place the change on either side of the barrier.
    return "spanning"


def _check_focus_atoms(sequence: Sequence, focus_atoms: Iterable[int]) -> None:
    focus = set(focus_atoms)
    for frame in sequence.frames:
        absent = sorted(focus - {atom.index for atom in frame.atoms})
        if absent:
            raise ValueError(
                f"outputs.focus_atoms {absent} are not 0-based atom indices of frame "
                f"{frame.label}, which has {len(frame.atoms)} atoms"
            )


def _identity_warnings(
    sequence: Sequence,
    atom_matches: Iterable[MatchResult],
    atoms: Iterable[int],
    subject: str,
    uses: str,
) -> list[str]:
    """Flag atoms matched to a different index, which geometric identity allows."""

    labels = {frame.index: frame.label for frame in sequence.frames}
    indices = sorted(set(atoms))
    warnings: list[str] = []
    for result in atom_matches:
        mapping = result.mapping
        moved = [
            f"{index}→{mapping.get(index, 'unmatched')}"
            for index in indices
            if mapping.get(index) != index
        ]
        if moved:
            warnings.append(
                f"Frames {labels[result.previous_frame_index]}→{labels[result.current_frame_index]}: "
                f"{subject} atom(s) matched to another index ({', '.join(moved)}); {uses} "
                "each frame's own atom indices."
            )
    return warnings


def _focus_view(
    sequence: Sequence, focus_atoms: Iterable[int], analysis: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep only the tracks tied to the focus atoms and the events on those tracks.

    Matching, numbering, and summaries already used every atom and feature, so IDs
    and confidences are those of the unfocused analysis. A track is kept whole when
    any observation lists a focus atom in ``neighbor_atom_indices``, which for an
    atom-centered feature is the atom itself; an event is kept when one of its
    source or target features belongs to a kept track.
    """

    focus = set(focus_atoms)
    tracks = analysis["tracks"]
    kept = {
        item["track_id"] for item in tracks if focus.intersection(item["neighbor_atom_indices"])
    }
    track_of = {(item["frame"], item["feature_id"]): item["track_id"] for item in tracks}
    events = [
        event
        for event in analysis["events"]
        if any(
            track_of.get(key) in kept
            for key in (
                *((event["frame_before"], item) for item in event["source_ids"]),
                *((event["frame_after"], item) for item in event["target_ids"]),
            )
        )
    ]
    observations = [item for item in tracks if item["track_id"] in kept]
    bonds = [
        item for item in analysis["bonds"] if focus.intersection((item["atom_a"], item["atom_b"]))
    ]
    first = sequence.frames[0]
    return {
        "events": events,
        "tracks": observations,
        "track_summary": [item for item in analysis["track_summary"] if item["track_id"] in kept],
        "bonds": bonds,
        "focus": {
            "atoms": [
                {"index": index, "element": first.atom_by_index(index).element}
                for index in sorted(focus)
            ],
            "tracks": {"reported": len(kept), "total": len(analysis["track_summary"])},
            "observations": {"reported": len(observations), "total": len(tracks)},
            "events": {"reported": len(events), "total": len(analysis["events"])},
            "bonds": {"reported": len(bonds), "total": len(analysis["bonds"])},
        },
    }


def _normalize_events(
    events: Iterable[Event], medium_threshold: float, high_threshold: float
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for number, event in enumerate(events, start=1):
        status = event.metadata["persistence_status"]
        if event.confidence >= high_threshold:
            confidence_label = "high"
        elif event.confidence >= medium_threshold:
            confidence_label = "medium"
        else:
            confidence_label = "low"
        normalized.append(
            {
                "event_id": f"E{number:05d}",
                "type": event.event_type.value,
                "start_frame": event.frame_after,
                "end_frame": event.metadata["persistence_end_frame"],
                "frame_before": event.frame_before,
                "frame_after": event.frame_after,
                "source_ids": list(event.source_ids),
                "target_ids": list(event.target_ids),
                "confidence": event.confidence,
                "confidence_label": confidence_label,
                "persistence": event.persistence,
                "persistence_status": status,
                "persistent": status == "confirmed",
                "description": event.description,
                "evidence": _json_value(event.evidence),
                "metadata": _json_value(event.metadata),
            }
        )
    return normalized


def analyze_sequence(
    sequence: Sequence, config: ElecTraceConfig, config_path: Path
) -> dict[str, Any]:
    """Run matching, event detection, lineage construction, and provenance."""

    if len(sequence.frames) < 2:
        raise ValueError("ElecTrace requires at least two ordered frames")
    configured_focus = config.outputs.focus_atoms
    if isinstance(configured_focus, str):
        check_atom_order(sequence)
    else:
        _check_focus_atoms(sequence, configured_focus)
    atom_config, feature_config = _matching_configs(config)
    tracking = track_sequence(sequence, atom_config, feature_config, _event_config(config))
    tracks = _build_tracks(sequence, tracking.feature_matches)
    configured = {*config.events.descriptor_thresholds, *config.matching.descriptor_scales}
    descriptors = sorted(configured - {"charge", "net_charge", "volume"})
    net_charges = "net_charge" in configured or any(
        "net_charge" in item.attributes for frame in sequence.frames for item in frame.features
    )
    track_summary = _summarize_tracks(
        sequence,
        tracks,
        ("charge", *(("net_charge",) if net_charges else ()), "volume", *descriptors),
    )
    bonds, bond_warnings = bond_records(sequence, tracks)
    descriptor_documents, descriptor_warnings = _descriptor_provenance(sequence)
    critical_point_counts, count_warnings = _critical_point_counts(sequence)
    selection = None
    if isinstance(configured_focus, str):
        selection = select_focus_atoms(
            sequence,
            tracks,
            tracking.events,
            bond_cutoffs=config.outputs.bond_cutoffs(),
            spin_threshold=config.events.descriptor_thresholds.get("spin"),
            critical_point_min_field=config.events.critical_point_min_field,
        )
        focus_atoms = list(selection.rules)
    else:
        focus_atoms = configured_focus
    groups: dict[str, Any] | None = None
    group_warnings: list[str] = []
    if config.outputs.atom_groups or config.outputs.molecule_groups:
        groups, group_warnings = atom_groups(
            sequence,
            configured=config.outputs.atom_groups,
            molecule_groups=config.outputs.molecule_groups,
            bond_cutoffs=config.outputs.bond_cutoffs(),
            focus_atoms=focus_atoms,
        )
        _summarize_groups(groups)

    warnings = [
        *sequence.metadata.get("discovery_warnings", ()),
        *descriptor_warnings,
        *bond_warnings,
    ]
    missing_features = [frame.label for frame in sequence.frames if not frame.features]
    if missing_features:
        warnings.append(
            "No electronic features were loaded for frame(s): " + ", ".join(missing_features)
        )
    warnings.extend(count_warnings)
    spin_frames = {
        frame.label
        for frame in sequence.frames
        if any(_as_finite_float(item.attributes.get("spin")) is not None for item in frame.features)
    }
    missing_spin = [frame.label for frame in sequence.frames if frame.label not in spin_frames]
    if missing_spin and (spin_frames or "spin" in descriptors):
        warnings.append(
            "No spin populations (spin/ACF.dat) were loaded for frame(s): "
            + ", ".join(missing_spin)
            + "; spin is missing there, not zero."
        )
    without_net_charges: dict[str, list[str]] = {}
    for frame in sequence.frames:
        reason = frame.metadata.get("net_charge_unavailable")
        if isinstance(reason, str):
            without_net_charges.setdefault(reason, []).append(frame.label)
    warnings.extend(
        f"No net charges for frame(s) {', '.join(labels)} ({reason}); their Bader charges "
        "are electron populations only."
        for reason, labels in without_net_charges.items()
    )
    missing_energies = [frame.label for frame in sequence.frames if frame.energy is None]
    if missing_energies:
        warnings.append("No VASP energy was found for frame(s): " + ", ".join(missing_energies))
    inferred_symbols = [
        frame.label for frame in sequence.frames if frame.metadata.get("symbols_inferred") is True
    ]
    if inferred_symbols:
        warnings.append(
            "VASP 4 element symbols were inferred for frame(s): " + ", ".join(inferred_symbols)
        )
    if selection is not None:
        warnings.extend(selection.warnings)
    warnings.extend(
        _identity_warnings(sequence, tracking.atom_matches, focus_atoms, "focus", "the focus uses")
    )
    bond_atoms = (index for item in bonds for index in (item["atom_a"], item["atom_b"]))
    warnings.extend(
        _identity_warnings(sequence, tracking.atom_matches, bond_atoms, "bond", "bond rows use")
    )
    if groups is not None:
        warnings.extend(group_warnings)
        group_atoms = (atom for item in groups["groups"] for atom in item["atoms"])
        warnings.extend(
            _identity_warnings(
                sequence, tracking.atom_matches, group_atoms, "group", "group sums use"
            )
        )

    sources = fingerprint_files(_source_files(sequence, config_path))
    transitions = [
        {
            "frame_before": atom_matches.previous_frame_index,
            "frame_after": atom_matches.current_frame_index,
            "atom_matches": atom_matches.to_dict(),
            "feature_matches": feature_matches.to_dict(),
        }
        for atom_matches, feature_matches in zip(
            tracking.atom_matches, tracking.feature_matches, strict=True
        )
    ]
    events = _normalize_events(
        tracking.events,
        config.events.medium_confidence_threshold,
        config.events.high_confidence_threshold,
    )
    series_kind = sequence.metadata["series_kind"]
    barrier = _locate_barrier(sequence, series_kind)
    for event in events:
        event["barrier_position"] = _barrier_position(event, barrier["frames"])
    # Numbering, confidences, and barrier labels already used every event, so weak
    # critical-point events leave gaps in the IDs, as a focus does.
    min_field = config.events.critical_point_min_field
    suppressed: dict[str, dict[str, int]] = {}
    if min_field:
        fields = critical_point_fields(sequence)
        kept = []
        for event in events:
            kind = weak_critical_point_kind(
                event["type"],
                event["frame_before"],
                event["frame_after"],
                event["source_ids"],
                event["target_ids"],
                fields,
                min_field,
            )
            if kind is None:
                kept.append(event)
            else:
                counts = suppressed.setdefault(kind, {})
                counts[event["type"]] = counts.get(event["type"], 0) + 1
        events = kept
    analysis: dict[str, Any] = {
        "schema_version": "0.1",
        "software_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(),
        "project": {
            "name": config.project_name,
            "series_kind": series_kind,
            "description": "Deformation-aware correspondence of atoms and electronic features across an ordered periodic sequence.",
            "metadata": dict(config.metadata),
        },
        "configuration": config.to_dict(),
        "provenance": {
            "environment": environment_record(project_directory=config_path.parent),
            "source_files": sources,
            "source_root": sequence.metadata.get("source_root"),
            "descriptor_producers": sorted(
                {
                    str(producer)
                    for frame in sequence.frames
                    for producer in frame.metadata.get("descriptor_producers", [])
                }
            ),
            "descriptor_documents": descriptor_documents,
        },
        "warnings": warnings,
        "frames": [_frame_record(frame) for frame in sequence.frames],
        "critical_point_counts": critical_point_counts,
        "transitions": transitions,
        "events": events,
        "tracks": tracks,
        "track_summary": track_summary,
        "bonds": bonds,
        "barrier": barrier,
        "diagnostics": tracking.diagnostics,
    }
    if groups is not None:
        analysis["atom_groups"] = groups
    if min_field:
        analysis["suppressed_events"] = {
            "critical_point_min_field": dict(min_field),
            "counts": suppressed,
        }
    if focus_atoms:
        analysis.update(_focus_view(sequence, focus_atoms, analysis))
        # Spectators exist only outside a focus. The floor uses every track, while
        # only the reported events are annotated.
        analysis["noise_floor"] = noise_floor(sequence, tracks, focus_atoms)
        annotate_events(analysis["events"], sequence, tracks, analysis["noise_floor"])
    if selection is not None:
        analysis["focus"] = auto_focus_record(selection, analysis.get("focus"), analysis)
    return analysis


def load_and_analyze(
    config_path: str | Path,
    *,
    strict: bool = True,
) -> tuple[dict[str, Any], ElecTraceConfig]:
    """Load a configured VASP path and return its analysis plus configuration."""

    path = Path(config_path).expanduser().resolve()
    config = load_config(path)
    descriptor_dir = config.inputs.descriptor_dir
    sequence = load_sequence(
        _resolve(path.parent, config.inputs.root),
        descriptor_dir=None if descriptor_dir is None else _resolve(path.parent, descriptor_dir),
        prefer_contcar=config.inputs.prefer_contcar,
        mode=config.series_kind,
        strict=strict,
        structure_name=config.inputs.structure_name,
        fallback_structure_name=config.inputs.fallback_structure_name,
        descriptor_name=config.inputs.descriptor_name,
        descriptor_source=config.inputs.descriptor_source,
    )
    return analyze_sequence(sequence, config, path), config


def _without_match_details(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Drop each frame's feature list and the atom and feature match edges.

    Every feature observation except its voxel support is already in ``tracks``,
    unless a focus filtered them, and events keep their own match evidence. Counts,
    unmatched IDs, and match diagnostics stay. The configuration record says the
    details were left out even when the analysis itself ran with the default.
    """

    def without(record: Mapping[str, Any], key: str) -> dict[str, Any]:
        return {name: value for name, value in record.items() if name != key}

    configuration = analysis["configuration"]
    return {
        **analysis,
        "configuration": {
            **configuration,
            "outputs": {**configuration["outputs"], "match_details": False},
        },
        "frames": [without(frame, "features") for frame in analysis["frames"]],
        "transitions": [
            {
                **transition,
                "atom_matches": without(transition["atom_matches"], "edges"),
                "feature_matches": without(transition["feature_matches"], "edges"),
            }
            for transition in analysis["transitions"]
        ],
    }


_KNOWN_BUNDLE_ARTIFACTS = frozenset(
    {
        "analysis.json",
        "atom_groups.csv",
        "bonds.csv",
        "events.csv",
        "feature_tracks.csv",
        "report.html",
        "manifest.json",
        "track_summary.csv",
    }
)


def write_analysis_bundle(
    analysis: Mapping[str, Any],
    config: ElecTraceConfig,
    directory: Path,
    overwrite: bool,
) -> AnalysisArtifacts:
    """Write selected JSON, CSV, and HTML products plus an integrity manifest."""

    if directory.exists() and any(directory.iterdir()) and not overwrite:
        raise FileExistsError(
            f"Output directory is not empty: {directory}. Use overwrite=True to replace known artifacts."
        )
    directory.mkdir(parents=True, exist_ok=True)
    # The CSV files hold no frame feature lists or match edges; callers keep the full analysis.
    document = analysis if config.outputs.match_details else _without_match_details(analysis)
    written: list[Path] = []
    if config.outputs.json:
        written.append(write_json(document, directory / "analysis.json"))
    if config.outputs.csv:
        written.append(write_events_csv(analysis["events"], directory / "events.csv"))
        written.append(write_tracks_csv(analysis["tracks"], directory / "feature_tracks.csv"))
        written.append(
            write_track_summary_csv(analysis["track_summary"], directory / "track_summary.csv")
        )
        written.append(write_bonds_csv(analysis["bonds"], directory / "bonds.csv"))
        if "atom_groups" in analysis:
            written.append(
                write_atom_groups_csv(
                    analysis["atom_groups"]["groups"], directory / "atom_groups.csv"
                )
            )
    if config.outputs.html_report:
        written.append(render_html_report(document, directory / "report.html"))

    selected = {item.name for item in written} | {"manifest.json"}
    if overwrite:
        for name in _KNOWN_BUNDLE_ARTIFACTS - selected:
            artifact = directory / name
            if artifact.is_file() or artifact.is_symlink():
                artifact.unlink()
    manifest = {
        "schema_version": "electrace.bundle.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "files": [
            {"path": item.name, "size_bytes": item.stat().st_size, "sha256": sha256_file(item)}
            for item in written
        ],
    }
    manifest_path = directory / "manifest.json"
    atomic_write_text(
        manifest_path, json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    written.append(manifest_path)
    return AnalysisArtifacts(dict(analysis), directory, tuple(written))


def run_project(
    config_path: str | Path,
    *,
    output_directory: str | Path | None = None,
    strict: bool = True,
    overwrite: bool = False,
) -> AnalysisArtifacts:
    """Load, analyze, and write a configured ElecTrace project."""

    path = Path(config_path).expanduser().resolve()
    analysis, config = load_and_analyze(path, strict=strict)
    configured_output = Path(config.outputs.directory).expanduser()
    output = configured_output if output_directory is None else output_directory
    return write_analysis_bundle(analysis, config, _resolve(path.parent, output), overwrite)
