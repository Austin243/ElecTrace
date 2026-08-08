"""Safe YAML loading and starter generation for ElecTrace workflows."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ._files import read_limited_text
from .models import (
    WORKFLOW_SCHEMA_VERSION,
    ExecutableSpec,
    SchedulerSpec,
    WorkflowManifest,
    WorkflowProject,
    WorkflowStep,
)

MAX_WORKFLOW_MANIFEST_BYTES = 1_000_000


class WorkflowManifestError(ValueError):
    """Raised when a workflow YAML document cannot be loaded safely."""


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader variant that refuses PyYAML's last-key-wins behavior."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            raise WorkflowManifestError("workflow YAML aliases are not allowed")
        return super().compose_node(parent, index)


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: Any, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise WorkflowManifestError("workflow YAML contains a non-hashable mapping key")
        if key in mapping:
            raise WorkflowManifestError(f"workflow YAML contains duplicate mapping key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_workflow_manifest(path: str | Path) -> WorkflowManifest:
    """Load a strict ``electrace.workflow.v1`` YAML manifest.

    Paths remain raw manifest values.  Resolution belongs to the orchestration
    boundary, which has both the manifest location and an explicit workspace
    policy; loading therefore has no current-working-directory side effects.
    """

    source = Path(path)
    try:
        text = read_limited_text(
            source,
            limit=MAX_WORKFLOW_MANIFEST_BYTES,
            context="workflow manifest",
        )
    except ValueError as exc:
        raise WorkflowManifestError(str(exc)) from exc
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except (RecursionError, yaml.YAMLError) as exc:
        raise WorkflowManifestError(f"invalid workflow YAML in {source}: {exc}") from exc
    return WorkflowManifest.from_dict(data)


def starter_workflow_manifest(*, root: str = ".") -> WorkflowManifest:
    """Return a local VASP-to-BadELF starter that is executable after review."""

    return WorkflowManifest(
        schema_version=WORKFLOW_SCHEMA_VERSION,
        project=WorkflowProject(
            root=root,
            series_kind="auto",
            workspace=".electrace-work",
            structure="CONTCAR",
            fallback="POSCAR",
        ),
        frames="all",
        executables={
            "vasp": ExecutableSpec(path="vasp_std"),
            "baderkit": ExecutableSpec(path="baderkit"),
        },
        scheduler=SchedulerSpec(kind="local"),
        steps=(
            WorkflowStep(
                id="vasp_elf",
                kind="vasp",
                executable="vasp",
                template_dir="templates/vasp-elf",
                options={
                    "required_inputs": ["INCAR", "KPOINTS", "POSCAR", "POTCAR"],
                    "expected_outputs": ["CHGCAR", "ELFCAR", "CONTCAR"],
                    "incar_overrides": {"LELF": True, "LCHARG": True, "NSW": 0},
                },
            ),
            WorkflowStep(
                id="elf_basins",
                kind="baderkit",
                executable="baderkit",
                capability="elf_basins",
                depends_on=("vasp_elf",),
            ),
        ),
    )


def dump_starter_manifest(path: str | Path, *, root: str = ".", overwrite: bool = False) -> Path:
    """Write a readable starter manifest and return its path.

    Existing files are preserved unless ``overwrite=True`` is explicit.
    Parent directories are created because the requested path itself is the
    authorization boundary for this local scaffolding operation.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = starter_workflow_manifest(root=root)
    document = yaml.safe_dump(
        manifest.to_dict(),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    if not overwrite:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(document)
        return target
    if target.is_symlink():
        raise ValueError(f"refusing to replace symlinked workflow manifest: {target}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(document)
        # Recheck for a symlink to give a clear diagnostic.  os.replace itself
        # replaces a raced leaf symlink rather than following its target.
        if target.is_symlink():
            raise ValueError(f"refusing to replace symlinked workflow manifest: {target}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


__all__ = [
    "MAX_WORKFLOW_MANIFEST_BYTES",
    "WorkflowManifestError",
    "dump_starter_manifest",
    "load_workflow_manifest",
    "starter_workflow_manifest",
]
