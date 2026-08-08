from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from electrace.plugins import (
    DEFAULT_REGISTRY,
    BaderKitPlugin,
    Capability,
    CommandPlan,
    PluginRegistry,
    doctor_plugins,
    doctor_plugins_path,
    plan_plugin,
    plan_plugin_path,
)


def _touch(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text("fixture\n", encoding="utf-8")


def test_builtin_registry_exposes_capabilities() -> None:
    assert DEFAULT_REGISTRY.names() == (
        "bader",
        "baderkit",
        "chargemol",
        "critic2",
        "lobster",
    )
    assert [plugin.name for plugin in DEFAULT_REGISTRY.for_capability(Capability.COHP)] == [
        "lobster"
    ]
    assert {plugin.name for plugin in DEFAULT_REGISTRY.for_capability("bader_charges")} == {
        "bader",
        "baderkit",
    }


def test_registry_rejects_duplicate_and_unknown_plugins() -> None:
    registry = PluginRegistry([BaderKitPlugin()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(BaderKitPlugin())
    with pytest.raises(KeyError, match="unknown plugin"):
        registry.get("missing")


def test_baderkit_plans_argv_without_executing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _touch(tmp_path, "CHGCAR", "CHGCAR_sum")
    executed = False

    def forbidden(*args: object, **kwargs: object) -> None:
        nonlocal executed
        executed = True
        raise AssertionError("planner attempted to execute an external tool")

    monkeypatch.setattr("subprocess.run", forbidden)
    plan = plan_plugin(
        "baderkit",
        tmp_path,
        capability="bader_charges",
        reference_file="CHGCAR_sum",
    )

    assert plan.argv == ("baderkit", "bader", "CHGCAR", "-tot", "CHGCAR_sum")
    assert plan.cwd == tmp_path.resolve()
    assert plan.expected_outputs == (tmp_path / "bader.json",)
    assert plan.missing_inputs == ()
    assert executed is False
    assert ">" not in plan.argv


def test_baderkit_badelf_uses_documented_grid_order_and_output(tmp_path: Path) -> None:
    _touch(tmp_path, "CHGCAR", "ELFCAR", "CHGCAR_sum")

    plan = plan_plugin(
        "baderkit",
        tmp_path,
        capability="elf_basins",
        configuration={"executable": "/opt/baderkit"},
        reference_file="CHGCAR_sum",
    )

    assert plan.argv == (
        "/opt/baderkit",
        "badelf",
        "CHGCAR",
        "ELFCAR",
        "-tot",
        "CHGCAR_sum",
    )
    assert plan.expected_outputs == (tmp_path / "badelf.json",)


def test_bader_plan_supports_optional_reference_density(tmp_path: Path) -> None:
    _touch(tmp_path, "CHGCAR", "CHGCAR_sum")

    plan = plan_plugin(
        "bader",
        tmp_path,
        capability="bader_charges",
        reference_file="CHGCAR_sum",
    )

    assert plan.argv == ("bader", "CHGCAR", "-ref", "CHGCAR_sum")
    assert plan.expected_outputs == (tmp_path / "ACF.dat",)
    assert plan.missing_inputs == ()
    assert "not formal oxidation states" in plan.notes[0]


def test_critic2_plan_uses_explicit_stdin_and_stdout_capture(tmp_path: Path) -> None:
    _touch(tmp_path, "CONTCAR", "CHGCAR")
    plan = plan_plugin("critic2", tmp_path, capability=Capability.QTAIM_CRITICAL_POINTS)

    assert plan.argv == ("critic2", "-q")
    assert plan.stdin == (
        "CRYSTAL CONTCAR\nLOAD CHGCAR\nAUTO\nCPREPORT critic2.features.json GRAPH\nEND\n"
    )
    assert plan.stdout_path == tmp_path / "critic2.cro"
    assert plan.expected_outputs == (
        tmp_path / "critic2.cro",
        tmp_path / "critic2.features.json",
    )
    assert "does not parse critic2.cro" in plan.notes[1]
    assert "review" in plan.notes[0].lower()


def test_lobster_and_chargemol_plans_declare_inputs_and_outputs(tmp_path: Path) -> None:
    _touch(tmp_path, "lobsterin", "WAVECAR", "POSCAR", "POTCAR", "job_control.txt")
    lobster = plan_plugin("lobster", tmp_path, capability="cobi")
    chargemol = plan_plugin("chargemol", tmp_path)

    assert lobster.argv == ("lobster",)
    assert lobster.expected_outputs == (tmp_path / "ICOBILIST.lobster",)
    assert lobster.missing_inputs == ()
    assert chargemol.expected_outputs == (tmp_path / "DDEC6_even_tempered_net_atomic_charges.xyz",)


def test_plan_rejects_disabled_plugin_and_unknown_options(tmp_path: Path) -> None:
    _touch(tmp_path, "CHGCAR")
    with pytest.raises(ValueError, match="disabled"):
        plan_plugin("baderkit", tmp_path, configuration={"enabled": False})
    with pytest.raises(TypeError, match="unknown BaderKit"):
        plan_plugin("baderkit", tmp_path, surprise=True)


@pytest.mark.parametrize(
    ("plugin", "options"),
    [
        ("baderkit", {"charge_file": "../CHGCAR"}),
        ("critic2", {"field_file": "CHGCAR\nEND"}),
        ("lobster", {"input_file": "../lobsterin"}),
        ("chargemol", {"job_control": "/tmp/job_control.txt"}),
    ],
)
def test_planner_filenames_must_be_basenames(
    tmp_path: Path, plugin: str, options: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="basename"):
        plan_plugin(plugin, tmp_path, **options)


@pytest.mark.parametrize(
    ("plugin", "options", "fixed_name"),
    [
        ("lobster", {"input_file": "custom-lobsterin"}, "lobsterin"),
        ("lobster", {"structure_file": "CONTCAR"}, "POSCAR"),
        ("chargemol", {"job_control": "custom-control.txt"}, "job_control.txt"),
    ],
)
def test_fixed_name_tools_reject_unstaged_aliases(
    tmp_path: Path,
    plugin: str,
    options: dict[str, Any],
    fixed_name: str,
) -> None:
    with pytest.raises(ValueError, match=rf"fixed filename {fixed_name!r}"):
        plan_plugin(plugin, tmp_path, **options)


def test_doctor_is_read_only_and_honors_configuration(tmp_path: Path) -> None:
    _touch(tmp_path, "CHGCAR")
    executable = tmp_path / "fake-baderkit"
    executable.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | 0o111)

    reports = doctor_plugins(
        tmp_path,
        configuration={
            "bader": {"enabled": False},
            "baderkit": {"enabled": True, "executable": str(executable)},
            "critic2": {"enabled": False},
            "lobster": {"enabled": False},
            "chargemol": {"enabled": False},
        },
    )

    assert len(reports) == 1
    assert reports[0].plugin == "baderkit"
    assert reports[0].installed is True
    assert reports[0].ready is True
    # The fake executable exits 99 if launched; reaching this assertion proves
    # the doctor only inspected the filesystem.
    assert reports[0].executable_path == executable.resolve()


def test_path_wide_plan_and_doctor_are_ordered_and_use_discovered_structure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "neb"
    for label in ("01", "00"):
        _touch(root / label, "FINAL.vasp")
    _touch(root / "00", "CHGCAR")
    executable = tmp_path / "fake-critic2"
    executable.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | 0o111)
    configuration: dict[str, dict[str, Any]] = {
        "bader": {"enabled": False},
        "baderkit": {"enabled": False},
        "critic2": {"enabled": True, "executable": str(executable)},
        "lobster": {"enabled": False},
        "chargemol": {"enabled": False},
    }

    plans = plan_plugin_path(
        "critic2",
        root,
        structure_name="FINAL.vasp",
        fallback_structure_name="START.vasp",
        configuration=configuration["critic2"],
    )
    reports = doctor_plugins_path(
        root,
        structure_name="FINAL.vasp",
        fallback_structure_name="START.vasp",
        configuration=configuration,
    )

    assert [plan.label for plan in plans] == ["00", "01"]
    assert [plan.directory for plan in plans] == [root / "00", root / "01"]
    assert all("CRYSTAL FINAL.vasp" in (plan.command.stdin or "") for plan in plans)
    assert [report.label for report in reports] == ["00", "01"]
    assert reports[0].ready is True
    assert reports[1].ready is False
    assert reports[1].plugins[0].missing_inputs == (root / "01" / "CHGCAR",)
    assert reports[0].to_dict()["frame"]["label"] == "00"


def test_nonzero_neb_labels_keep_plan_and_doctor_indices_canonical(tmp_path: Path) -> None:
    root = tmp_path / "neb"
    for label in ("09", "08"):
        _touch(root / label, "FINAL.vasp")
    configuration = {
        "bader": {"enabled": False},
        "baderkit": {"enabled": False},
        "critic2": {"enabled": False},
        "lobster": {"enabled": False},
        "chargemol": {"enabled": False},
    }

    plans = plan_plugin_path(
        "critic2",
        root,
        structure_name="FINAL.vasp",
        fallback_structure_name="START.vasp",
    )
    reports = doctor_plugins_path(
        root,
        structure_name="FINAL.vasp",
        fallback_structure_name="START.vasp",
        configuration=configuration,
    )

    assert [(plan.index, plan.label) for plan in plans] == [(0, "08"), (1, "09")]
    assert [(report.index, report.label) for report in reports] == [
        (0, "08"),
        (1, "09"),
    ]


def test_command_plan_validation_and_serialization(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        CommandPlan(
            plugin="test",
            capability=Capability.BADER_CHARGES,
            argv=("tool",),
            cwd=Path("relative"),
        )
    plan = CommandPlan(
        plugin="test",
        capability=Capability.BADER_CHARGES,
        argv=("missing-tool", "input"),
        cwd=tmp_path.resolve(),
        environment={"OMP_NUM_THREADS": "2"},
    )
    payload = plan.to_dict()
    assert payload["argv"] == ["missing-tool", "input"]
    assert payload["capability"] == "bader_charges"
    assert payload["environment"] == {"OMP_NUM_THREADS": "2"}
    assert plan.ready is False
