from __future__ import annotations

import csv
import json
from pathlib import Path

from electrace.demo import generate_demo
from electrace.pipeline import run_project
from electrace.provenance import sha256_file


def test_demo_pipeline_analyzes_and_writes_report_bundle(tmp_path: Path) -> None:
    config_path = generate_demo(tmp_path / "demo")
    artifacts = run_project(config_path)
    analysis = artifacts.analysis

    event_types = [event["type"] for event in analysis["events"]]
    assert "merge" in event_types
    assert "property_change" in event_types
    assert len(analysis["frames"]) == 6
    assert analysis["provenance"]["descriptor_producers"] == ["electrace-demo"]
    assert {path.name for path in artifacts.files} == {
        "analysis.json",
        "events.csv",
        "feature_tracks.csv",
        "manifest.json",
        "report.html",
        "track_summary.csv",
    }

    manifest = json.loads((artifacts.output_directory / "manifest.json").read_text())
    for record in manifest["files"]:
        assert record["sha256"] == sha256_file(artifacts.output_directory / record["path"])
    with (artifacts.output_directory / "events.csv").open(newline="") as handle:
        assert len(list(csv.DictReader(handle))) == len(analysis["events"])
    assert "Electronic event timeline" in (artifacts.output_directory / "report.html").read_text()
