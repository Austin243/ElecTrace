"""Machine-readable ElecTrace result exporters.

CSV cells that could be interpreted as spreadsheet formulas are prefixed with
an apostrophe.  The check ignores leading Unicode whitespace, so values such
as ``"  =1+1"`` are neutralized as well as values beginning directly with
``=``, ``+``, ``-``, or ``@``.  Other scalar values are preserved verbatim;
structured cells are encoded as strict JSON.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from electrace._files import atomic_output_path


def _strict_json_dumps(value: Any, *, context: str, **kwargs: Any) -> str:
    """Serialize *value* as sorted, standards-compliant JSON with an actionable error."""

    try:
        return json.dumps(value, allow_nan=False, sort_keys=True, **kwargs)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} contains a non-finite or non-JSON value: {exc}") from exc


def _safe_csv_string(value: str) -> str:
    """Neutralize formulas while preserving every non-formula-like string."""

    return f"'{value}" if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _flatten_csv_rows(rows: Iterable[Mapping[str, Any]], *, context: str) -> list[dict[str, Any]]:
    """Validate CSV cells and encode structured cells as strict JSON."""

    flattened_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=1):
        flattened: dict[str, Any] = {}
        for key, value in row.items():
            encoded = _strict_json_dumps(value, context=f"{context} row {row_index}, field {key!r}")
            if isinstance(value, (Mapping, list, tuple)):
                flattened[key] = encoded
            elif isinstance(value, str):
                flattened[key] = _safe_csv_string(value)
            else:
                flattened[key] = value
        flattened_rows.append(flattened)
    return flattened_rows


def _write_csv(
    records: Iterable[Mapping[str, Any]], output: Path, context: str, preferred: list[str]
) -> Path:
    """Write formula-neutralized rows: the preferred columns, then the others by name."""

    rows = _flatten_csv_rows(records, context=context)
    fields = preferred + sorted({key for row in rows for key in row if key not in preferred})
    with (
        atomic_output_path(output) as temporary,
        temporary.open("w", encoding="utf-8", newline="") as handle,
    ):
        writer = csv.DictWriter(handle, fieldnames=fields)
        # Field names can be untrusted too.
        writer.writerow({field: _safe_csv_string(field) for field in fields})
        writer.writerows(rows)
    return output


def write_json(data: Mapping[str, Any], output: Path) -> Path:
    """Write the canonical analysis representation as stable, pretty JSON."""

    # Streamed: the indenting encoder's tokens would otherwise all be held for one final join.
    with (
        atomic_output_path(output) as temporary,
        temporary.open("w", encoding="utf-8", newline="\n") as handle,
    ):
        try:
            json.dump(data, handle, allow_nan=False, sort_keys=True, indent=2)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"analysis JSON contains a non-finite or non-JSON value: {exc}"
            ) from exc
        handle.write("\n")
    return output


def write_events_csv(events: Iterable[Mapping[str, Any]], output: Path) -> Path:
    """Write a flat, formula-neutralized event table for data tools."""

    preferred = [
        "event_id",
        "type",
        "start_frame",
        "end_frame",
        "source_ids",
        "target_ids",
        "confidence",
        "persistent",
        "description",
    ]
    return _write_csv(events, output, "events CSV", preferred)


def write_track_summary_csv(summary: Iterable[Mapping[str, Any]], output: Path) -> Path:
    """Write one formula-neutralized row per track with a column group per quantity."""

    flattened: list[dict[str, Any]] = []
    for record in summary:
        row = {key: value for key, value in record.items() if key != "changes"}
        for quantity, change in record.get("changes", {}).items():
            step = change.get("largest_step") or {}
            row.update(
                {
                    f"{quantity}_observed": change.get("observed"),
                    f"{quantity}_net_change": change.get("net_change"),
                    f"{quantity}_range": change.get("range"),
                    f"{quantity}_largest_step_delta": step.get("delta"),
                    f"{quantity}_largest_step_frame_before": step.get("frame_before"),
                    f"{quantity}_largest_step_frame_after": step.get("frame_after"),
                }
            )
        flattened.append(row)
    preferred = [
        "track_id",
        "kind",
        "first_frame",
        "last_frame",
        "observations",
        "spans_path",
        "parent_track_ids",
    ]
    # Keep each quantity's columns together, in summary order.
    fields = list(dict.fromkeys([*preferred, *(key for row in flattened for key in row)]))
    return _write_csv(flattened, output, "track-summary CSV", fields)


def write_bonds_csv(bonds: Iterable[Mapping[str, Any]], output: Path) -> Path:
    """Write one formula-neutralized row per bond and frame, grouped by bond."""

    preferred = [
        "bond_id",
        "frame",
        "frame_label",
        "atom_a",
        "element_a",
        "atom_b",
        "element_b",
        "lattice_translation",
        "distance",
        "field",
        "laplacian",
        "icohp",
        "icobi",
        "feature_ids",
        "track_ids",
    ]
    return _write_csv(bonds, output, "bonds CSV", preferred)


def write_atom_groups_csv(groups: Iterable[Mapping[str, Any]], output: Path) -> Path:
    """Write one row per atom group, charge producer, and frame; atoms are in the JSON."""

    fields = [
        "group_id",
        "source",
        "formula",
        "atom_count",
        "producer",
        "frame",
        "frame_label",
        "charge",
        "net_charge",
        "spin",
    ]
    rows = (
        {
            "group_id": group["group_id"],
            "source": group["source"],
            "formula": group["formula"],
            "atom_count": len(group["atoms"]),
            "producer": group["producer"],
            "frame": value["frame"],
            "frame_label": value["frame_label"],
            "charge": value["charge"],
            "net_charge": value["net_charge"],
            "spin": value["spin"],
        }
        for group in groups
        for value in group["values"]
    )
    return _write_csv(rows, output, "atom-groups CSV", fields)


def write_tracks_csv(tracks: Iterable[Mapping[str, Any]], output: Path) -> Path:
    """Write formula-neutralized feature-track observations in long form."""

    preferred = [
        "track_id",
        "frame",
        "feature_id",
        "kind",
        "fractional_x",
        "fractional_y",
        "fractional_z",
        "charge",
        "volume",
        "confidence",
    ]
    return _write_csv(tracks, output, "feature-tracks CSV", preferred)
