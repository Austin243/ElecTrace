from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from electrace.config import ElecTraceConfig, EventSettings, MatchingSettings
from electrace.models import Feature, Frame, Sequence
from electrace.pipeline import analyze_sequence, write_analysis_bundle

ROOT = Path(__file__).parents[1]
CELL = ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0))
# Every adjacent step stays below the 0.5 ICOHP and 0.10 charge event thresholds.
ICOHP = (-0.20, -0.45, -0.60, -0.90, -1.00)
CHARGE = (None, 0.10, 0.17, 0.20, 0.25)
UNOBSERVED = {"observed": 0, "net_change": None, "range": None, "largest_step": None}


def gradual_analysis() -> tuple[dict[str, Any], ElecTraceConfig]:
    frames = []
    for position, (icohp, charge) in enumerate(zip(ICOHP, CHARGE, strict=True)):
        features = [
            Feature("bond", "bond_descriptor", (0.5, 0.5, 0.5), charge, attributes={"icohp": icohp})
        ]
        if position == len(ICOHP) - 1:
            features.append(
                Feature(
                    "late", "non_nuclear_attractor", (0.1, 0.1, 0.1), attributes={"icohp": -0.3}
                )
            )
        # Sampled frame indices need not be contiguous.
        frames.append(Frame(2 * position, f"{2 * position:02d}", CELL, (), tuple(features)))
    config = ElecTraceConfig(
        matching=MatchingSettings(descriptor_scales={"icohp": 0.5, "rho": 0.02}),
        events=EventSettings(descriptor_thresholds={"icohp": 0.5}),
    )
    return analyze_sequence(Sequence(tuple(frames)), config), config


def test_track_summary_reports_gradual_change_that_raises_no_event() -> None:
    analysis, _ = gradual_analysis()
    bond, late = analysis["track_summary"]

    assert not [event for event in analysis["events"] if event["type"] == "property_change"]
    assert (bond["track_id"], bond["first_frame"], bond["last_frame"]) == ("T00001", 0, 8)
    assert (bond["observations"], bond["spans_path"], bond["parent_track_ids"]) == (5, True, [])
    assert list(bond["changes"]) == ["charge", "volume", "icohp", "rho"]
    icohp = bond["changes"]["icohp"]
    assert icohp["observed"] == 5
    assert icohp["net_change"] == pytest.approx(-0.80)
    assert icohp["range"] == pytest.approx(0.80)
    assert (icohp["largest_step"]["frame_before"], icohp["largest_step"]["frame_after"]) == (4, 6)
    assert icohp["largest_step"]["delta"] == pytest.approx(-0.30)
    charge = bond["changes"]["charge"]
    assert charge["observed"] == 4
    assert charge["net_change"] is None
    assert charge["range"] == pytest.approx(0.15)
    assert (charge["largest_step"]["frame_before"], charge["largest_step"]["frame_after"]) == (2, 4)
    assert bond["changes"]["volume"] == bond["changes"]["rho"] == UNOBSERVED
    assert (late["first_frame"], late["last_frame"], late["spans_path"]) == (8, 8, False)
    assert late["changes"]["icohp"] == {**UNOBSERVED, "observed": 1}


def test_track_summary_csv_keeps_missing_values_empty(tmp_path: Path) -> None:
    analysis, config = gradual_analysis()
    artifacts = write_analysis_bundle(analysis, config, tmp_path / "results")

    with (artifacts.output_directory / "track_summary.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["track_id"] for row in rows] == ["T00001", "T00002"]
    assert float(rows[0]["icohp_net_change"]) == pytest.approx(-0.80)
    assert rows[0]["icohp_largest_step_frame_before"] == "4"
    assert rows[0]["charge_net_change"] == rows[0]["volume_range"] == ""
    assert "Whole-path track changes" in (artifacts.output_directory / "report.html").read_text()


def test_track_summary_matches_analysis_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "schemas/analysis-v0.1.schema.json").read_text(encoding="utf-8"))
    analysis, _ = gradual_analysis()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(analysis, schema)

    analysis["track_summary"][0]["changes"]["icohp"]["largest_step"] = {"delta": -0.3}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(analysis, schema)
