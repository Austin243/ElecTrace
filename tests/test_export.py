from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from electrace.export import write_events_csv, write_json, write_tracks_csv


def test_machine_readable_exports(tmp_path: Path) -> None:
    json_path = write_json({"schema_version": "0.1"}, tmp_path / "analysis.json")
    events_path = write_events_csv(
        [{"event_id": "E1", "type": "birth", "target_ids": ["nna-1"]}],
        tmp_path / "events.csv",
    )
    tracks_path = write_tracks_csv(
        [
            {
                "track_id": "T1",
                "frame": 0,
                "feature_id": "nna-1",
                "attributes": {"accepted": True},
            }
        ],
        tmp_path / "tracks.csv",
    )

    assert json.loads(json_path.read_text())["schema_version"] == "0.1"
    with events_path.open(newline="") as handle:
        assert next(csv.DictReader(handle))["target_ids"] == '["nna-1"]'
    with tracks_path.open(newline="") as handle:
        assert json.loads(next(csv.DictReader(handle))["attributes"]) == {"accepted": True}


def test_json_export_rejects_non_finite_values(tmp_path: Path) -> None:
    output = tmp_path / "analysis.json"
    with pytest.raises(ValueError, match="non-finite or non-JSON"):
        write_json({"bad": float("nan")}, output)
    assert not output.exists()


def test_csv_exports_neutralize_formula_strings(tmp_path: Path) -> None:
    dangerous = '=HYPERLINK("https://example.invalid")'
    path = write_events_csv(
        [{"event_id": dangerous, "type": "birth", "description": dangerous}],
        tmp_path / "events.csv",
    )

    with path.open(newline="") as handle:
        event = next(csv.DictReader(handle))

    assert event["event_id"] == f"'{dangerous}"
    assert event["description"] == f"'{dangerous}"
