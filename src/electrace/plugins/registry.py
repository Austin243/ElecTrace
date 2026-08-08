"""Plugin registration, lookup, capability filtering, and doctor checks."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from electrace.io.discovery import SeriesKind, discover_path

from .base import (
    Capability,
    CommandPlan,
    DoctorReport,
    FrameCommandPlan,
    FrameDoctorReport,
    Plugin,
)
from .builtin import BUILTIN_PLUGINS


class PluginRegistry:
    """A small explicit registry; importing a module never runs a plugin."""

    def __init__(self, plugins: Iterable[Plugin] = ()) -> None:
        self._plugins: dict[str, Plugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: Plugin, *, replace: bool = False) -> None:
        name = plugin.name.strip().lower()
        if not name:
            raise ValueError("plugin name must not be empty")
        if name in self._plugins and not replace:
            raise ValueError(f"plugin {name!r} is already registered")
        if not plugin.capabilities:
            raise ValueError(f"plugin {name!r} declares no capabilities")
        self._plugins[name] = plugin

    def get(self, name: str) -> Plugin:
        normalized = name.strip().lower()
        try:
            return self._plugins[normalized]
        except KeyError as exc:
            choices = ", ".join(self.names()) or "none"
            raise KeyError(f"unknown plugin {name!r}; registered plugins: {choices}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))

    def for_capability(self, capability: Capability | str) -> tuple[Plugin, ...]:
        selected = capability if isinstance(capability, Capability) else Capability(str(capability))
        return tuple(plugin for plugin in self._plugins.values() if selected in plugin.capabilities)

    def doctor(
        self,
        frame_directory: str | Path | None = None,
        *,
        configuration: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[DoctorReport, ...]:
        """Inspect executable and input availability without running commands."""

        configuration = configuration or {}
        reports = []
        for name in self.names():
            options = dict(configuration.get(name, {}))
            if options.pop("enabled", True) is False:
                continue
            executable = options.pop("executable", None)
            capability = options.pop("capability", None)
            reports.append(
                self._plugins[name].doctor(
                    frame_directory,
                    capability=capability,
                    executable=executable,
                    **options,
                )
            )
        return tuple(reports)

    def doctor_path(
        self,
        path: str | Path,
        *,
        configuration: dict[str, dict[str, Any]] | None = None,
        mode: str | SeriesKind = "auto",
        prefer_contcar: bool = True,
        structure_name: str = "CONTCAR",
        fallback_structure_name: str = "POSCAR",
        strict: bool = True,
    ) -> tuple[FrameDoctorReport, ...]:
        """Inspect every discovered frame in deterministic path order."""

        discovered = discover_path(
            path,
            mode=mode,
            prefer_contcar=prefer_contcar,
            structure_name=structure_name,
            fallback_structure_name=fallback_structure_name,
            strict=strict,
        )
        return tuple(
            FrameDoctorReport(
                index=frame.index,
                label=frame.label,
                directory=frame.directory,
                plugins=self.doctor(
                    frame.directory,
                    configuration=_configuration_for_frame(
                        configuration, critic2_structure_name=frame.structure.name
                    ),
                ),
            )
            for frame in discovered.frames
        )

    def plan(
        self,
        name: str,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        configuration: dict[str, Any] | None = None,
        **options: Any,
    ) -> CommandPlan:
        plugin = self.get(name)
        configured = dict(configuration or {})
        if configured.pop("enabled", True) is False:
            raise ValueError(f"plugin {name!r} is disabled")
        executable = configured.pop("executable", None)
        configured_capability = configured.pop("capability", None)
        selected_capability = capability if capability is not None else configured_capability
        configured.update(options)
        return plugin.plan(
            frame_directory,
            capability=selected_capability,
            executable=executable,
            **configured,
        )

    def plan_path(
        self,
        name: str,
        path: str | Path,
        *,
        capability: Capability | str | None = None,
        configuration: dict[str, Any] | None = None,
        mode: str | SeriesKind = "auto",
        prefer_contcar: bool = True,
        structure_name: str = "CONTCAR",
        fallback_structure_name: str = "POSCAR",
        strict: bool = True,
        **options: Any,
    ) -> tuple[FrameCommandPlan, ...]:
        """Build one non-executing plan per discovered frame, in path order."""

        discovered = discover_path(
            path,
            mode=mode,
            prefer_contcar=prefer_contcar,
            structure_name=structure_name,
            fallback_structure_name=fallback_structure_name,
            strict=strict,
        )
        return tuple(
            FrameCommandPlan(
                index=frame.index,
                label=frame.label,
                directory=frame.directory,
                command=self.plan(
                    name,
                    frame.directory,
                    capability=capability,
                    configuration=(
                        {
                            **dict(configuration or {}),
                            "structure_file": frame.structure.name,
                        }
                        if name.strip().lower() == "critic2"
                        and "structure_file" not in (configuration or {})
                        and "structure_file" not in options
                        else configuration
                    ),
                    **options,
                ),
            )
            for frame in discovered.frames
        )


def _configuration_for_frame(
    configuration: dict[str, dict[str, Any]] | None,
    *,
    critic2_structure_name: str,
) -> dict[str, dict[str, Any]]:
    """Copy plugin configuration and bind critic2 to the discovered structure."""

    result = {name: dict(options) for name, options in (configuration or {}).items()}
    critic2 = result.setdefault("critic2", {})
    critic2.setdefault("structure_file", critic2_structure_name)
    return result


DEFAULT_REGISTRY = PluginRegistry(cast(Iterable[Plugin], BUILTIN_PLUGINS))


def doctor_plugins(
    frame_directory: str | Path | None = None,
    *,
    configuration: dict[str, dict[str, Any]] | None = None,
) -> tuple[DoctorReport, ...]:
    """Run read-only availability checks for enabled built-in plugins."""

    return DEFAULT_REGISTRY.doctor(frame_directory, configuration=configuration)


def doctor_plugins_path(
    path: str | Path,
    *,
    configuration: dict[str, dict[str, Any]] | None = None,
    mode: str | SeriesKind = "auto",
    prefer_contcar: bool = True,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
    strict: bool = True,
) -> tuple[FrameDoctorReport, ...]:
    """Run read-only built-in plugin checks for every discovered frame."""

    return DEFAULT_REGISTRY.doctor_path(
        path,
        configuration=configuration,
        mode=mode,
        prefer_contcar=prefer_contcar,
        structure_name=structure_name,
        fallback_structure_name=fallback_structure_name,
        strict=strict,
    )


def plan_plugin(
    name: str,
    frame_directory: str | Path,
    *,
    capability: Capability | str | None = None,
    configuration: dict[str, Any] | None = None,
    **options: Any,
) -> CommandPlan:
    """Build, but do not execute, a command plan for a built-in plugin."""

    return DEFAULT_REGISTRY.plan(
        name,
        frame_directory,
        capability=capability,
        configuration=configuration,
        **options,
    )


def plan_plugin_path(
    name: str,
    path: str | Path,
    *,
    capability: Capability | str | None = None,
    configuration: dict[str, Any] | None = None,
    mode: str | SeriesKind = "auto",
    prefer_contcar: bool = True,
    structure_name: str = "CONTCAR",
    fallback_structure_name: str = "POSCAR",
    strict: bool = True,
    **options: Any,
) -> tuple[FrameCommandPlan, ...]:
    """Build deterministic non-executing plans for a complete VASP path."""

    return DEFAULT_REGISTRY.plan_path(
        name,
        path,
        capability=capability,
        configuration=configuration,
        mode=mode,
        prefer_contcar=prefer_contcar,
        structure_name=structure_name,
        fallback_structure_name=fallback_structure_name,
        strict=strict,
        **options,
    )
