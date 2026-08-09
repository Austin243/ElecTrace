from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from electrace.config import InputConfig, load_config, write_default_config
from electrace.pipeline import _event_config, _matching_configs


def test_default_config_round_trip(tmp_path: Path) -> None:
    config = load_config(write_default_config(tmp_path / "electrace.toml"))

    assert config.project_name == "ElecTrace analysis"
    assert config.series_kind == "auto"
    assert config.matching.max_atom_displacement == pytest.approx(1.5)
    assert config.events.descriptor_thresholds["icohp"] == pytest.approx(0.5)
    assert config.plugins["critic2"]["enabled"] is True


def test_default_config_refuses_overwrite(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "electrace.toml")
    with pytest.raises(FileExistsError):
        write_default_config(path)


def test_configured_scales_and_thresholds_reach_runtime(tmp_path: Path) -> None:
    path = tmp_path / "electrace.toml"
    path.write_text(
        """[matching]
descriptor_weight = 0.8
conservation_tolerance = 0.2

[matching.descriptor_scales]
icohp = 0.25
rho = 0.02

[events]
minimum_event_confidence = 0.25

[events.descriptor_thresholds]
icohp = 0.4
""",
        encoding="utf-8",
    )

    config = load_config(path)
    _, matching = _matching_configs(config)
    events = _event_config(config)

    assert matching.descriptor_scales == {"icohp": 0.25, "rho": 0.02}
    assert events.min_event_confidence == pytest.approx(0.25)
    assert events.descriptor_thresholds == {"icohp": 0.4}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("structure_name", "../CONTCAR"),
        ("fallback_structure_name", "inputs/POSCAR"),
        ("descriptor_name", "/tmp/features.json"),
    ],
)
def test_input_filenames_must_be_basenames(field: str, value: str) -> None:
    options: dict[str, Any] = {field: value}
    with pytest.raises(ValueError, match="must be a basename"):
        InputConfig(**options)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('[inputs]\nprefer_contcar = "false"\n', "must be a boolean"),
        ("[matching]\nmax_feature_displacement = 0\n", "must be greater than 0"),
        ("[events]\nminimum_persistence = 2.5\n", "must be an integer"),
        ('[plugins.critic2]\nenabled = "false"\n', "must be a boolean"),
        ("[matcing]\nposition_weight = 2\n", "unknown keys"),
    ],
)
def test_invalid_configuration_fails_cleanly(tmp_path: Path, document: str, message: str) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises((TypeError, ValueError), match=message):
        load_config(path)


def test_json_compatible_plugin_options_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "plugin.toml"
    path.write_text(
        """[plugins.custom]
enabled = true
executable = "custom-tool"
capability = "custom_evidence"
iterations = 4
[plugins.custom.parameters]
alpha = 0.25
""",
        encoding="utf-8",
    )

    options = load_config(path).plugins["custom"]

    assert options["iterations"] == 4
    assert options["parameters"] == {"alpha": 0.25}
