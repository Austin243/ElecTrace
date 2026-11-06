"""A descriptor file missing from a frame is missing evidence, not a change in the chemistry."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from electrace.config import write_default_config
from electrace.pipeline import load_and_analyze

ROOT = Path(__file__).parents[1]
POSCAR = "H\n1\n10 0 0\n0 10 0\n0 0 10\nH\n2\nDirect\n0.1 0.1 0.1\n0.6 0.6 0.6\n"
LABELS = ("00", "01", "02", "03", "04")


def _frames(root: Path, *, charges: dict[str, float] | None = None) -> Path:
    """Write frames whose atom 0 has charge 1.0, or the given charge per label."""

    for label in LABELS:
        directory = root / label
        directory.mkdir(parents=True)
        (directory / "POSCAR").write_text(POSCAR, encoding="utf-8")
        first = (charges or {}).get(label, 1.0)
        (directory / "ACF.dat").write_text(
            f"1 0 0 0 {first} 0 10\n2 0 0 0 1.0 0 10\n", encoding="utf-8"
        )
    return root


def _tool(directory: Path, *feature_ids: str, name: str = "features.json") -> None:
    """Write a canonical document whose features sit far from the atoms and each other."""

    features = [
        {
            "id": feature_id,
            "kind": "elf_basin",
            "fractional_position": [0.3, 0.2 + 0.2 * number, 0.8],
        }
        for number, feature_id in enumerate(feature_ids)
    ]
    document = {
        "schema_version": "electrace.features.v1",
        "producer": {"name": "tool"},
        "features": features,
    }
    (directory / name).write_text(json.dumps(document), encoding="utf-8")


def _analyze(root: Path, **inputs: str) -> dict[str, Any]:
    config = root.parent / f"{root.name}.toml"
    write_default_config(config, input_root=root, overwrite=True)
    if inputs:
        lines = "".join(f'{key} = "{value}"\n' for key, value in inputs.items())
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("[inputs]\n", f"[inputs]\n{lines}", 1), encoding="utf-8")
    return load_and_analyze(config)[0]


def _events(analysis: dict[str, Any], *types: str) -> list[tuple[str, int, int, str]]:
    return [
        (
            event["type"],
            event["frame_before"],
            event["frame_after"],
            (event["source_ids"] or event["target_ids"])[0],
        )
        for event in analysis["events"]
        if event["type"] in types
    ]


def _warnings(analysis: dict[str, Any], text: str) -> list[str]:
    return [warning for warning in analysis["warnings"] if text in warning]


def test_missing_acf_in_the_hcn_example_gives_no_false_births_or_deaths(tmp_path: Path) -> None:
    root = tmp_path / "frames"
    shutil.copytree(ROOT / "examples" / "hcn-hnc" / "frames", root)
    clean = _analyze(root)
    (root / "03" / "ACF.dat").unlink()

    analysis = _analyze(root)

    assert _events(analysis, "birth", "death") == []
    assert analysis["missing_descriptors"] == [
        {
            "producer": "bader",
            "file": "ACF.dat",
            "frames": [3],
            "frame_labels": ["03"],
            "events_left_out": {"birth": 3, "death": 3},
        }
    ]
    warning = _warnings(analysis, "ACF.dat")
    assert len(warning) == 1
    assert "bader output ACF.dat is missing from frame(s) 03" in warning[0]
    assert "3 deaths and 3 births" in warning[0]
    # What other producers and other steps found is untouched.
    assert _events(analysis, "connectivity_change") == _events(clean, "connectivity_change")
    assert analysis["diagnostics"]["reported_events"] == len(analysis["events"])
    assert [event["event_id"] for event in analysis["events"]] == [
        f"E{number:05d}" for number in range(1, len(analysis["events"]) + 1)
    ]
    # Nothing was said about the file before, so a clean run must stay silent.
    assert "missing_descriptors" not in clean
    assert _warnings(clean, "is missing from") == []


@pytest.mark.parametrize(
    ("label", "kept_type", "left_out"),
    [
        ("00", "birth", {"birth": 2, "death": 0}),
        ("04", "death", {"birth": 0, "death": 2}),
    ],
)
def test_missing_endpoint_gives_no_false_events(
    tmp_path: Path, label: str, kept_type: str, left_out: dict[str, int]
) -> None:
    root = _frames(tmp_path / "frames")
    (root / label / "ACF.dat").unlink()

    analysis = _analyze(root)

    assert _events(analysis, kept_type) == []
    assert analysis["missing_descriptors"][0]["events_left_out"] == left_out
    assert analysis["missing_descriptors"][0]["frame_labels"] == [label]
    # The frame has no descriptors at all, which keeps its own warning.
    assert _warnings(analysis, f"No electronic features were loaded for frame(s): {label}")


def test_two_adjacent_missing_frames_are_one_gap(tmp_path: Path) -> None:
    root = _frames(tmp_path / "frames")
    (root / "01" / "ACF.dat").unlink()
    (root / "02" / "ACF.dat").unlink()

    analysis = _analyze(root)

    assert _events(analysis, "birth", "death") == []
    assert analysis["missing_descriptors"][0]["frame_labels"] == ["01", "02"]
    assert analysis["missing_descriptors"][0]["events_left_out"] == {"birth": 2, "death": 2}


def test_real_births_and_deaths_survive_beside_a_gap(tmp_path: Path) -> None:
    """The counterexample: a producer with output in both frames still reports the change."""

    root = _frames(tmp_path / "frames")
    # A document without a feature says the tool looked and found none; it is not missing.
    for label, features in (
        ("00", ("kept", "lost")),
        ("01", ("kept", "lost")),
        ("02", ("kept",)),
        ("03", ("kept", "born")),
        ("04", ("kept", "born")),
    ):
        _tool(root / label, *features)
    (root / "02" / "ACF.dat").unlink()

    analysis = _analyze(root)

    assert ("death", 1, 2, "lost") in _events(analysis, "death")
    assert ("birth", 2, 3, "born") in _events(analysis, "birth")
    assert not [item for item in _events(analysis, "birth", "death") if "bader" in item[3]]
    assert [gap["producer"] for gap in analysis["missing_descriptors"]] == ["bader"]


def test_feature_lost_before_a_gap_is_still_a_death_but_the_gap_is_not(tmp_path: Path) -> None:
    root = _frames(tmp_path / "frames")
    for label, features in (
        ("00", ("kept", "lost")),
        ("01", ("kept", "lost")),
        ("02", ("kept",)),
        ("04", ("kept",)),
    ):
        _tool(root / label, *features)

    analysis = _analyze(root)

    assert _events(analysis, "death") == [("death", 1, 2, "lost")]
    assert _events(analysis, "birth") == []
    assert analysis["missing_descriptors"] == [
        {
            "producer": "tool",
            "file": None,
            "frames": [3],
            "frame_labels": ["03"],
            "events_left_out": {"birth": 1, "death": 1},
        }
    ]
    assert "tool output is missing from frame(s) 03" in _warnings(analysis, "tool output")[0]


def test_change_beside_a_gap_is_censored_not_transient(tmp_path: Path) -> None:
    changed = {"01": 1.5, "02": 1.5, "03": 1.5, "04": 1.5}
    clean = _analyze(_frames(tmp_path / "clean", charges=changed))
    gapped_root = _frames(tmp_path / "gapped", charges=changed)
    (gapped_root / "02" / "ACF.dat").unlink()

    analysis = _analyze(gapped_root)

    before = next(event for event in clean["events"] if event["type"] == "property_change")
    after = next(event for event in analysis["events"] if event["type"] == "property_change")
    assert (before["frame_before"], before["frame_after"]) == (0, 1)
    assert (after["frame_before"], after["frame_after"]) == (0, 1)
    assert before["persistence_status"] == "confirmed"
    assert after["persistence_status"] == "right_censored"
    assert after["confidence"] == pytest.approx(before["confidence"] * 0.85)
    assert after["evidence"]["maximum_observable_persistence"] == 1


def test_lineage_that_really_ends_stays_transient(tmp_path: Path) -> None:
    """The counterexample: a feature gone from a frame whose document exists did end."""

    root = _frames(tmp_path / "frames")
    for label, charge in (("00", 1.0), ("01", 1.5)):
        document = {
            "schema_version": "electrace.features.v1",
            "producer": {"name": "tool"},
            "features": [
                {
                    "id": "f",
                    "kind": "elf_basin",
                    "fractional_position": [0.3, 0.2, 0.8],
                    "charge": charge,
                }
            ],
            "metadata": {"charge_convention": "test population"},
        }
        (root / label / "features.json").write_text(json.dumps(document), encoding="utf-8")
    for label in ("02", "03", "04"):
        _tool(root / label)

    analysis = _analyze(root)

    event = next(item for item in analysis["events"] if item["source_ids"] == ["f"])
    assert event["type"] == "property_change"
    assert event["persistence_status"] == "transient"
    assert "missing_descriptors" not in analysis


def test_each_lobster_list_counts_as_its_own_output(tmp_path: Path) -> None:
    root = _frames(tmp_path / "frames")
    for label in LABELS:
        (root / label / "ACF.dat").unlink()
        (root / label / "ICOHPLIST.lobster").write_text(
            "No. atom1 atom2 distance ICOHP\n1 H1 H2 7.0 -1.0\n", encoding="utf-8"
        )
        if label != "02":
            (root / label / "ICOBILIST.lobster").write_text(
                "No. atom1 atom2 distance ICOBI\n1 H1 H2 7.0 0.5\n", encoding="utf-8"
            )

    analysis = _analyze(root)

    assert _events(analysis, "birth", "death") == []
    assert analysis["missing_descriptors"] == [
        {
            "producer": "lobster",
            "file": "ICOBILIST.lobster",
            "frames": [2],
            "frame_labels": ["02"],
            "events_left_out": {"birth": 1, "death": 1},
        }
    ]


def test_flat_external_descriptors_are_compared_by_producer(tmp_path: Path) -> None:
    root = _frames(tmp_path / "frames")
    for label in LABELS:
        (root / label / "ACF.dat").unlink()
    external = tmp_path / "external"
    external.mkdir()
    for label in ("00", "01", "03", "04"):
        _tool(external, "a", "b", name=f"{label}.json")

    analysis = _analyze(root, descriptor_dir=str(external))

    assert _events(analysis, "birth", "death") == []
    assert analysis["missing_descriptors"][0]["producer"] == "tool"
    assert analysis["missing_descriptors"][0]["file"] is None
    assert analysis["missing_descriptors"][0]["frame_labels"] == ["02"]


def test_producer_absent_from_every_frame_is_not_a_gap(tmp_path: Path) -> None:
    root = _frames(tmp_path / "frames", charges={"02": 1.5})

    analysis = _analyze(root)

    assert "missing_descriptors" not in analysis
    assert _warnings(analysis, "is missing from") == []
    assert _events(analysis, "property_change")


def test_missing_critic2_keeps_its_counts_entry_and_one_warning(tmp_path: Path) -> None:
    root = tmp_path / "frames"
    shutil.copytree(ROOT / "examples" / "hcn-hnc" / "frames", root)
    (root / "03" / "critic2.features.json").unlink()

    analysis = _analyze(root)

    assert _events(analysis, "birth", "death") == []
    assert analysis["missing_descriptors"][0]["producer"] == "critic2"
    assert analysis["missing_descriptors"][0]["file"] == "critic2.features.json"
    assert len(_warnings(analysis, "frame(s) 03")) == 1
    entry = next(item for item in analysis["critical_point_counts"] if item["frame_label"] == "03")
    assert entry["reason"] == "no critic2 document"


def test_analysis_with_a_gap_matches_the_schema(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    root = _frames(tmp_path / "frames")
    (root / "02" / "ACF.dat").unlink()
    schema = json.loads(
        (ROOT / "schemas" / "analysis-v0.1.schema.json").read_text(encoding="utf-8")
    )

    jsonschema.validate(json.loads(json.dumps(_analyze(root))), schema)
    assert "missing_descriptors" in schema["properties"]
