"""Load and create ElecTrace workflow YAML manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from electrace._files import atomic_write_text

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
    """A workflow YAML document could not be loaded."""


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    def construct_mapping(self, node: Any, deep: bool = False) -> dict[object, object]:
        self.flatten_mapping(node)
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise WorkflowManifestError(f"duplicate workflow YAML key: {key!r}")
            seen.add(key)
        return cast(dict[object, object], super().construct_mapping(node, deep=deep))


def load_workflow_manifest(path: str | Path) -> WorkflowManifest:
    source = Path(path)
    try:
        if source.stat().st_size > MAX_WORKFLOW_MANIFEST_BYTES:
            raise WorkflowManifestError("workflow manifest exceeds 1 MB")
        data = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, RecursionError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkflowManifestError(f"invalid workflow YAML in {source}: {exc}") from exc
    return WorkflowManifest.from_dict(data)


def starter_workflow_manifest(*, root: str = ".") -> WorkflowManifest:
    """Return a local VASP-to-BadELF starter manifest."""

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
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"refusing to replace symlinked workflow manifest: {target}")
    if target.exists() and not overwrite:
        raise FileExistsError(f"workflow manifest already exists: {target}")
    document = yaml.safe_dump(
        starter_workflow_manifest(root=root).to_dict(), sort_keys=False, allow_unicode=True
    )
    return atomic_write_text(target, document)


__all__ = [
    "MAX_WORKFLOW_MANIFEST_BYTES",
    "WorkflowManifestError",
    "dump_starter_manifest",
    "load_workflow_manifest",
    "starter_workflow_manifest",
]
