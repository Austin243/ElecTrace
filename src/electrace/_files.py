"""Shared internal filesystem primitives for atomic result publication."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

# Marks a collected workflow analysis, whose input/ holds rewritten energies.
COLLECTION_RECORD = ".electrace-collection.json"


@contextmanager
def atomic_output_path(output: Path) -> Iterator[Path]:
    """Yield a same-directory temporary path, then atomically replace *output*."""

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    temporary.touch(exist_ok=False)
    try:
        yield temporary
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(output: Path, payload: str, *, mode: int | None = None) -> Path:
    """Write UTF-8 text without following or partially replacing *output*."""

    with atomic_output_path(output) as temporary:
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        if mode is not None:
            temporary.chmod(mode)
    return output


def atomic_write_json(path: Path, payload: object) -> Path:
    """Atomically publish deterministic, standards-compliant JSON."""

    document = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return atomic_write_text(path, document, mode=0o600)


def publish_directory(temporary: Path, target: Path) -> None:
    """Move *temporary* to *target*, restoring any previous *target* if the move fails."""

    if not target.exists():
        temporary.replace(target)
        return
    backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.previous-", dir=target.parent))
    backup.rmdir()
    target.replace(backup)
    try:
        temporary.replace(target)
    except BaseException:
        backup.replace(target)
        raise
    shutil.rmtree(backup)
