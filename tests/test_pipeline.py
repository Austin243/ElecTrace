from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from electrace.config import ElecTraceConfig
from electrace.demo import generate_demo
from electrace.models import Event, EventType
from electrace.pipeline import (
    _normalize_events,
    load_and_analyze,
    run_project,
    write_analysis_bundle,
)
from electrace.provenance import sha256_file


def _add_ambiguous_demo_feature(config_path: Path) -> str:
    descriptor_path = config_path.parent / "01" / "features.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    duplicate = dict(descriptor["features"][0])
    duplicate["id"] = "ambiguous-duplicate"
    descriptor["features"].append(duplicate)
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    return duplicate["id"]


def test_demo_pipeline_detects_expected_electronic_events(tmp_path: Path) -> None:
    config_path = generate_demo(tmp_path / "demo")
    analysis, _ = load_and_analyze(config_path)

    event_types = [event["type"] for event in analysis["events"]]
    assert "merge" in event_types
    assert event_types.count("birth") == 2
    assert "property_change" in event_types
    assert analysis["project"]["series_kind"] == "neb"
    assert analysis["warnings"] == []
    assert len(analysis["frames"]) == 6
    assert len(analysis["tracks"]) == 15
    assert analysis["provenance"]["descriptor_producers"] == ["electrace-demo"]
    assert all(source["sha256"] for source in analysis["provenance"]["source_files"])


def test_strict_matching_fails_and_permissive_matching_suppresses_ambiguous_claims(
    tmp_path: Path,
) -> None:
    config_path = generate_demo(tmp_path / "demo")
    duplicate_id = _add_ambiguous_demo_feature(config_path)

    with pytest.raises(ValueError, match="Ambiguous correspondence in strict mode"):
        run_project(config_path)
    assert not (config_path.parent / "electrace-results").exists()

    analysis, _ = load_and_analyze(config_path, strict=False)

    assert any("Ambiguous feature correspondence" in warning for warning in analysis["warnings"])
    first_transition = analysis["transitions"][0]["feature_matches"]
    assert first_transition["diagnostics"]["ambiguous"] is True
    assert first_transition["diagnostics"]["ambiguities"]
    assert all(
        duplicate_id not in {*event["source_ids"], *event["target_ids"]}
        for event in analysis["events"]
    )
    duplicate_track = next(
        item for item in analysis["tracks"] if item["feature_id"] == duplicate_id
    )
    assert duplicate_track["parent_track_ids"] == []


def test_descriptor_document_provenance_and_warnings_reach_analysis(
    tmp_path: Path,
) -> None:
    config_path = generate_demo(tmp_path / "demo")
    descriptor_path = config_path.parent / "00" / "features.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["metadata"]["warnings"] = ["synthetic parser warning <unsafe>"]
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

    analysis, _ = load_and_analyze(config_path)

    assert (
        "Descriptor warning (00, electrace-demo): synthetic parser warning <unsafe>"
        in analysis["warnings"]
    )
    first = analysis["provenance"]["descriptor_documents"][0]
    assert first["frame"] == {"index": 0, "label": "00"}
    assert first["producer"] == {"name": "electrace-demo", "version": "0.1"}
    assert first["metadata"]["warnings"] == ["synthetic parser warning <unsafe>"]
    assert "charge_convention" in first["metadata"]


def test_result_bundle_is_complete_and_integrity_manifest_matches(tmp_path: Path) -> None:
    config_path = generate_demo(tmp_path / "demo")
    artifacts = run_project(config_path)

    assert {path.name for path in artifacts.files} == {
        "analysis.json",
        "events.csv",
        "feature_tracks.csv",
        "manifest.json",
        "report.html",
    }
    manifest = json.loads((artifacts.output_directory / "manifest.json").read_text())
    for record in manifest["files"]:
        path = artifacts.output_directory / record["path"]
        assert record["sha256"] == sha256_file(path)
    with (artifacts.output_directory / "events.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(artifacts.analysis["events"])
    assert "Electronic event timeline" in (artifacts.output_directory / "report.html").read_text()

    with pytest.raises(FileExistsError):
        run_project(config_path)
    rerun = run_project(config_path, overwrite=True)
    assert rerun.output_directory == artifacts.output_directory


def test_overwrite_removes_only_unselected_known_bundle_artifacts(
    tmp_path: Path,
) -> None:
    analysis: dict[str, Any] = {
        "schema_version": "0.1",
        "project": {"name": "bundle regression"},
        "events": [{"event_id": "E1", "type": "birth"}],
        "tracks": [{"track_id": "T1", "frame": 0}],
        "frames": [],
    }
    config = ElecTraceConfig()
    output = tmp_path / "bundle"
    first = write_analysis_bundle(analysis, config, output)
    assert {path.name for path in first.files} == {
        "analysis.json",
        "events.csv",
        "feature_tracks.csv",
        "manifest.json",
        "report.html",
    }
    (output / "analysis.h5").write_bytes(b"stale known artifact")
    user_file = output / "research-notes.txt"
    user_file.write_text("preserve me", encoding="utf-8")

    config.outputs.json = False
    config.outputs.html_report = False
    config.outputs.hdf5 = False
    rerun = write_analysis_bundle(analysis, config, output, overwrite=True)

    assert {path.name for path in rerun.files} == {
        "events.csv",
        "feature_tracks.csv",
        "manifest.json",
    }
    assert not (output / "analysis.json").exists()
    assert not (output / "analysis.h5").exists()
    assert not (output / "report.html").exists()
    assert user_file.read_text(encoding="utf-8") == "preserve me"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert {record["path"] for record in manifest["files"]} == {
        "events.csv",
        "feature_tracks.csv",
    }


def test_overwrite_never_follows_reserved_artifact_symlinks(tmp_path: Path) -> None:
    analysis: dict[str, Any] = {
        "schema_version": "0.1",
        "project": {"name": "symlink regression"},
        "events": [],
        "tracks": [],
        "frames": [],
    }
    config = ElecTraceConfig()
    output = tmp_path / "bundle"
    write_analysis_bundle(analysis, config, output)
    external = tmp_path / "external.json"
    external.write_text("do not replace", encoding="utf-8")
    analysis_path = output / "analysis.json"
    analysis_path.unlink()
    analysis_path.symlink_to(external)
    unknown_target = tmp_path / "unknown-target.txt"
    unknown_target.write_text("user data", encoding="utf-8")
    unknown_link = output / "user-link.txt"
    unknown_link.symlink_to(unknown_target)

    write_analysis_bundle(analysis, config, output, overwrite=True)

    assert external.read_text(encoding="utf-8") == "do not replace"
    assert not analysis_path.is_symlink()
    assert json.loads(analysis_path.read_text(encoding="utf-8"))["schema_version"] == "0.1"
    assert unknown_link.is_symlink()
    assert unknown_link.read_text(encoding="utf-8") == "user data"


def test_late_export_failure_never_publishes_a_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysis: dict[str, Any] = {
        "schema_version": "0.1",
        "project": {"name": "transaction regression"},
        "events": [{"event_id": "E1", "type": "birth"}],
        "tracks": [{"track_id": "T1", "frame": 0}],
        "frames": [],
    }
    config = ElecTraceConfig()
    existing = tmp_path / "existing"
    write_analysis_bundle(analysis, config, existing)
    before = {path.name: path.read_bytes() for path in existing.iterdir() if path.is_file()}

    def fail_report(*_args: object, **_kwargs: object) -> Path:
        raise RuntimeError("forced late report failure")

    monkeypatch.setattr("electrace.pipeline.render_html_report", fail_report)

    with pytest.raises(RuntimeError, match="forced late report failure"):
        write_analysis_bundle(analysis, config, existing, overwrite=True)
    after = {path.name: path.read_bytes() for path in existing.iterdir() if path.is_file()}
    assert after == before

    new_output = tmp_path / "new"
    with pytest.raises(RuntimeError, match="forced late report failure"):
        write_analysis_bundle(analysis, config, new_output)
    assert new_output.is_dir()
    assert not any(new_output.iterdir())


def test_load_and_analyze_forwards_all_configured_input_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "images"
    descriptors = tmp_path / "descriptors"
    root.mkdir()
    descriptors.mkdir()
    config_path = tmp_path / "electrace.toml"
    config_path.write_text(
        """[project]
name = "custom inputs"
series_kind = "ordered"

[inputs]
root = "images"
descriptor_dir = "descriptors"
structure_name = "FINAL.vasp"
fallback_structure_name = "START.vasp"
descriptor_name = "electronic.json"
descriptor_source = "merge"
prefer_contcar = false
""",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_load_sequence(path: Path, **kwargs: Any) -> object:
        captured["path"] = path
        captured.update(kwargs)
        return sentinel

    def fake_analyze_sequence(
        sequence: object, config: object, *, config_path: Path, strict: bool
    ) -> dict[str, bool]:
        assert sequence is sentinel
        captured["config_path"] = config_path
        captured["analysis_strict"] = strict
        return {"ok": True}

    monkeypatch.setattr("electrace.pipeline.load_sequence", fake_load_sequence)
    monkeypatch.setattr("electrace.pipeline.analyze_sequence", fake_analyze_sequence)

    analysis, _ = load_and_analyze(config_path)

    assert analysis == {"ok": True}
    assert captured["path"] == root.resolve()
    assert captured["descriptor_dir"] == descriptors.resolve()
    assert captured["structure_name"] == "FINAL.vasp"
    assert captured["fallback_structure_name"] == "START.vasp"
    assert captured["descriptor_name"] == "electronic.json"
    assert captured["descriptor_source"] == "merge"
    assert captured["prefer_contcar"] is False
    assert captured["analysis_strict"] is True


def test_event_normalization_uses_observed_persistence_end_frame() -> None:
    event = Event(
        type=EventType.PROPERTY_CHANGE,
        frame_before=10,
        frame_after=20,
        source_ids=("before",),
        target_ids=("after",),
        confidence=0.8,
        persistence=2,
        metadata={
            "persistence_status": "confirmed",
            "persistence_end_frame": 40,
        },
    )

    normalized = _normalize_events((event,), minimum_persistence=2)

    assert normalized[0]["start_frame"] == 20
    assert normalized[0]["end_frame"] == 40
    assert normalized[0]["confidence_label"] == "high"


def test_event_confidence_labels_use_configured_inclusive_thresholds() -> None:
    events = tuple(
        Event(
            type=EventType.BIRTH,
            frame_before=0,
            frame_after=index,
            target_ids=(f"f{index}",),
            confidence=confidence,
            metadata={"persistence_status": "confirmed"},
        )
        for index, confidence in enumerate((0.75, 0.50, 0.49), start=1)
    )

    normalized = _normalize_events(
        events,
        minimum_persistence=1,
        medium_confidence_threshold=0.50,
        high_confidence_threshold=0.75,
    )

    assert [event["confidence_label"] for event in normalized] == [
        "high",
        "medium",
        "low",
    ]
    assert all(event["persistent"] is True for event in normalized)


def test_transient_status_is_not_reported_as_persistent_when_minimum_is_one() -> None:
    event = Event(
        type=EventType.BIRTH,
        frame_before=0,
        frame_after=1,
        target_ids=("f",),
        confidence=0.9,
        persistence=1,
        metadata={"persistence_status": "transient"},
    )

    normalized = _normalize_events((event,), minimum_persistence=1)

    assert normalized[0]["persistent"] is False
