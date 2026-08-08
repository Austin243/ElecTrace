from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from electrace.export import (
    ExportDependencyError,
    write_events_csv,
    write_hdf5,
    write_json,
    write_tracks_csv,
)


def test_machine_readable_exports(tmp_path: Path) -> None:
    analysis = {"schema_version": "0.1", "events": [{"type": "birth"}]}
    json_path = write_json(analysis, tmp_path / "analysis.json")
    events_path = write_events_csv(
        [
            {
                "event_id": "E0001",
                "type": "birth",
                "source_ids": [],
                "target_ids": ["nna-1"],
                "confidence": 0.9,
            }
        ],
        tmp_path / "events.csv",
    )
    tracks_path = write_tracks_csv(
        [
            {
                "track_id": "T0001",
                "frame": 0,
                "feature_id": "nna-1",
                "kind": "nna",
                "parent_track_ids": ["T0000"],
                "attributes": {"accepted": True, "note": None},
            }
        ],
        tmp_path / "tracks.csv",
    )

    assert json.loads(json_path.read_text())["schema_version"] == "0.1"
    with events_path.open(newline="") as handle:
        assert next(csv.DictReader(handle))["target_ids"] == '["nna-1"]'
    with tracks_path.open(newline="") as handle:
        track = next(csv.DictReader(handle))
    assert track["track_id"] == "T0001"
    assert json.loads(track["parent_track_ids"]) == ["T0000"]
    assert json.loads(track["attributes"]) == {"accepted": True, "note": None}


@pytest.mark.parametrize("bad_value", [float("nan"), object()])
def test_json_export_rejects_non_finite_and_non_json_values(
    tmp_path: Path, bad_value: object
) -> None:
    output = tmp_path / "analysis.json"

    with pytest.raises(
        ValueError,
        match="analysis JSON contains a non-finite or non-JSON value",
    ):
        write_json({"nested": {"bad": bad_value}}, output)

    assert not output.exists()


def test_hdf5_export_rejects_non_finite_analysis_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "analysis.h5"

    with pytest.raises(
        ValueError,
        match="HDF5 analysis JSON contains a non-finite or non-JSON value",
    ):
        write_hdf5({"nested": [float("inf")]}, output)

    assert not output.exists()


def test_missing_hdf5_dependency_raises_dedicated_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "h5py", None)

    with pytest.raises(ExportDependencyError, match=r"pip install electrace\[hdf5\]"):
        write_hdf5({"schema_version": "0.1"}, tmp_path / "analysis.h5")


@pytest.mark.parametrize(
    "writer,row,context",
    [
        (
            write_events_csv,
            {"event_id": "E1", "metadata": {"bad": float("nan")}},
            "events CSV",
        ),
        (
            write_tracks_csv,
            {"track_id": "T1", "attributes": {"bad": object()}},
            "feature-tracks CSV",
        ),
    ],
)
def test_csv_exports_reject_invalid_nested_json_without_partial_files(
    tmp_path: Path,
    writer: object,
    row: dict[str, object],
    context: str,
) -> None:
    output = tmp_path / "output.csv"

    with pytest.raises(
        ValueError,
        match=rf"{context} row 1, field .* contains a non-finite or non-JSON value",
    ):
        writer([row], output)  # type: ignore[operator]

    assert not output.exists()


@pytest.mark.parametrize(
    "dangerous",
    [
        '=HYPERLINK("https://example.invalid")',
        "+SUM(1,1)",
        "-2+3",
        "@SUM(1,1)",
        " \t=1+1",
    ],
)
def test_csv_exports_neutralize_formula_like_strings(tmp_path: Path, dangerous: str) -> None:
    events_path = write_events_csv(
        [
            {
                "event_id": dangerous,
                "type": "birth",
                "description": dangerous,
                "@untrusted_header": dangerous,
            }
        ],
        tmp_path / "events.csv",
    )
    tracks_path = write_tracks_csv(
        [{"track_id": dangerous, "feature_id": dangerous, "kind": "nna"}],
        tmp_path / "tracks.csv",
    )

    with events_path.open(newline="") as handle:
        event = next(csv.DictReader(handle))
    with tracks_path.open(newline="") as handle:
        track = next(csv.DictReader(handle))

    assert event["event_id"] == f"'{dangerous}"
    assert event["description"] == f"'{dangerous}"
    assert event["'@untrusted_header"] == f"'{dangerous}"
    assert event["type"] == "birth"
    assert track["track_id"] == f"'{dangerous}"
    assert track["feature_id"] == f"'{dangerous}"
    assert track["kind"] == "nna"
