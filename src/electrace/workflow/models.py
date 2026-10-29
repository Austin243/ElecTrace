"""Typed models for ``electrace.workflow.v1`` manifests."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias, cast

WORKFLOW_SCHEMA_VERSION = "electrace.workflow.v1"

JSONScalar: TypeAlias = bool | int | float | str | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

_SERIES_KINDS = {"auto", "neb", "ordered", "pressure", "static"}
_STEP_KINDS = {"bader", "baderkit", "critic2", "lobster", "vasp", "volume"}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*\Z")
_CAPABILITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_DIRECTIVE = re.compile(r"[a-z][a-z0-9-]*\Z")
_DIRECTIVE_VALUE = re.compile(r"[A-Za-z0-9_./,:@%+=\[\]*?{}~-]+\Z")
_UNSAFE_DIRECTIVES = {"command", "job-name", "script", "wrap"}


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{path} must be non-empty text")
    return value.strip()


def _identifier(value: object, path: str) -> str:
    result = _text(value, path)
    if _IDENTIFIER.fullmatch(result) is None:
        raise ValueError(f"{path} must use lowercase letters, digits, '-' or '_'")
    return result


def _basename(value: object, path: str) -> str:
    result = _text(value, path)
    if result in {".", ".."} or Path(result).name != result or "\\" in result:
        raise ValueError(f"{path} must be a basename")
    return result


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{path} must be a string-keyed mapping")
    return dict(value)


def _sequence(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be a sequence")
    return value


def _fields(data: dict[str, Any], required: set[str], allowed: set[str], path: str) -> None:
    if missing := required - data.keys():
        raise ValueError(f"{path} is missing required keys: {', '.join(sorted(missing))}")
    if unknown := data.keys() - allowed:
        raise ValueError(f"{path} contains unknown keys: {', '.join(sorted(unknown))}")


def _json_object(value: object, path: str) -> dict[str, JSONValue]:
    result = _mapping(value, path)
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"{path} must contain finite JSON values") from exc
    return cast(dict[str, JSONValue], result)


@dataclass(frozen=True, slots=True)
class WorkflowProject:
    root: str
    series_kind: str
    workspace: str
    structure: str
    fallback: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", _text(self.root, "project.root"))
        kind = _text(self.series_kind, "project.series_kind")
        if kind not in _SERIES_KINDS:
            raise ValueError(f"unsupported project.series_kind: {kind}")
        object.__setattr__(self, "series_kind", kind)
        object.__setattr__(self, "workspace", _text(self.workspace, "project.workspace"))
        object.__setattr__(self, "structure", _basename(self.structure, "project.structure"))
        object.__setattr__(self, "fallback", _basename(self.fallback, "project.fallback"))

    @classmethod
    def from_dict(cls, value: object) -> WorkflowProject:
        data = _mapping(value, "project")
        keys = {"root", "series_kind", "workspace", "structure", "fallback"}
        _fields(data, keys, keys, "project")
        return cls(**{key: _text(data[key], f"project.{key}") for key in keys})

    def to_dict(self) -> dict[str, str]:
        return {
            "root": self.root,
            "series_kind": self.series_kind,
            "workspace": self.workspace,
            "structure": self.structure,
            "fallback": self.fallback,
        }


@dataclass(frozen=True, slots=True)
class ExecutableSpec:
    path: str
    argv_prefix: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "executable.path"))
        object.__setattr__(
            self,
            "argv_prefix",
            tuple(_text(token, "executable.argv_prefix") for token in self.argv_prefix),
        )

    @classmethod
    def from_dict(cls, value: object, *, path: str = "executable") -> ExecutableSpec:
        data = _mapping(value, path)
        _fields(data, {"path"}, {"path", "argv_prefix"}, path)
        prefix = _sequence(data.get("argv_prefix", []), f"{path}.argv_prefix")
        return cls(_text(data["path"], f"{path}.path"), tuple(prefix))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"path": self.path}
        if self.argv_prefix:
            result["argv_prefix"] = list(self.argv_prefix)
        return result


@dataclass(frozen=True, slots=True)
class SchedulerSpec:
    kind: str
    directives: dict[str, JSONScalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = _text(self.kind, "scheduler.kind")
        if kind not in {"local", "slurm"}:
            raise ValueError("scheduler.kind must be local or slurm")
        directives: dict[str, JSONScalar] = {}
        for key, value in self.directives.items():
            if _DIRECTIVE.fullmatch(key) is None or key in _UNSAFE_DIRECTIVES:
                raise ValueError(f"unsafe Slurm directive: {key!r}")
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise TypeError(f"scheduler.directives.{key} must be a scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"scheduler.directives.{key} must be finite")
            if isinstance(value, str) and _DIRECTIVE_VALUE.fullmatch(value) is None:
                raise ValueError(f"unsafe Slurm directive value for {key!r}")
            directives[key] = value
        if kind == "local" and directives:
            raise ValueError("local scheduler does not accept Slurm directives")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "directives", directives)

    @classmethod
    def from_dict(cls, value: object) -> SchedulerSpec:
        data = _mapping(value, "scheduler")
        _fields(data, {"kind"}, {"kind", "directives"}, "scheduler")
        return cls(
            _text(data["kind"], "scheduler.kind"),
            _mapping(data.get("directives", {}), "scheduler.directives"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "directives": dict(self.directives)}


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    id: str
    kind: str
    executable: str
    capability: str | None = None
    template_dir: str | None = None
    depends_on: tuple[str, ...] = ()
    options: dict[str, JSONValue] = field(default_factory=dict)
    resources: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifier = _identifier(self.id, "step.id")
        kind = _text(self.kind, f"step {identifier}.kind")
        if kind not in _STEP_KINDS:
            raise ValueError(f"unsupported workflow step kind: {kind}")
        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "executable", _identifier(self.executable, "step.executable"))
        if self.capability is not None:
            capability = _text(self.capability, "step.capability")
            if _CAPABILITY.fullmatch(capability) is None:
                raise ValueError("step.capability contains unsafe characters")
            object.__setattr__(self, "capability", capability)
        if self.template_dir is not None:
            object.__setattr__(self, "template_dir", _text(self.template_dir, "step.template_dir"))
        dependencies = tuple(_identifier(item, "step.depends_on") for item in self.depends_on)
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("step.depends_on contains duplicates")
        object.__setattr__(self, "depends_on", dependencies)
        object.__setattr__(self, "options", _json_object(self.options, "step.options"))
        object.__setattr__(self, "resources", _json_object(self.resources, "step.resources"))

    @classmethod
    def from_dict(cls, value: object, *, index: int) -> WorkflowStep:
        path = f"steps[{index}]"
        data = _mapping(value, path)
        allowed = {
            "id",
            "kind",
            "executable",
            "capability",
            "template_dir",
            "depends_on",
            "options",
            "resources",
        }
        _fields(data, {"id", "kind", "executable"}, allowed, path)
        dependencies = _sequence(data.get("depends_on", []), f"{path}.depends_on")
        return cls(
            id=_text(data["id"], f"{path}.id"),
            kind=_text(data["kind"], f"{path}.kind"),
            executable=_text(data["executable"], f"{path}.executable"),
            capability=data.get("capability"),
            template_dir=data.get("template_dir"),
            depends_on=tuple(dependencies),
            options=_json_object(data.get("options", {}), f"{path}.options"),
            resources=_json_object(data.get("resources", {}), f"{path}.resources"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "executable": self.executable,
            "depends_on": list(self.depends_on),
            "options": dict(self.options),
            "resources": dict(self.resources),
        }
        if self.capability is not None:
            result["capability"] = self.capability
        if self.template_dir is not None:
            result["template_dir"] = self.template_dir
        return result


@dataclass(frozen=True, slots=True)
class WorkflowManifest:
    schema_version: str
    project: WorkflowProject
    frames: str
    executables: dict[str, ExecutableSpec]
    scheduler: SchedulerSpec
    steps: tuple[WorkflowStep, ...]

    def __post_init__(self) -> None:
        if self.schema_version != WORKFLOW_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {WORKFLOW_SCHEMA_VERSION!r}")
        from .selection import validate_frame_selection

        validate_frame_selection(self.frames)
        if not self.executables or not self.steps:
            raise ValueError("workflow requires executables and steps")
        ids: set[str] = set()
        for step in self.steps:
            if step.id in ids:
                raise ValueError(f"duplicate workflow step id: {step.id}")
            if step.executable not in self.executables:
                raise ValueError(f"step {step.id!r} references an unknown executable")
            unknown = set(step.depends_on) - ids
            if unknown:
                raise ValueError(f"step {step.id!r} dependencies must name earlier steps")
            ids.add(step.id)

    @classmethod
    def from_dict(cls, value: object) -> WorkflowManifest:
        data = _mapping(value, "workflow manifest")
        keys = {"schema_version", "project", "frames", "executables", "scheduler", "steps"}
        _fields(data, keys, keys, "workflow manifest")
        executable_data = _mapping(data["executables"], "executables")
        steps = _sequence(data["steps"], "steps")
        return cls(
            schema_version=_text(data["schema_version"], "schema_version"),
            project=WorkflowProject.from_dict(data["project"]),
            frames=_text(data["frames"], "frames"),
            executables={
                name: ExecutableSpec.from_dict(spec, path=f"executables.{name}")
                for name, spec in executable_data.items()
            },
            scheduler=SchedulerSpec.from_dict(data["scheduler"]),
            steps=tuple(
                WorkflowStep.from_dict(step, index=index) for index, step in enumerate(steps)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project": self.project.to_dict(),
            "frames": self.frames,
            "executables": {name: value.to_dict() for name, value in self.executables.items()},
            "scheduler": self.scheduler.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
        }


__all__ = [
    "WORKFLOW_SCHEMA_VERSION",
    "ExecutableSpec",
    "JSONScalar",
    "JSONValue",
    "SchedulerSpec",
    "WorkflowManifest",
    "WorkflowProject",
    "WorkflowStep",
]
