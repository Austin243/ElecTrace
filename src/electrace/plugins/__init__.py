"""External analysis plugin discovery and non-executing command planning."""

from .base import (
    Capability,
    CommandPlan,
    DoctorReport,
    FrameCommandPlan,
    FrameDoctorReport,
    Plugin,
    resolve_executable,
)
from .builtin import BaderKitPlugin, BaderPlugin, ChargemolPlugin, Critic2Plugin, LobsterPlugin
from .registry import (
    DEFAULT_REGISTRY,
    PluginRegistry,
    doctor_plugins,
    doctor_plugins_path,
    plan_plugin,
    plan_plugin_path,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "BaderKitPlugin",
    "BaderPlugin",
    "Capability",
    "ChargemolPlugin",
    "CommandPlan",
    "Critic2Plugin",
    "DoctorReport",
    "FrameCommandPlan",
    "FrameDoctorReport",
    "LobsterPlugin",
    "Plugin",
    "PluginRegistry",
    "doctor_plugins",
    "doctor_plugins_path",
    "plan_plugin",
    "plan_plugin_path",
    "resolve_executable",
]
