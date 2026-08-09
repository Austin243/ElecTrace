"""Non-executing plans for external electronic-analysis tools."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


class Capability(StrEnum):
    BADER_CHARGES = "bader_charges"
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
    environment: Mapping[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.plugin.strip() or not self.argv:
            raise ValueError("command plan requires a plugin and argv")
        object.__setattr__(self, "plugin", self.plugin.strip().lower())
        object.__setattr__(self, "capability", Capability(self.capability))
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "required_inputs", tuple(map(Path, self.required_inputs)))
        object.__setattr__(self, "expected_outputs", tuple(map(Path, self.expected_outputs)))
        object.__setattr__(self, "environment", dict(self.environment))
        object.__setattr__(self, "notes", tuple(self.notes))
        if not self.cwd.is_absolute() or any(
            not path.is_absolute() for path in (*self.required_inputs, *self.expected_outputs)
        ):
            raise ValueError("command-plan paths must be absolute")

    @property
    def missing_inputs(self) -> tuple[Path, ...]:
        return tuple(path for path in self.required_inputs if not path.is_file())

    @property
    def ready(self) -> bool:
        return (
            self.cwd.is_dir()
            and resolve_executable(self.argv[0]) is not None
            and not self.missing_inputs
        )

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
            "environment": dict(self.environment),
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
class FrameCommandPlan:
    index: int
    label: str
    directory: Path
    command: CommandPlan

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": {"index": self.index, "label": self.label, "directory": str(self.directory)},
            "command": self.command.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FrameDoctorReport:
    index: int
    label: str
    directory: Path
    plugins: tuple[DoctorReport, ...]

    @property
    def ready(self) -> bool:
        return all(report.ready for report in self.plugins)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": {"index": self.index, "label": self.label, "directory": str(self.directory)},
            "ready": self.ready,
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


class Plugin(Protocol):
    name: str
    default_executable: str
    capabilities: tuple[Capability, ...]

    def doctor(
        self,
        frame_directory: str | Path | None = None,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        **options: Any,
    ) -> DoctorReport: ...

    def plan(
        self,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        **options: Any,
    ) -> CommandPlan: ...
