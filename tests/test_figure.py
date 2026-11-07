"""Path figures from the demo, and clean errors on analysis files they cannot use."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from rich.text import Text
from typer.testing import CliRunner

from electrace.cli import app
from electrace.demo import generate_demo
from electrace.figure import write_path_figure
from electrace.pipeline import run_project

pytest.importorskip("matplotlib")
runner = CliRunner()


@pytest.fixture
def demo_analysis(tmp_path: Path) -> dict[str, Any]:
    output = run_project(generate_demo(tmp_path / "demo")).output_directory
    return json.loads((output / "analysis.json").read_text(encoding="utf-8"))


def test_demo_figure_has_a_density_panel(tmp_path: Path, demo_analysis: dict[str, Any]) -> None:
    payload = write_path_figure(
        demo_analysis,
        tmp_path / "figure",
        bonds=["C0-N1"],
        atoms=[],
        limits={},
        overwrite=False,
    )

    assert "rho" in payload["panels"]
    assert payload["bonds"][0]["rho"][2:] == pytest.approx([0.06, 0.085, 0.11, 0.135])
    assert payload["bonds"][0]["rho"][:2] == [None, None]
    assert not [warning for warning in payload["warnings"] if "density" in warning]


def _bond_row(analysis: dict[str, Any]) -> None:
    analysis["bonds"][0]["frame"] = 99


def _barrier(analysis: dict[str, Any]) -> None:
    analysis["barrier"]["frames"] = [99]


def _event_on_the_bond(analysis: dict[str, Any]) -> None:
    # A change on a chosen bond attaches to its series, and used to fail after drawing.
    event = next(item for item in analysis["events"] if "bcp-local-2" in item["source_ids"])
    event["frame_after"] = 99


@pytest.mark.parametrize(
    ("damage", "record"),
    [(_bond_row, "a bond row"), (_barrier, "the barrier record"), (_event_on_the_bond, "an event")],
)
def test_unknown_frame_stops_cleanly_before_writing(
    tmp_path: Path,
    demo_analysis: dict[str, Any],
    damage: Callable[[dict[str, Any]], None],
    record: str,
) -> None:
    damage(demo_analysis)
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(demo_analysis), encoding="utf-8")

    result = runner.invoke(
        app, ["figure", str(path), "--bond", "C0-N1", "--output", str(tmp_path / "figure")]
    )

    assert result.exit_code == 2
    text = " ".join(Text.from_ansi(result.output).plain.split())
    assert f"{record} in this analysis names frame 99" in text
    assert not isinstance(result.exception, KeyError)
    assert list(tmp_path.glob("figure*")) == []


def test_bond_id_with_an_atom_absent_from_a_frame_stops_cleanly(
    tmp_path: Path, demo_analysis: dict[str, Any]
) -> None:
    frame = demo_analysis["frames"][4]
    frame["atoms"] = [atom for atom in frame["atoms"] if atom["index"] != 1]
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(demo_analysis), encoding="utf-8")
    bond_id = demo_analysis["bonds"][0]["bond_id"]

    result = runner.invoke(
        app, ["figure", str(path), "--bond", bond_id, "--output", str(tmp_path / "figure")]
    )

    assert result.exit_code == 2
    assert "atom 1 is not an atom of every frame" in Text.from_ansi(result.output).plain
    assert not isinstance(result.exception, KeyError)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda analysis: analysis["bonds"][0].pop("bond_id"), "bond_id"),
        (lambda analysis: analysis["bonds"][0].pop("frame"), "frame"),
        (lambda analysis: analysis["events"][0].pop("frame_after"), "frame_after"),
        (lambda analysis: analysis["bonds"].__setitem__(0, "not a row"), "string indices"),
        (lambda analysis: analysis["events"].__setitem__(0, 7), "not subscriptable"),
    ],
)
def test_damaged_record_stops_cleanly(
    tmp_path: Path,
    demo_analysis: dict[str, Any],
    damage: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    damage(demo_analysis)
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(demo_analysis), encoding="utf-8")

    result = runner.invoke(
        app, ["figure", str(path), "--bond", "C0-N1", "--output", str(tmp_path / "figure")]
    )

    assert result.exit_code == 2
    assert message in Text.from_ansi(result.output).plain
    assert isinstance(result.exception, SystemExit)
    assert list(tmp_path.glob("figure*")) == []
