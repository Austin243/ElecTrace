"""Typed, JSON-ready models for versioned ElecTrace workflow manifests.

The workflow contract describes command *planning*.  It deliberately contains
no shell snippets: executable prefixes are argument vectors, scheduler settings
are scalar directives, and multiline input belongs in reviewed template files.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias, cast

WORKFLOW_SCHEMA_VERSION = "electrace.workflow.v1"

JSONScalar: TypeAlias = bool | int | float | str | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

_SERIES_KINDS = frozenset({"auto", "neb", "ordered", "pressure", "static"})
_STEP_KINDS = frozenset({"bader", "baderkit", "critic2", "lobster", "vasp"})
_SCHEDULER_KINDS = frozenset({"local", "slurm"})
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
_CAPABILITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_DIRECTIVE = re.compile(r"^[a-z][a-z0-9-]*$")
_DIRECTIVE_VALUE = re.compile(r"^[A-Za-z0-9_./,:@%+=\[\]*?{}~-]+$")
_UNSAFE_SLURM_DIRECTIVES = frozenset({"command", "job-name", "script", "wrap"})


def _has_control_characters(value: str) -> bool:
    return any(
        ord(character) < 32 or 127 <= ord(character) <= 159 or character in {"\u2028", "\u2029"}
        for character in value
    )


def _required_string(value: object, path: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{path} must be a string")
    result = value
    if not result or result != result.strip() or _has_control_characters(result):
        raise ValueError(
            f"{path} must be a non-empty string without surrounding whitespace "
            "or control characters"
        )
    return result


def _path_string(value: object, path: str) -> str:
    """Validate a path-shaped value without resolving it or requiring existence."""

    return _required_string(value, path)


def _basename(value: object, path: str) -> str:
    result = _required_string(value, path)
    if result in {".", ".."} or "/" in result or "\\" in result or Path(result).is_absolute():
        raise ValueError(f"{path} must be a basename, not a path: {result!r}")
    return result


def _identifier(value: object, path: str) -> str:
    result = _required_string(value, path)
    if _IDENTIFIER.fullmatch(result) is None:
        raise ValueError(
            f"{path} must start with a lowercase letter and contain only "
            "lowercase letters, digits, '-' or '_'"
        )
    return result


def _capability(value: object, path: str) -> str:
    result = _required_string(value, path)
    if _CAPABILITY.fullmatch(result) is None:
        raise ValueError(f"{path} contains unsafe characters")
    return result


def _json_copy(
    value: object,
    *,
    path: str,
    _active: set[int] | None = None,
    _depth: int = 0,
) -> JSONValue:
    """Return a strict JSON copy, rejecting YAML-only and unsafe values."""

    if _depth > 64:
        raise ValueError(f"{path} exceeds the maximum JSON nesting depth of 64")
    if value is None or type(value) is bool or type(value) is int:
        return cast(JSONScalar, value)
    if type(value) is float:
        number = value
        if not math.isfinite(number):
            raise ValueError(f"{path} must be finite for JSON serialization")
        return number
    if type(value) is str:
        text = value
        if _has_control_characters(text):
            raise ValueError(f"{path} must not contain control characters or newlines")
        return text
    if type(value) is list:
        sequence = cast(list[object], value)
        active = set() if _active is None else _active
        identity = id(sequence)
        if identity in active:
            raise ValueError(f"{path} contains a cyclic sequence")
        active.add(identity)
        try:
            return [
                _json_copy(
                    item,
                    path=f"{path}[{index}]",
                    _active=active,
                    _depth=_depth + 1,
                )
                for index, item in enumerate(sequence)
            ]
        finally:
            active.remove(identity)
    if type(value) is dict:
        mapping = cast(dict[object, object], value)
        active = set() if _active is None else _active
        identity = id(mapping)
        if identity in active:
            raise ValueError(f"{path} contains a cyclic mapping")
        active.add(identity)
        result: dict[str, JSONValue] = {}
        try:
            for key, item in mapping.items():
                if type(key) is not str:
                    raise ValueError(f"{path} contains non-string key {key!r}")
                string_key = key
                if _has_control_characters(string_key):
                    raise ValueError(f"{path} contains an unsafe key {string_key!r}")
                result[string_key] = _json_copy(
                    item,
                    path=f"{path}.{string_key}",
                    _active=active,
                    _depth=_depth + 1,
                )
            return result
        finally:
            active.remove(identity)
    raise ValueError(f"{path} has non-JSON-compatible value of type {type(value).__name__}")


def _table(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        raise TypeError(f"{path} must be a mapping")
    table = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in table.items():
        if type(key) is not str:
            raise ValueError(f"{path} contains non-string key {key!r}")
        result[key] = item
    return result


def _list(value: object, path: str) -> list[object]:
    if type(value) is not list:
        raise TypeError(f"{path} must be a sequence")
    return cast(list[object], value)


def _check_keys(
    data: dict[str, object],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    path: str,
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} contains unknown keys: {', '.join(unknown)}")
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"{path} is missing required keys: {', '.join(missing)}")


def _json_object(value: object, path: str) -> dict[str, JSONValue]:
    copied = _json_copy(value, path=path)
    if type(copied) is not dict:
        raise TypeError(f"{path} must be a mapping")
    return copied


@dataclass(frozen=True, slots=True)
class WorkflowProject:
    """Discovery and workspace settings shared by every workflow step."""

    root: str
    series_kind: str
    workspace: str
    structure: str
    fallback: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", _path_string(self.root, "project.root"))
        series_kind = _required_string(self.series_kind, "project.series_kind")
        if series_kind not in _SERIES_KINDS:
            choices = ", ".join(sorted(_SERIES_KINDS))
            raise ValueError(f"project.series_kind must be one of {choices}")
        object.__setattr__(self, "series_kind", series_kind)
        object.__setattr__(self, "workspace", _path_string(self.workspace, "project.workspace"))
        object.__setattr__(self, "structure", _basename(self.structure, "project.structure"))
        object.__setattr__(self, "fallback", _basename(self.fallback, "project.fallback"))

    @classmethod
    def from_dict(cls, value: object) -> WorkflowProject:
        """Construct a project section from a strict manifest mapping."""

        data = _table(value, "project")
        keys = frozenset({"fallback", "root", "series_kind", "structure", "workspace"})
        _check_keys(data, allowed=keys, required=keys, path="project")
        return cls(
            root=_required_string(data["root"], "project.root"),
            series_kind=_required_string(data["series_kind"], "project.series_kind"),
            workspace=_required_string(data["workspace"], "project.workspace"),
            structure=_required_string(data["structure"], "project.structure"),
            fallback=_required_string(data["fallback"], "project.fallback"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-ready project mapping."""

        return {
            "root": self.root,
            "series_kind": self.series_kind,
            "workspace": self.workspace,
            "structure": self.structure,
            "fallback": self.fallback,
        }


@dataclass(frozen=True, slots=True)
class ExecutableSpec:
    """An executable path and optional argument-vector prefix."""

    path: str
    argv_prefix: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _path_string(self.path, "executable.path"))
        if type(self.argv_prefix) is not tuple:
            raise TypeError("executable.argv_prefix must be a sequence")
        prefix = tuple(
            _required_string(token, f"executable.argv_prefix[{index}]")
            for index, token in enumerate(self.argv_prefix)
        )
        object.__setattr__(self, "argv_prefix", prefix)

    @classmethod
    def from_dict(cls, value: object, *, path: str = "executable") -> ExecutableSpec:
        """Construct an executable entry from a strict manifest mapping."""

        data = _table(value, path)
        _check_keys(
            data,
            allowed=frozenset({"argv_prefix", "path"}),
            required=frozenset({"path"}),
            path=path,
        )
        raw_prefix = data.get("argv_prefix", [])
        prefix = tuple(
            _required_string(token, f"{path}.argv_prefix[{index}]")
            for index, token in enumerate(_list(raw_prefix, f"{path}.argv_prefix"))
        )
        return cls(path=_required_string(data["path"], f"{path}.path"), argv_prefix=prefix)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready executable mapping."""

        result: dict[str, Any] = {"path": self.path}
        if self.argv_prefix:
            result["argv_prefix"] = list(self.argv_prefix)
        return result


@dataclass(frozen=True, slots=True)
class SchedulerSpec:
    """Local or Slurm scheduling settings without embedded commands."""

    kind: str
    directives: dict[str, JSONScalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = _required_string(self.kind, "scheduler.kind")
        if kind not in _SCHEDULER_KINDS:
            raise ValueError("scheduler.kind must be one of local, slurm")
        object.__setattr__(self, "kind", kind)
        if type(self.directives) is not dict:
            raise TypeError("scheduler.directives must be a mapping")

        directives: dict[str, JSONScalar] = {}
        for raw_key, raw_value in self.directives.items():
            key = _required_string(raw_key, "scheduler directive key")
            if _DIRECTIVE.fullmatch(key) is None:
                raise ValueError(f"scheduler directive {key!r} contains unsafe characters")
            if key == "job-name":
                raise ValueError(
                    "scheduler directive 'job-name' is reserved; pass the job name "
                    "through workflow submit --job-name"
                )
            if key in _UNSAFE_SLURM_DIRECTIVES:
                raise ValueError(
                    f"scheduler directive {key!r} is not allowed to embed executable content"
                )
            value = _json_copy(raw_value, path=f"scheduler.directives.{key}")
            if isinstance(value, (dict, list)):
                raise TypeError(f"scheduler.directives.{key} must be a scalar")
            if isinstance(value, str) and _DIRECTIVE_VALUE.fullmatch(value) is None:
                raise ValueError(f"scheduler directive {key!r} has unsafe string value {value!r}")
            directives[key] = value
        if kind == "local" and directives:
            raise ValueError("scheduler.directives must be empty when scheduler.kind is 'local'")
        object.__setattr__(self, "directives", directives)

    @classmethod
    def from_dict(cls, value: object) -> SchedulerSpec:
        """Construct a scheduler section from a strict manifest mapping."""

        data = _table(value, "scheduler")
        _check_keys(
            data,
            allowed=frozenset({"directives", "kind"}),
            required=frozenset({"kind"}),
            path="scheduler",
        )
        raw_directives = _table(data.get("directives", {}), "scheduler.directives")
        directives: dict[str, JSONScalar] = {}
        for key, value_item in raw_directives.items():
            copied = _json_copy(value_item, path=f"scheduler.directives.{key}")
            if isinstance(copied, (dict, list)):
                raise TypeError(f"scheduler.directives.{key} must be a scalar")
            directives[key] = copied
        return cls(kind=_required_string(data["kind"], "scheduler.kind"), directives=directives)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready scheduler mapping."""

        validated = SchedulerSpec(kind=self.kind, directives=self.directives)
        return {"kind": validated.kind, "directives": dict(validated.directives)}


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One ordered, dependency-aware external-tool planning step."""

    id: str
    kind: str
    executable: str
    capability: str | None = None
    template_dir: str | None = None
    depends_on: tuple[str, ...] = ()
    options: dict[str, JSONValue] = field(default_factory=dict)
    resources: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "step.id"))
        kind = _required_string(self.kind, f"step {self.id!r}.kind")
        if kind not in _STEP_KINDS:
            choices = ", ".join(sorted(_STEP_KINDS))
            raise ValueError(f"step {self.id!r}.kind must be one of {choices}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "executable",
            _identifier(self.executable, f"step {self.id!r}.executable"),
        )
        if self.capability is not None:
            object.__setattr__(
                self,
                "capability",
                _capability(self.capability, f"step {self.id!r}.capability"),
            )
        if self.template_dir is not None:
            object.__setattr__(
                self,
                "template_dir",
                _path_string(self.template_dir, f"step {self.id!r}.template_dir"),
            )
        if type(self.depends_on) is not tuple:
            raise TypeError(f"step {self.id!r}.depends_on must be a sequence")
        dependencies = tuple(
            _identifier(dependency, f"step {self.id!r}.depends_on[{index}]")
            for index, dependency in enumerate(self.depends_on)
        )
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"step {self.id!r}.depends_on must not contain duplicates")
        object.__setattr__(self, "depends_on", dependencies)
        object.__setattr__(self, "options", _json_object(self.options, f"step {self.id!r}.options"))
        object.__setattr__(
            self,
            "resources",
            _json_object(self.resources, f"step {self.id!r}.resources"),
        )

    @classmethod
    def from_dict(cls, value: object, *, index: int) -> WorkflowStep:
        """Construct a step from one strict manifest sequence entry."""

        path = f"steps[{index}]"
        data = _table(value, path)
        allowed = frozenset(
            {
                "capability",
                "depends_on",
                "executable",
                "id",
                "kind",
                "options",
                "resources",
                "template_dir",
            }
        )
        required = frozenset({"executable", "id", "kind"})
        _check_keys(data, allowed=allowed, required=required, path=path)
        raw_capability = data.get("capability")
        capability = (
            None
            if raw_capability is None
            else _required_string(raw_capability, f"{path}.capability")
        )
        raw_template = data.get("template_dir")
        template_dir = (
            None if raw_template is None else _required_string(raw_template, f"{path}.template_dir")
        )
        dependencies = tuple(
            _required_string(dependency, f"{path}.depends_on[{dependency_index}]")
            for dependency_index, dependency in enumerate(
                _list(data.get("depends_on", []), f"{path}.depends_on")
            )
        )
        return cls(
            id=_required_string(data["id"], f"{path}.id"),
            kind=_required_string(data["kind"], f"{path}.kind"),
            executable=_required_string(data["executable"], f"{path}.executable"),
            capability=capability,
            template_dir=template_dir,
            depends_on=dependencies,
            options=_json_object(data.get("options", {}), f"{path}.options"),
            resources=_json_object(data.get("resources", {}), f"{path}.resources"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-ready step mapping."""

        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "executable": self.executable,
        }
        if self.capability is not None:
            result["capability"] = self.capability
        if self.template_dir is not None:
            result["template_dir"] = self.template_dir
        result["depends_on"] = list(self.depends_on)
        result["options"] = _json_object(self.options, f"step {self.id!r}.options")
        result["resources"] = _json_object(self.resources, f"step {self.id!r}.resources")
        return result


@dataclass(frozen=True, slots=True)
class WorkflowManifest:
    """Complete v1 workflow manifest with validated cross-references."""

    schema_version: str
    project: WorkflowProject
    frames: str
    executables: dict[str, ExecutableSpec]
    scheduler: SchedulerSpec
    steps: tuple[WorkflowStep, ...]

    def __post_init__(self) -> None:
        version = _required_string(self.schema_version, "schema_version")
        if version != WORKFLOW_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {WORKFLOW_SCHEMA_VERSION!r}; got {version!r}")
        object.__setattr__(self, "schema_version", version)
        if not isinstance(self.project, WorkflowProject):
            raise TypeError("project must be a WorkflowProject")
        frames = _required_string(self.frames, "frames")
        # Imported lazily so selection can depend on the discovery model without
        # introducing a module import cycle.
        from .selection import validate_frame_selection

        validate_frame_selection(frames)
        object.__setattr__(self, "frames", frames)

        if type(self.executables) is not dict:
            raise TypeError("executables must be a mapping")
        if not self.executables:
            raise ValueError("executables must contain at least one entry")
        executables: dict[str, ExecutableSpec] = {}
        for raw_name, spec in self.executables.items():
            name = _identifier(raw_name, "executable name")
            if not isinstance(spec, ExecutableSpec):
                raise TypeError(f"executables.{name} must be an ExecutableSpec")
            executables[name] = spec
        object.__setattr__(self, "executables", executables)

        if not isinstance(self.scheduler, SchedulerSpec):
            raise TypeError("scheduler must be a SchedulerSpec")
        if type(self.steps) is not tuple:
            raise TypeError("steps must be a sequence")
        if not self.steps:
            raise ValueError("steps must contain at least one entry")
        if any(not isinstance(step, WorkflowStep) for step in self.steps):
            raise TypeError("every steps entry must be a WorkflowStep")

        identifiers = [step.id for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            duplicates = sorted(
                identifier for identifier, count in Counter(identifiers).items() if count > 1
            )
            raise ValueError(f"steps contain duplicate ids: {', '.join(duplicates)}")
        step_by_id = {step.id: step for step in self.steps}
        for step in self.steps:
            if step.executable not in executables:
                raise ValueError(
                    f"step {step.id!r} references unknown executable {step.executable!r}"
                )
            unknown = sorted(set(step.depends_on) - set(step_by_id))
            if unknown:
                raise ValueError(
                    f"step {step.id!r} references unknown dependencies: {', '.join(unknown)}"
                )

        self._reject_dependency_cycles(step_by_id)
        positions = {step.id: index for index, step in enumerate(self.steps)}
        for step in self.steps:
            forward = [
                dependency
                for dependency in step.depends_on
                if positions[dependency] >= positions[step.id]
            ]
            if forward:
                raise ValueError(
                    f"step {step.id!r} has forward dependencies: {', '.join(forward)}; "
                    "dependencies must name earlier steps"
                )

    @staticmethod
    def _reject_dependency_cycles(step_by_id: dict[str, WorkflowStep]) -> None:
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(identifier: str) -> None:
            state[identifier] = 1
            stack.append(identifier)
            for dependency in step_by_id[identifier].depends_on:
                dependency_state = state.get(dependency, 0)
                if dependency_state == 1:
                    start = stack.index(dependency)
                    cycle = [*stack[start:], dependency]
                    raise ValueError(f"workflow dependency cycle: {' -> '.join(cycle)}")
                if dependency_state == 0:
                    visit(dependency)
            stack.pop()
            state[identifier] = 2

        for identifier in step_by_id:
            if state.get(identifier, 0) == 0:
                visit(identifier)

    @classmethod
    def from_dict(cls, value: object) -> WorkflowManifest:
        """Construct and validate a manifest from parsed YAML data."""

        copied = _json_copy(value, path="workflow manifest")
        data = _table(copied, "workflow manifest")
        keys = frozenset(
            {"executables", "frames", "project", "scheduler", "schema_version", "steps"}
        )
        _check_keys(data, allowed=keys, required=keys, path="workflow manifest")

        executable_data = _table(data["executables"], "executables")
        executables = {
            _identifier(name, "executable name"): ExecutableSpec.from_dict(
                spec, path=f"executables.{name}"
            )
            for name, spec in executable_data.items()
        }
        raw_steps = _list(data["steps"], "steps")
        return cls(
            schema_version=_required_string(data["schema_version"], "schema_version"),
            project=WorkflowProject.from_dict(data["project"]),
            frames=_required_string(data["frames"], "frames"),
            executables=executables,
            scheduler=SchedulerSpec.from_dict(data["scheduler"]),
            steps=tuple(
                WorkflowStep.from_dict(step, index=index) for index, step in enumerate(raw_steps)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic mapping accepted by strict JSON encoders."""

        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project": self.project.to_dict(),
            "frames": self.frames,
            "executables": {
                name: executable.to_dict() for name, executable in self.executables.items()
            },
            "scheduler": self.scheduler.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
        }
        # Defend the public contract if a caller mutated one of the nested
        # extension mappings after construction.
        copied = _json_copy(result, path="workflow manifest")
        return cast(dict[str, Any], copied)


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
