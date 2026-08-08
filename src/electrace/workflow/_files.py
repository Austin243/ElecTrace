"""Small, shared filesystem primitives for the workflow trust boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path

from electrace._files import atomic_write_text as atomic_write_text


def absolute_lexical(path: Path) -> Path:
    """Return an absolute, normalized path without resolving symlinks."""

    return Path(os.path.abspath(path.expanduser()))


def symlink_component(path: Path) -> Path | None:
    """Return the first symlink in *path*, including the leaf, if any."""

    return next((component for component in (path, *path.parents) if component.is_symlink()), None)


def read_limited_text(path: Path, *, limit: int, context: str) -> str:
    """Read UTF-8 text while enforcing a byte limit during the read."""

    if limit < 1:
        raise ValueError("text read limit must be positive")
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"{context} exceeds {limit} bytes: {path}")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{context} is not valid UTF-8: {path}") from exc


def atomic_write_json(path: Path, payload: object) -> Path:
    """Atomically publish deterministic, standards-compliant JSON."""

    document = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return atomic_write_text(path, document, mode=0o600)
