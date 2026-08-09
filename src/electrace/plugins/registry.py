"""Registry and convenience functions for built-in command planners."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from electrace.io.discovery import discover_path

from .base import Capability, CommandPlan, DoctorReport, FrameCommandPlan, FrameDoctorReport, Plugin
from .builtin import BUILTIN_PLUGINS


class PluginRegistry:
    def __init__(self, plugins: Iterable[Plugin] = ()) -> None:
        self._plugins: dict[str, Plugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: Plugin, *, replace: bool = False) -> None:
        name = plugin.name.strip().lower()
        if not name or not plugin.capabilities:
            raise ValueError("plugin requires a name and capabilities")
        if name in self._plugins and not replace:
            raise ValueError(f"plugin {name!r} is already registered")
        self._plugins[name] = plugin

    def get(self, name: str) -> Plugin:
        try:
            return self._plugins[name.strip().lower()]
        except KeyError as exc:
            raise KeyError(
                f"unknown plugin {name!r}; choose from {', '.join(self.names())}"
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))

    def for_capability(self, capability: Capability | str) -> tuple[Plugin, ...]:
        selected = Capability(capability)
        return tuple(plugin for plugin in self._plugins.values() if selected in plugin.capabilities)

    def doctor(
        self,
        frame_directory: str | Path | None = None,
        *,
        configuration: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[DoctorReport, ...]:
        reports = []
        for name in self.names():
            options = dict((configuration or {}).get(name, {}))
            if not options.pop("enabled", True):
                continue
            reports.append(
                self._plugins[name].doctor(
                    frame_directory,
                    executable=options.pop("executable", None),
                    capability=options.pop("capability", None),
                    **options,
                )
            )
        return tuple(reports)

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
        return self.get(name).plan(
            frame_directory,
            capability=capability or settings.pop("capability", None),
            executable=settings.pop("executable", None),
            **settings,
        )

    def doctor_path(self, path: str | Path, **options: Any) -> tuple[FrameDoctorReport, ...]:
        configuration = options.pop("configuration", None)
        discovered = discover_path(path, **options)
        return tuple(
            FrameDoctorReport(
                frame.index,
                frame.label,
                frame.directory,
                self.doctor(
                    frame.directory,
                    configuration=_frame_configuration(configuration, frame.structure.name),
                ),
            )
            for frame in discovered.frames
        )

    def plan_path(
        self,
        name: str,
        path: str | Path,
        *,
        capability: Capability | str | None = None,
        configuration: dict[str, Any] | None = None,
        **options: Any,
    ) -> tuple[FrameCommandPlan, ...]:
        discovery_keys = {
            key: options.pop(key)
            for key in tuple(options)
            if key
            in {"mode", "prefer_contcar", "structure_name", "fallback_structure_name", "strict"}
        }
        discovered = discover_path(path, **discovery_keys)
        return tuple(
            FrameCommandPlan(
                frame.index,
                frame.label,
                frame.directory,
                self.plan(
                    name,
                    frame.directory,
                    capability=capability,
                    configuration=_single_frame_configuration(
                        name, configuration, frame.structure.name, options
                    ),
                    **options,
                ),
            )
            for frame in discovered.frames
        )


def _frame_configuration(
    configuration: dict[str, dict[str, Any]] | None, structure: str
) -> dict[str, dict[str, Any]]:
    result = {name: dict(values) for name, values in (configuration or {}).items()}
    result.setdefault("critic2", {}).setdefault("structure_file", structure)
    return result


def _single_frame_configuration(
    name: str, configuration: dict[str, Any] | None, structure: str, options: dict[str, Any]
) -> dict[str, Any] | None:
    if name.strip().lower() != "critic2" or "structure_file" in options:
        return configuration
    result = dict(configuration or {})
    result.setdefault("structure_file", structure)
    return result


DEFAULT_REGISTRY = PluginRegistry(cast(Iterable[Plugin], BUILTIN_PLUGINS))


def doctor_plugins(
    frame_directory: str | Path | None = None, **options: Any
) -> tuple[DoctorReport, ...]:
    return DEFAULT_REGISTRY.doctor(frame_directory, **options)


def doctor_plugins_path(path: str | Path, **options: Any) -> tuple[FrameDoctorReport, ...]:
    return DEFAULT_REGISTRY.doctor_path(path, **options)


def plan_plugin(name: str, frame_directory: str | Path, **options: Any) -> CommandPlan:
    return DEFAULT_REGISTRY.plan(name, frame_directory, **options)


def plan_plugin_path(name: str, path: str | Path, **options: Any) -> tuple[FrameCommandPlan, ...]:
    return DEFAULT_REGISTRY.plan_path(name, path, **options)
