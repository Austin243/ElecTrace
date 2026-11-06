"""Apply configured options to the built-in command planners."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from electrace.io.discovery import FrameSource

from .base import Capability, CommandPlan, DoctorReport, FrameDoctorReport
from .builtin import PLUGINS


def doctor_plugins(
    frame_directory: str | Path | None = None,
    *,
    configuration: dict[str, dict[str, Any]] | None = None,
) -> tuple[DoctorReport, ...]:
    reports = []
    for name, plugin in PLUGINS.items():
        options = dict((configuration or {}).get(name, {}))
        if not options.pop("enabled", True):
            continue
        reports.append(
            plugin.doctor(
                frame_directory,
                executable=options.pop("executable", None),
                capability=options.pop("capability", None),
                **options,
            )
        )
    return tuple(reports)


class PluginRegistry:
    # A class only because Python names it in the error for a workflow step option called
    # "self": "PluginRegistry.plan() got multiple values for argument 'self'".

    def plan(
        self,
        name: str,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        configuration: dict[str, Any] | None = None,
        **options: Any,
    ) -> CommandPlan:
        settings = dict(configuration or {})
        if not settings.pop("enabled", True):
            raise ValueError(f"plugin {name!r} is disabled")
        settings.update(options)
        plugin = PLUGINS.get(name.strip().lower())
        if plugin is None:
            raise KeyError(f"unknown plugin {name!r}; choose from {', '.join(PLUGINS)}")
        return plugin.plan(
            frame_directory,
            capability=capability or settings.pop("capability", None),
            executable=settings.pop("executable", None),
            **settings,
        )


def plan_plugin(name: str, frame_directory: str | Path, **options: Any) -> CommandPlan:
    return PluginRegistry().plan(name, frame_directory, **options)


def doctor_plugins_frames(
    frames: Iterable[FrameSource], *, configuration: dict[str, dict[str, Any]]
) -> tuple[FrameDoctorReport, ...]:
    reports = []
    for frame in frames:
        frame_configuration = {name: dict(values) for name, values in configuration.items()}
        critic2 = frame_configuration.setdefault("critic2", {})
        critic2.setdefault("structure_file", frame.structure.name)
        reports.append(
            FrameDoctorReport(
                frame.index,
                frame.label,
                frame.directory,
                doctor_plugins(frame.directory, configuration=frame_configuration),
            )
        )
    return tuple(reports)


def plan_plugin_frames(
    name: str,
    frames: Iterable[FrameSource],
    *,
    capability: str | None,
    configuration: dict[str, Any],
) -> list[dict[str, Any]]:
    """Plan *name*, already lowercase and stripped, for each of *frames*."""

    plans = []
    for frame in frames:
        settings = configuration
        if name == "critic2":
            settings = {"structure_file": frame.structure.name, **configuration}
        command = plan_plugin(name, frame.directory, capability=capability, configuration=settings)
        plans.append(
            {
                "frame": {
                    "index": frame.index,
                    "label": frame.label,
                    "directory": str(frame.directory),
                },
                "command": command.to_dict(),
            }
        )
    return plans
