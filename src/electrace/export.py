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

from electrace._files import atomic_output_path, atomic_write_text


class ExportDependencyError(RuntimeError):
    """Raised when a requested export needs an unavailable optional package."""


def _strict_json_dumps(value: Any, *, context: str, **kwargs: Any) -> str:
    """Serialize *value* as standards-compliant JSON with an actionable error."""

    try:
        return json.dumps(value, allow_nan=False, **kwargs)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} contains a non-finite or non-JSON value: {exc}") from exc


def _safe_csv_string(value: str) -> str:
    """Neutralize formulas while preserving every non-formula-like string."""

    candidate = value.lstrip()
    return f"'{value}" if candidate.startswith(("=", "+", "-", "@")) else value


def _flatten_csv_rows(rows: Iterable[Mapping[str, Any]], *, context: str) -> list[dict[str, Any]]:
    """Validate CSV cells and encode structured cells as strict JSON."""

    flattened_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=1):
        flattened: dict[str, Any] = {}
        for key, value in row.items():
            if not isinstance(key, str):
                raise ValueError(f"{context} row {row_index} has non-string field name {key!r}")
            field_context = f"{context} row {row_index}, field {key!r}"
            encoded = _strict_json_dumps(value, context=field_context, sort_keys=True)
            if isinstance(value, (Mapping, list, tuple)):
                flattened[key] = encoded
            elif isinstance(value, str):
                flattened[key] = _safe_csv_string(value)
            else:
                flattened[key] = value
        flattened_rows.append(flattened)
    return flattened_rows


def _write_csv(rows: list[dict[str, Any]], fields: list[str], output: Path) -> None:
    """Write validated rows, neutralizing formula-like untrusted headers too."""

    with (
        atomic_output_path(output) as temporary,
        temporary.open("w", encoding="utf-8", newline="") as handle,
    ):
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writerow({field: _safe_csv_string(field) for field in fields})
        writer.writerows(rows)


def write_json(data: Mapping[str, Any], path: str | Path) -> Path:
    """Write the canonical analysis representation as stable, pretty JSON."""

    payload = _strict_json_dumps(
        data,
        context="analysis JSON",
        indent=2,
        sort_keys=True,
    )
    output = Path(path)
    atomic_write_text(output, payload + "\n")
    return output


def write_events_csv(events: Iterable[Mapping[str, Any]], path: str | Path) -> Path:
    """Write a flat, formula-neutralized event table for data tools."""

    rows = _flatten_csv_rows(events, context="events CSV")
    output = Path(path)
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
    extras = sorted({key for row in rows for key in row if key not in preferred})
    fields = preferred + extras
    _write_csv(rows, fields, output)
    return output


def write_tracks_csv(tracks: Iterable[Mapping[str, Any]], path: str | Path) -> Path:
    """Write formula-neutralized feature-track observations in long form."""

    rows = _flatten_csv_rows(tracks, context="feature-tracks CSV")
    output = Path(path)
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
    extras = sorted({key for row in rows for key in row if key not in preferred})
    fields = preferred + extras
    _write_csv(rows, fields, output)
    return output


def write_hdf5(data: Mapping[str, Any], path: str | Path) -> Path:
    """Write the canonical JSON into an HDF5 container.

    Keeping the canonical document as a UTF-8 dataset provides a lossless,
    versionable interchange layer while leaving room for optimized numerical
    datasets in later schema versions.
    """

    payload = _strict_json_dumps(
        data,
        context="HDF5 analysis JSON",
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ExportDependencyError("HDF5 export requires `pip install electrace[hdf5]`") from exc

    output = Path(path)
    with (
        atomic_output_path(output) as temporary,
        h5py.File(temporary, "w") as handle,
    ):
        handle.attrs["schema"] = "electrace.analysis"
        handle.attrs["schema_version"] = str(data.get("schema_version", "0.1"))
        handle.create_dataset("analysis_json", data=payload)
    return output
