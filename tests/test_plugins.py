from __future__ import annotations

from pathlib import Path

from electrace.plugins import (
    DEFAULT_REGISTRY,
    Capability,
    doctor_plugins,
    plan_plugin,
)


def _touch(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text("fixture\n", encoding="utf-8")


def test_builtin_registry_exposes_supported_tools() -> None:
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


def test_bader_and_badelf_plans_declare_inputs_and_outputs(tmp_path: Path) -> None:
    _touch(tmp_path, "CHGCAR", "ELFCAR", "CHGCAR_sum")

    bader = plan_plugin(
        "bader",
        tmp_path,
        capability="bader_charges",
        reference_file="CHGCAR_sum",
    )
    badelf = plan_plugin(
        "baderkit",
        tmp_path,
        capability="elf_basins",
        reference_file="CHGCAR_sum",
    )

    assert bader.argv == ("bader", "CHGCAR", "-ref", "CHGCAR_sum")
    assert bader.expected_outputs == (tmp_path / "ACF.dat",)
    assert badelf.argv == (
        "baderkit",
        "badelf",
        "CHGCAR",
        "ELFCAR",
        "-tot",
        "CHGCAR_sum",
    )
    assert badelf.expected_outputs == (tmp_path / "badelf.json",)


def test_critic2_plan_uses_explicit_stdin(tmp_path: Path) -> None:
    _touch(tmp_path, "CONTCAR", "CHGCAR")

    plan = plan_plugin("critic2", tmp_path, capability=Capability.QTAIM_CRITICAL_POINTS)

    assert plan.argv == ("critic2", "-q")
    assert plan.stdin == (
        "CRYSTAL CONTCAR\nLOAD CHGCAR\nAUTO\nCPREPORT critic2.features.json GRAPH\nEND\n"
    )
    assert plan.expected_outputs == (
        tmp_path / "critic2.cro",
        tmp_path / "critic2.features.json",
    )


def test_lobster_and_chargemol_plans(tmp_path: Path) -> None:
    _touch(tmp_path, "lobsterin", "WAVECAR", "POSCAR", "POTCAR", "job_control.txt")

    lobster = plan_plugin("lobster", tmp_path, capability="cobi")
    chargemol = plan_plugin("chargemol", tmp_path)

    assert lobster.expected_outputs == (tmp_path / "ICOBILIST.lobster",)
    assert chargemol.expected_outputs == (tmp_path / "DDEC6_even_tempered_net_atomic_charges.xyz",)


def test_doctor_checks_files_without_launching_executable(tmp_path: Path) -> None:
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
    assert reports[0].installed is True
    assert reports[0].ready is True
