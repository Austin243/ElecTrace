from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from electrace.config import (
    ElecTraceConfig,
    EventSettings,
    InputConfig,
    load_config,
    write_default_config,
)
from electrace.pipeline import _event_config, _matching_configs


def test_default_config_round_trip(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "electrace.toml")
    config = load_config(path)

    assert config.project_name == "ElecTrace analysis"
    assert config.series_kind == "auto"
    assert config.matching.max_atom_displacement == pytest.approx(1.5)
    assert config.matching.descriptor_weight == pytest.approx(0.35)
    assert config.matching.ambiguity_tolerance == pytest.approx(0.05)
    assert config.matching.conservation_tolerance == pytest.approx(0.35)
    assert config.events.minimum_event_confidence == pytest.approx(0.10)
    assert config.events.descriptor_thresholds["icohp"] == pytest.approx(0.5)
    assert config.plugins["critic2"]["enabled"] is True
    assert config.inputs.descriptor_source == "auto"

    _, matching = _matching_configs(config)
    events = _event_config(config)
    assert matching.descriptor_scales == {}
    assert matching.ambiguity_tolerance == pytest.approx(0.05)
    assert matching.conservation_tolerance == pytest.approx(0.35)
    assert events.hysteresis_frames == 1
    assert events.descriptor_thresholds["icobi"] == pytest.approx(0.1)


def test_default_config_refuses_to_overwrite(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "electrace.toml")

    with pytest.raises(FileExistsError):
        write_default_config(path)


@pytest.mark.parametrize("target_exists", [False, True])
def test_default_config_never_follows_leaf_symlinks(tmp_path: Path, target_exists: bool) -> None:
    target = tmp_path / "outside.toml"
    if target_exists:
        target.write_text("outside\n", encoding="utf-8")
    destination = tmp_path / "electrace.toml"
    destination.symlink_to(target)

    with pytest.raises(OSError, match="symbolic link"):
        write_default_config(destination, overwrite=True)

    assert destination.is_symlink()
    assert target.exists() is target_exists
    if target_exists:
        assert target.read_text(encoding="utf-8") == "outside\n"


def test_default_config_write_is_atomic_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "electrace.toml"
    destination.write_text("original\n", encoding="utf-8")
    write_text = Path.write_text

    def fail_temporary_write(path: Path, data: str, **kwargs: Any) -> int:
        if path.name.startswith(f".{destination.name}."):
            write_text(path, "partial\n", **kwargs)
            raise OSError("simulated write failure")
        return write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temporary_write)

    with pytest.raises(OSError, match="simulated write failure"):
        write_default_config(destination, overwrite=True)

    assert destination.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


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


def test_descriptor_source_is_normalized_and_requires_external_location() -> None:
    assert InputConfig(descriptor_source=" AUTO ").descriptor_source == "auto"
    with pytest.raises(ValueError, match="descriptor_source must be one of"):
        InputConfig(descriptor_source="prefer_external")
    with pytest.raises(ValueError, match="descriptor_dir is required"):
        InputConfig(descriptor_source="merge")


def test_descriptor_scales_are_loaded_and_propagated(tmp_path: Path) -> None:
    path = tmp_path / "electrace.toml"
    path.write_text(
        """[matching]
descriptor_weight = 0.8
ambiguity_tolerance = 0.02
conservation_tolerance = 0.2

[matching.descriptor_scales]
icohp = 0.25
rho = 0.02

[events]
hysteresis_frames = 2
minimum_event_confidence = 0.25

[events.descriptor_thresholds]
icohp = 0.4
""",
        encoding="utf-8",
    )
    config = load_config(path)
    _, matching = _matching_configs(config)
    events = _event_config(config)

    assert matching.descriptor_weight == pytest.approx(0.8)
    assert matching.ambiguity_tolerance == pytest.approx(0.02)
    assert matching.conservation_tolerance == pytest.approx(0.2)
    assert matching.descriptor_scales == {"icohp": 0.25, "rho": 0.02}
    assert events.hysteresis_frames == 2
    assert events.min_event_confidence == pytest.approx(0.25)
    assert events.descriptor_thresholds == {"icohp": 0.4}


@pytest.mark.parametrize(
    ("medium", "high"),
    [(-0.1, 0.8), (0.9, 0.8), (0.5, 1.1), (float("nan"), 0.8)],
)
def test_confidence_threshold_order_is_validated(medium: float, high: float) -> None:
    with pytest.raises(ValueError, match=r"confidence_threshold|confidence thresholds"):
        EventSettings(
            medium_confidence_threshold=medium,
            high_confidence_threshold=high,
        )


def test_equal_confidence_thresholds_are_allowed() -> None:
    settings = EventSettings(
        medium_confidence_threshold=0.7,
        high_confidence_threshold=0.7,
    )
    assert settings.medium_confidence_threshold == settings.high_confidence_threshold


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan")])
def test_minimum_event_confidence_is_validated(value: float) -> None:
    with pytest.raises(ValueError, match="minimum_event_confidence"):
        EventSettings(minimum_event_confidence=value)


def test_toml_dates_are_rejected_in_json_bound_metadata(tmp_path: Path) -> None:
    path = tmp_path / "dated.toml"
    path.write_text("[metadata]\ncreated = 2026-08-07\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"configuration\.metadata\.created.*date"):
        load_config(path)

    with pytest.raises(ValueError, match=r"configuration\.metadata\.created.*date"):
        ElecTraceConfig(metadata={"created": date(2026, 8, 7)})


@pytest.mark.parametrize("literal", ["nan", "inf", "-inf"])
def test_nonfinite_toml_values_are_rejected_recursively(tmp_path: Path, literal: str) -> None:
    path = tmp_path / "nonfinite.toml"
    path.write_text(f"[metadata]\nvalue = {literal}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"configuration\.metadata\.value must be finite"):
        load_config(path)


def test_to_dict_revalidates_mutated_metadata() -> None:
    config = ElecTraceConfig()
    config.metadata["bad"] = float("nan")

    with pytest.raises(ValueError, match=r"configuration\.metadata\.bad must be finite"):
        config.to_dict()


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('[inputs]\nprefer_contcar = "false"\n', "inputs.prefer_contcar must be a boolean"),
        ('[outputs]\njson = "false"\n', "outputs.json must be a boolean"),
        (
            '[plugins.critic2]\nenabled = "false"\n',
            "plugins.critic2.enabled must be a boolean",
        ),
        (
            "[plugins.critic2]\nexecutable = false\n",
            "plugins.critic2.executable must be a string",
        ),
        (
            "[plugins.critic2]\ncapability = 7\n",
            "plugins.critic2.capability must be a string",
        ),
    ],
)
def test_behavioral_booleans_and_reserved_plugin_options_are_strict(
    tmp_path: Path, document: str, message: str
) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises((TypeError, ValueError), match=message):
        load_config(path)


@pytest.mark.parametrize("literal", ["2.9", "2.0", "true"])
def test_integer_event_settings_reject_nonintegers(tmp_path: Path, literal: str) -> None:
    path = tmp_path / "invalid-integer.toml"
    path.write_text(f"[events]\nminimum_persistence = {literal}\n", encoding="utf-8")

    with pytest.raises(
        (TypeError, ValueError), match=r"events\.minimum_persistence must be an integer"
    ):
        load_config(path)


@pytest.mark.parametrize(
    "document",
    [
        '[matching.descriptor_scales]\n"icohp " = 0.5\n',
        '[events.descriptor_thresholds]\n" icobi" = 0.1\n',
    ],
)
def test_descriptor_keys_reject_surrounding_whitespace(tmp_path: Path, document: str) -> None:
    path = tmp_path / "whitespace-key.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ValueError, match="must not contain surrounding whitespace"):
        load_config(path)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            "[matching]\nsplit_merge_threshold = 2\n",
            "matching.split_merge_threshold must be at most 1",
        ),
        (
            "[matching]\nconservation_tolerance = 2\n",
            "matching.conservation_tolerance must be at most 1",
        ),
        (
            "[matching]\nambiguity_tolerance = -0.1\n",
            "matching.ambiguity_tolerance must be at least 0",
        ),
        (
            "[matching]\nmax_feature_displacement = 0\n",
            "matching.max_feature_displacement must be greater than 0",
        ),
        (
            "[matching.descriptor_scales]\nicohp = 0\n",
            "matching.descriptor_scales.icohp must be greater than 0",
        ),
        (
            "[events]\ncharge_change_threshold = 0\n",
            "events.charge_change_threshold must be greater than 0",
        ),
        ('[outputs]\ndirectory = ""\n', "outputs.directory must be a non-empty string"),
    ],
)
def test_scientific_numeric_ranges_and_output_directory_are_validated(
    tmp_path: Path, document: str, message: str
) -> None:
    path = tmp_path / "invalid-range.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises((TypeError, ValueError), match=message):
        load_config(path)


def test_arbitrary_json_compatible_plugin_options_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "plugin.toml"
    path.write_text(
        """[plugins.custom]
enabled = true
executable = "custom-tool"
capability = "custom_evidence"
iterations = 4
labels = ["a", "b"]
[plugins.custom.parameters]
alpha = 0.25
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.plugins["custom"]["iterations"] == 4
    assert config.plugins["custom"]["labels"] == ["a", "b"]
    assert config.plugins["custom"]["parameters"] == {"alpha": 0.25}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("[matcing]\nposition_weight = 2\n", "configuration root contains unknown keys: matcing"),
        ('[project]\nseries_knd = "neb"\n', r"\[project\] contains unknown keys: series_knd"),
    ],
)
def test_unknown_fixed_configuration_keys_are_rejected(
    tmp_path: Path, document: str, message: str
) -> None:
    path = tmp_path / "typo.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_plugin_table_names_are_lowercase_and_normalization_collisions_fail(
    tmp_path: Path,
) -> None:
    uppercase = tmp_path / "uppercase.toml"
    uppercase.write_text("[plugins.Critic2]\nenabled = true\n", encoding="utf-8")
    duplicate = tmp_path / "duplicate.toml"
    duplicate.write_text(
        """[plugins.critic2]
enabled = true
[plugins." critic2 "]
enabled = false
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be lowercase"):
        load_config(uppercase)
    with pytest.raises(ValueError, match="duplicate normalized plugin name"):
        load_config(duplicate)
