from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from electrace import __version__
from electrace.cli import _python_supported, app
from electrace.demo import generate_demo

runner = CliRunner()


def _plain(output: str) -> str:
    return Text.from_ansi(output).plain


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in _plain(result.stdout)


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ((3, 10), False),
        ((3, 11), True),
        ((3, 13, 9), True),
        ((3, 14), False),
        ((4, 0), False),
    ],
)
def test_python_support_matches_the_documented_tested_range(
    version: tuple[int, ...],
    supported: bool,
) -> None:
    assert _python_supported(version) is supported


def test_demo_inspect_doctor_and_plan_workflow(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    generated = runner.invoke(app, ["demo", "--output", str(demo), "--json"])
    assert generated.exit_code == 0, generated.output
    generated_payload = json.loads(generated.stdout)
    assert generated_payload["command"] == "demo"
    assert generated_payload["data"]["analysis"]["counts"]["frames"] == 6
    assert (demo / "electrace-results" / "analysis.json").is_file()

    inspected = runner.invoke(app, ["inspect", str(demo / "electrace-results"), "--json"])
    assert inspected.exit_code == 0, inspected.output
    inspection = json.loads(inspected.stdout)
    assert inspection["command"] == "inspect"
    assert inspection["data"]["counts"]["frames"] == 6
    assert inspection["data"]["counts"]["events"] == 6

    diagnosed = runner.invoke(
        app,
        ["doctor", "--config", str(demo / "electrace.toml"), "--json"],
    )
    assert diagnosed.exit_code == 0, diagnosed.output
    diagnosis = json.loads(_plain(diagnosed.stdout))
    assert (diagnosis["schema_version"], diagnosis["ok"], diagnosis["command"]) == (
        "electrace.cli.v1",
        True,
        "doctor",
    )
    assert diagnosis["data"]["python_supported"] is True
    assert [frame["frame"]["label"] for frame in diagnosis["data"]["frames"]] == [
        f"{index:02d}" for index in range(6)
    ]

    planned = runner.invoke(
        app,
        [
            "plan",
            "critic2",
            str(demo / "00"),
            "--capability",
            "qtaim_critical_points",
            "--config",
            str(demo / "electrace.toml"),
            "--json",
        ],
    )
    assert planned.exit_code == 0, planned.output
    planned_payload = json.loads(planned.stdout)
    assert planned_payload["data"]["plugin"] == "critic2"
    assert "ready" not in planned_payload["data"]


def test_init_refuses_overwrite_without_force(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    config = tmp_path / "electrace.toml"
    first = runner.invoke(app, ["init", str(config), "--images", str(images), "--json"])
    second = runner.invoke(app, ["init", str(config), "--images", str(images)])

    assert first.exit_code == 0
    assert json.loads(first.stdout)["data"]["configuration"] == str(config.resolve())
    assert second.exit_code == 2
    assert "Refusing to replace" in _plain(second.output)


def test_analyze_json_reports_counts(tmp_path: Path) -> None:
    config_path = generate_demo(tmp_path / "demo")

    result = runner.invoke(app, ["analyze", str(config_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "analyze"
    assert payload["data"]["counts"]["frames"] == 6
    assert payload["data"]["counts"]["events"] == 6


def test_inspect_malformed_analysis_returns_json_error(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis.json"
    analysis.write_text(
        json.dumps({"project": {}, "frames": None, "tracks": [], "events": []}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(analysis), "--json"])

    assert result.exit_code == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["command"] == "inspect"
    assert payload["error"]["code"] == "invalid_input"
    assert "NoneType" in payload["error"]["message"]


def test_invalid_input_filename_config_is_a_clean_cli_error(tmp_path: Path) -> None:
    config = tmp_path / "invalid.toml"
    config.write_text(
        """[inputs]
structure_name = 7
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config)])

    assert result.exit_code == 2
    assert "inputs.structure_name must be a string" in _plain(result.output)
    assert "Traceback" not in result.output


def test_configured_structure_name_cannot_inject_path_wide_critic2_input(
    tmp_path: Path,
) -> None:
    config = tmp_path / "injection.toml"
    config.write_text(
        """[inputs]
structure_name = "CONTCAR\\nEND"
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "plan",
            "critic2",
            str(tmp_path),
            "--all-frames",
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 2
    assert "inputs.structure_name must be a basename" in _plain(result.output)
    assert "CPREPORT" not in result.output


def _named_frames(root: Path, labels: tuple[str, ...]) -> Path:
    for label in labels:
        frame = root / "frames" / label
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(
            "H\n1\n5 0 0\n0 5 0\n0 0 5\nH\n1\nDirect\n0 0 0\n", encoding="utf-8"
        )
        (frame / "ACF.dat").write_text("1 0 0 0 1.0 0 10\n", encoding="utf-8")
    config = root / "electrace.toml"
    assert (
        runner.invoke(app, ["init", str(config), "--images", str(root / "frames")]).exit_code == 0
    )
    return config


def test_analyze_and_inspect_show_a_frame_order_that_may_be_wrong(tmp_path: Path) -> None:
    config = _named_frames(tmp_path, ("IS", "TS", "FS"))

    analyzed = runner.invoke(app, ["analyze", str(config)])
    assert analyzed.exit_code == 0, analyzed.output
    text = " ".join(_plain(analyzed.output).split())
    assert "Frame order: FS, IS, TS" in text
    assert "Warning: Frames are in the natural order of their names (FS, IS, TS)" in text

    as_json = runner.invoke(app, ["analyze", str(config), "--force", "--json"])
    assert any("natural order" in item for item in json.loads(as_json.stdout)["warnings"])

    inspected = runner.invoke(app, ["inspect", str(tmp_path / "frames")])
    assert "Warning: Frames are in the natural order" in " ".join(_plain(inspected.output).split())
    as_json = runner.invoke(app, ["inspect", str(tmp_path / "frames"), "--json"])
    assert any("natural order" in item for item in json.loads(as_json.stdout)["warnings"])


def test_numbered_frames_get_the_order_line_and_no_order_warning(tmp_path: Path) -> None:
    config = _named_frames(tmp_path, ("image_1", "image_2", "image_10"))

    analyzed = runner.invoke(app, ["analyze", str(config)])

    assert analyzed.exit_code == 0, analyzed.output
    text = " ".join(_plain(analyzed.output).split())
    assert "Frame order: image_1, image_2, image_10" in text
    assert "natural order" not in text


def test_parse_errors_use_normal_cli_output() -> None:
    result = runner.invoke(app, ["doctor", "--unknown", "--json"])

    assert result.exit_code == 2
    assert "No such option" in _plain(result.output)


def test_workflow_validation_failure_is_a_machine_readable_error(tmp_path: Path) -> None:
    manifest = tmp_path / "invalid.yaml"
    manifest.write_text("schema_version: electrace.workflow.v1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["workflow", "validate", str(manifest), "--json"],
    )

    assert result.exit_code == 2
    assert result.stderr == ""
    assert "\x1b" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "workflow.validate"
    assert payload["error"]["code"] == "validation_failed"
