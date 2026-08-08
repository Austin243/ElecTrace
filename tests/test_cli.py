from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from electrace import __version__
from electrace.cli import _python_supported, app
from electrace.config import ElecTraceConfig, OutputConfig
from electrace.demo import generate_demo
from electrace.export import ExportDependencyError

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
    generated = runner.invoke(app, ["demo", "--output", str(demo)])
    assert generated.exit_code == 0, generated.output
    assert (demo / "electrace-results" / "analysis.json").is_file()

    inspected = runner.invoke(app, ["inspect", str(demo / "electrace-results")])
    assert inspected.exit_code == 0, inspected.output
    plain_output = _plain(inspected.stdout)
    assert "6 frames" in plain_output
    assert "6 events" in plain_output

    diagnosed = runner.invoke(
        app,
        ["doctor", "--config", str(demo / "electrace.toml"), "--json"],
    )
    assert diagnosed.exit_code == 0, diagnosed.output
    diagnosis = json.loads(_plain(diagnosed.stdout))
    assert diagnosis["python_supported"] is True
    assert [frame["frame"]["label"] for frame in diagnosis["frames"]] == [
        "00",
        "01",
        "02",
        "03",
        "04",
        "05",
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
        ],
    )
    assert planned.exit_code == 0, planned.output
    plain_output = _plain(planned.stdout)
    assert '"plugin": "critic2"' in plain_output
    assert '"ready":' not in plain_output

    batch = runner.invoke(
        app,
        [
            "plan",
            "critic2",
            str(demo),
            "--all-frames",
            "--capability",
            "qtaim_critical_points",
            "--config",
            str(demo / "electrace.toml"),
        ],
    )
    assert batch.exit_code == 0, batch.output
    batch_payload = json.loads(_plain(batch.stdout))
    assert [plan["frame"]["label"] for plan in batch_payload["plans"]] == [
        "00",
        "01",
        "02",
        "03",
        "04",
        "05",
    ]
    assert all(
        "CPREPORT critic2.features.json GRAPH" in plan["command"]["stdin"]
        for plan in batch_payload["plans"]
    )


def test_init_refuses_overwrite_without_force(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    config = tmp_path / "electrace.toml"
    first = runner.invoke(app, ["init", str(config), "--images", str(images)])
    second = runner.invoke(app, ["init", str(config), "--images", str(images)])

    assert first.exit_code == 0
    assert second.exit_code == 2
    assert "Refusing to replace" in _plain(second.output)


def test_analyze_reports_missing_hdf5_dependency_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "electrace.toml"
    config_path.write_text(
        "[outputs]\nhdf5 = true\n",
        encoding="utf-8",
    )
    config = ElecTraceConfig(
        outputs=OutputConfig(html_report=False, json=False, csv=False, hdf5=True)
    )
    analysis = {"schema_version": "0.1", "events": [], "tracks": []}
    monkeypatch.setattr(
        "electrace.pipeline.load_and_analyze",
        lambda *_args, **_kwargs: (analysis, config),
    )
    monkeypatch.setitem(sys.modules, "h5py", None)

    result = runner.invoke(
        app,
        [
            "analyze",
            str(config_path),
            "--output",
            str(tmp_path / "results"),
        ],
    )

    assert result.exit_code == 2
    assert "pip install electrace[hdf5]" in _plain(result.output)
    assert "Traceback" not in result.output


def test_analyze_strict_rejects_ambiguity_and_permissive_reports_warning(
    tmp_path: Path,
) -> None:
    config_path = generate_demo(tmp_path / "demo")
    descriptor_path = config_path.parent / "01" / "features.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    duplicate = dict(descriptor["features"][0])
    duplicate["id"] = "ambiguous-duplicate"
    descriptor["features"].append(duplicate)
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

    strict = runner.invoke(app, ["analyze", str(config_path)])

    assert strict.exit_code == 2
    assert "Ambiguous correspondence in strict mode" in _plain(strict.output)
    assert "--permissive" in _plain(strict.output)
    assert not (config_path.parent / "electrace-results").exists()

    permissive = runner.invoke(app, ["analyze", str(config_path), "--permissive"])

    assert permissive.exit_code == 0, permissive.output
    assert "warnings" in _plain(permissive.output)


def test_demo_reports_export_dependency_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_export(*_args: object, **_kwargs: object) -> None:
        raise ExportDependencyError("optional exporter unavailable")

    monkeypatch.setattr("electrace.cli.run_project", fail_export)

    result = runner.invoke(
        app,
        ["demo", "--output", str(tmp_path / "demo")],
    )

    assert result.exit_code == 2
    assert "optional exporter unavailable" in _plain(result.output)
    assert "Traceback" not in result.output


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


def test_quoted_boolean_config_is_a_clean_cli_error(tmp_path: Path) -> None:
    config = tmp_path / "invalid-boolean.toml"
    config.write_text(
        """[outputs]
json = "false"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config)])

    assert result.exit_code == 2
    assert "outputs.json must be a boolean" in _plain(result.output)
    assert "Traceback" not in result.output


def test_misspelled_root_config_table_is_a_clean_cli_error(tmp_path: Path) -> None:
    config = tmp_path / "typo.toml"
    config.write_text(
        """[matcing]
position_weight = 2.0
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config)])

    assert result.exit_code == 2
    assert "configuration root contains unknown keys: matcing" in _plain(result.output)
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


def test_mixed_case_plugin_name_still_honors_normalized_configuration(tmp_path: Path) -> None:
    frame = tmp_path / "frame"
    frame.mkdir()
    config = tmp_path / "electrace.toml"
    config.write_text(
        """[plugins.critic2]
enabled = false
executable = "must-not-be-ignored"
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["plan", "Critic2", str(frame), "--config", str(config)],
    )

    assert result.exit_code == 2
    assert "plugin 'critic2' is disabled" in _plain(result.output)


def test_single_frame_plan_honors_configured_structure_name(tmp_path: Path) -> None:
    frame = tmp_path / "frame"
    frame.mkdir()
    structure = frame / "FINAL.vasp"
    structure.write_text("planned input only\n", encoding="utf-8")
    config = tmp_path / "electrace.toml"
    config.write_text(
        """[inputs]
structure_name = "FINAL.vasp"
""",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["plan", "critic2", str(frame), "--config", str(config)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(_plain(result.stdout))
    assert payload["required_inputs"][0] == str(structure.resolve())
    assert "CRYSTAL FINAL.vasp" in payload["stdin"]
