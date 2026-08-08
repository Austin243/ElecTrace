"""Reproducibility and source-integrity helpers."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return a streaming SHA-256 digest for *path*."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_files(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    """Create stable path/size/hash records for existing regular files."""

    records: list[dict[str, Any]] = []
    for item in sorted((Path(path).resolve() for path in paths), key=str):
        if not item.is_file():
            continue
        stat = item.stat()
        records.append(
            {
                "path": str(item),
                "size_bytes": stat.st_size,
                "sha256": sha256_file(item),
            }
        )
    return records


def _version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _git_revision(directory: Path) -> str | None:
    """Read the checkout revision without failing outside a Git repository."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=directory,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    return revision or None


def environment_record(*, project_directory: str | Path | None = None) -> dict[str, Any]:
    """Return a compact execution-environment audit record."""

    root = Path(project_directory or Path.cwd()).resolve()
    packages = {
        name: value
        for name in ("electrace", "numpy", "scipy", "ase", "h5py", "typer", "rich")
        if (value := _version(name)) is not None
    }
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "packages": packages,
        "git_revision": _git_revision(root),
    }
