"""Shared internal filesystem primitives for atomic result publication."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def atomic_output_path(output: Path) -> Iterator[Path]:
    """Yield a same-directory temporary path, then atomically replace *output*."""

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    for _ in range(10):
        candidate = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o666,
            )
        except FileExistsError:  # pragma: no cover - UUID collision defense
            continue
        os.close(descriptor)
        temporary = candidate
        break
    if temporary is None:  # pragma: no cover - UUID collision defense
        raise OSError(f"could not allocate a temporary output beside {output}")
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
