"""Non-executing plans for external electronic-analysis tools."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Capability(StrEnum):
    BADER_CHARGES = "bader_charges"
    SPIN_POPULATIONS = "spin_populations"
    ELF_BASINS = "elf_basins"
    NON_NUCLEAR_ATTRACTORS = "non_nuclear_attractors"
    QTAIM_CRITICAL_POINTS = "qtaim_critical_points"
    COHP = "cohp"
    COBI = "cobi"
    DDEC_CHARGES = "ddec_charges"


@dataclass(frozen=True, slots=True)
class CommandPlan:
    """A shell-free command description; planning never executes it."""

    plugin: str
    capability: Capability
    argv: tuple[str, ...]
    cwd: Path
    required_inputs: tuple[Path, ...] = ()
    expected_outputs: tuple[Path, ...] = ()
    stdin: str | None = None
    stdout_path: Path | None = None
    notes: tuple[str, ...] = ()

    @property
    def missing_inputs(self) -> tuple[Path, ...]:
        return tuple(path for path in self.required_inputs if not path.is_file())

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "capability": self.capability.value,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "required_inputs": [str(path) for path in self.required_inputs],
            "expected_outputs": [str(path) for path in self.expected_outputs],
            "stdin": self.stdin,
            "stdout_path": str(self.stdout_path) if self.stdout_path else None,
            "environment": {},  # no planner sets one; the plan's JSON keeps the key
            "notes": list(self.notes),
            "missing_inputs": [str(path) for path in self.missing_inputs],
        }


@dataclass(frozen=True, slots=True)
class DoctorReport:
    plugin: str
    executable: str
    executable_path: Path | None
    capabilities: tuple[Capability, ...]
    missing_inputs: tuple[Path, ...] = ()
    messages: tuple[str, ...] = ()

    @property
    def installed(self) -> bool:
        return self.executable_path is not None

    @property
    def ready(self) -> bool:
        return self.installed and not self.missing_inputs

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "executable": self.executable,
            "executable_path": str(self.executable_path) if self.executable_path else None,
            "installed": self.installed,
            "ready": self.ready,
            "capabilities": [item.value for item in self.capabilities],
            "missing_inputs": [str(path) for path in self.missing_inputs],
            "messages": list(self.messages),
        }


@dataclass(frozen=True, slots=True)
class FrameDoctorReport:
    index: int
    label: str
    directory: Path
    plugins: tuple[DoctorReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": {"index": self.index, "label": self.label, "directory": str(self.directory)},
            "ready": all(report.ready for report in self.plugins),
            "plugins": [report.to_dict() for report in self.plugins],
        }


def resolve_executable(executable: str) -> Path | None:
    candidate = Path(executable).expanduser()
    if candidate.is_absolute() or len(candidate.parts) > 1:
        return (
            candidate.resolve() if candidate.is_file() and os.access(candidate, os.X_OK) else None
        )
    found = shutil.which(executable)
    return Path(found).resolve() if found else None
