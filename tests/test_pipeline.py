from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

from electrace.config import load_config, write_default_config
from electrace.demo import generate_demo
from electrace.pipeline import load_and_analyze, run_project
from electrace.provenance import sha256_file

HCN_FRAMES = Path(__file__).parents[1] / "examples" / "hcn-hnc" / "frames"


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
        "bonds.csv",
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


def _hcn_analysis(root: Path, *thresholds: str, drop: str | None = None) -> dict[str, Any]:
    frames = root / "frames"
    shutil.copytree(HCN_FRAMES, frames)
    if drop is not None:
        for path in frames.glob(f"*/{drop}"):
            path.unlink()
    config = root / "electrace.toml"
    write_default_config(config, input_root=frames)
    text = config.read_text(encoding="utf-8")
    added = "".join(f"{line}\n" for line in thresholds)
    config.write_text(
        text.replace("oxidation_state = 0.5\n", f"oxidation_state = 0.5\n{added}", 1),
        encoding="utf-8",
    )
    return load_and_analyze(config)[0]


def _untracked_density(analysis: dict[str, Any]) -> list[str]:
    return [item for item in analysis["warnings"] if "raise no events" in item]


def test_density_without_thresholds_is_warned_and_changes_no_events(tmp_path: Path) -> None:
    analysis = _hcn_analysis(tmp_path)

    warning = _untracked_density(analysis)
    assert len(warning) == 1
    assert "field and laplacian" in warning[0]
    assert "[events.descriptor_thresholds]" in warning[0]
    assert len(analysis["events"]) == 10
    assert not any(
        set(event["evidence"].get("descriptor_changes", {})) & {"field", "laplacian"}
        for event in analysis["events"]
    )
    assert any(row["field"] is not None for row in analysis["bonds"])


def test_density_thresholds_silence_the_warning_and_add_events(tmp_path: Path) -> None:
    analysis = _hcn_analysis(tmp_path, "field = 0.05", "laplacian = 0.25")

    assert _untracked_density(analysis) == []
    assert len(analysis["events"]) == 20


def test_density_warning_names_only_the_quantity_without_a_threshold(tmp_path: Path) -> None:
    warning = _untracked_density(_hcn_analysis(tmp_path, "field = 0.05"))

    assert len(warning) == 1
    assert "laplacian" in warning[0]
    assert "field" not in warning[0].replace("field = ", "")


def test_no_density_warning_without_density(tmp_path: Path) -> None:
    assert _untracked_density(_hcn_analysis(tmp_path, drop="critic2.features.json")) == []


def test_init_template_leaves_density_thresholds_commented(tmp_path: Path) -> None:
    config = write_default_config(tmp_path / "electrace.toml")

    assert "# field = 0.05" in config.read_text(encoding="utf-8")
    assert "# laplacian = 0.25" in config.read_text(encoding="utf-8")
    assert not {"field", "laplacian"} & set(load_config(config).events.descriptor_thresholds)
