"""Non-executing plugin contracts for external electronic-analysis tools."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol


class Capability(StrEnum):
    """Scientific result families a plugin can produce."""

    BADER_CHARGES = "bader_charges"
    ELF_BASINS = "elf_basins"
    NON_NUCLEAR_ATTRACTORS = "non_nuclear_attractors"
    QTAIM_CRITICAL_POINTS = "qtaim_critical_points"
    COHP = "cohp"
    COBI = "cobi"
    DDEC_CHARGES = "ddec_charges"


@dataclass(frozen=True, slots=True)
class CommandPlan:
    """A serializable, shell-free plan for a command that has *not* run.

    ``argv`` is intentionally a token tuple instead of a shell command.  A
    workflow layer may display, serialize, or explicitly approve and execute a
    plan later; plugin planning itself never invokes external science tools.
    ``stdout_path`` expresses safe output capture without embedding ``>`` in a
    shell string.
    """

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
        plugin = str(self.plugin).strip().lower()
        if not plugin:
            raise ValueError("plugin name must not be empty")
        object.__setattr__(self, "plugin", plugin)
        try:
            capability = (
                self.capability
                if isinstance(self.capability, Capability)
                else Capability(self.capability)
            )
        except ValueError as exc:
            raise ValueError(f"unknown command-plan capability {self.capability!r}") from exc
        object.__setattr__(self, "capability", capability)
        argv = tuple(self.argv)
        if not argv or any(not isinstance(token, str) or not token for token in argv):
            raise ValueError("argv must contain non-empty string tokens")
        if any("\x00" in token for token in argv):
            raise ValueError("argv tokens must not contain NUL bytes")
        object.__setattr__(self, "argv", argv)
        cwd = Path(self.cwd)
        if not cwd.is_absolute():
            raise ValueError("command-plan cwd must be absolute")
        object.__setattr__(self, "cwd", cwd)
        for field_name in ("required_inputs", "expected_outputs"):
            paths = tuple(Path(path) for path in getattr(self, field_name))
            if any(not path.is_absolute() for path in paths):
                raise ValueError(f"command-plan {field_name} must contain only absolute paths")
            object.__setattr__(self, field_name, paths)
        if self.stdout_path is not None:
            stdout_path = Path(self.stdout_path)
            if not stdout_path.is_absolute():
                raise ValueError("command-plan stdout_path must be absolute")
            object.__setattr__(self, "stdout_path", stdout_path)
        if self.stdin is not None and "\x00" in self.stdin:
            raise ValueError("stdin must not contain NUL bytes")
        environment = dict(self.environment)
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or "\x00" in key
            or "\x00" in value
            for key, value in environment.items()
        ):
            raise ValueError("command-plan environment must contain NUL-free string pairs")
        object.__setattr__(self, "environment", MappingProxyType(environment))
        notes = tuple(self.notes)
        if any(not isinstance(note, str) for note in notes):
            raise ValueError("command-plan notes must contain strings")
        object.__setattr__(self, "notes", notes)

    @property
    def missing_inputs(self) -> tuple[Path, ...]:
        """Required inputs that do not currently exist."""

        return tuple(path for path in self.required_inputs if not path.is_file())

    @property
    def ready(self) -> bool:
        """Whether the executable and all declared inputs are available."""

        executable = self.argv[0]
        command_path = Path(executable).expanduser()
        if command_path.is_absolute():
            executable_available = command_path.is_file() and os.access(command_path, os.X_OK)
        elif len(command_path.parts) > 1:
            candidate = self.cwd / command_path
            executable_available = candidate.is_file() and os.access(candidate, os.X_OK)
        else:
            executable_available = shutil.which(executable) is not None
        return self.cwd.is_dir() and executable_available and not self.missing_inputs

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable audit record."""

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
    """Availability and input-readiness report for one plugin."""

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
            "capabilities": [capability.value for capability in self.capabilities],
            "missing_inputs": [str(path) for path in self.missing_inputs],
            "messages": list(self.messages),
        }


@dataclass(frozen=True, slots=True)
class FrameCommandPlan:
    """A command plan associated with one discovered path frame."""

    index: int
    label: str
    directory: Path
    command: CommandPlan

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("frame index must be non-negative")
        if not self.label or not self.label.strip():
            raise ValueError("frame label must not be empty")
        directory = Path(self.directory)
        if not directory.is_absolute():
            raise ValueError("frame directory must be absolute")
        if self.command.cwd != directory:
            raise ValueError("frame directory must match command-plan cwd")
        object.__setattr__(self, "directory", directory)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": {
                "index": self.index,
                "label": self.label,
                "directory": str(self.directory),
            },
            "command": self.command.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FrameDoctorReport:
    """Read-only plugin checks associated with one discovered path frame."""

    index: int
    label: str
    directory: Path
    plugins: tuple[DoctorReport, ...]

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("frame index must be non-negative")
        if not self.label or not self.label.strip():
            raise ValueError("frame label must not be empty")
        directory = Path(self.directory)
        if not directory.is_absolute():
            raise ValueError("frame directory must be absolute")
        object.__setattr__(self, "directory", directory)
        object.__setattr__(self, "plugins", tuple(self.plugins))

    @property
    def ready(self) -> bool:
        return all(report.ready for report in self.plugins)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": {
                "index": self.index,
                "label": self.label,
                "directory": str(self.directory),
            },
            "ready": self.ready,
            "plugins": [report.to_dict() for report in self.plugins],
        }


def resolve_executable(executable: str) -> Path | None:
    """Resolve an explicit path or PATH executable without launching it."""

    candidate = Path(executable).expanduser()
    if candidate.is_absolute() or len(candidate.parts) > 1:
        return (
            candidate.resolve() if candidate.is_file() and os.access(candidate, os.X_OK) else None
        )
    found = shutil.which(executable)
    return Path(found).resolve() if found else None


class Plugin(Protocol):
    """Structural interface implemented by built-in and third-party plugins."""

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
