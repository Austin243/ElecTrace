"""Canonical frame-index selection for discovered ElecTrace paths."""

from __future__ import annotations

import re
from collections.abc import Sequence

from electrace.io.discovery import FrameSource

_INTEGER = re.compile(r"^[+-]?\d+$")


class FrameSelectionError(ValueError):
    """Raised when a workflow frame selector is malformed or cannot resolve."""


def _selection_tokens(selection: str) -> tuple[int | slice, ...]:
    if type(selection) is not str:
        raise TypeError("frame selection must be a string")
    if not selection or any(
        ord(character) < 32 or ord(character) == 127 for character in selection
    ):
        raise FrameSelectionError(
            "frame selection must be non-empty and contain no control characters"
        )

    stripped = selection.strip()
    if not stripped:
        raise FrameSelectionError("frame selection must not be blank")
    if stripped == "all":
        return (slice(None, None, None),)
    if "all" in {part.strip() for part in stripped.split(",")}:
        raise FrameSelectionError("'all' cannot be combined with other frame selectors")

    selectors: list[int | slice] = []
    for token_index, raw_token in enumerate(stripped.split(",")):
        token = raw_token.strip()
        if not token:
            raise FrameSelectionError(f"frame selector token {token_index} is empty")
        if ":" not in token:
            if _INTEGER.fullmatch(token) is None:
                raise FrameSelectionError(f"invalid canonical frame index {token!r}")
            index = int(token)
            if index < 0:
                raise FrameSelectionError(
                    f"explicit canonical frame index must be non-negative; got {index}"
                )
            selectors.append(index)
            continue

        components = token.split(":")
        if len(components) not in {2, 3}:
            raise FrameSelectionError(
                f"frame slice {token!r} must use start:stop or start:stop:step"
            )
        parsed: list[int | None] = []
        for raw_component in components:
            component = raw_component.strip()
            if not component:
                parsed.append(None)
                continue
            if _INTEGER.fullmatch(component) is None:
                raise FrameSelectionError(
                    f"frame slice {token!r} has invalid component {component!r}"
                )
            parsed.append(int(component))
        start, stop = parsed[0], parsed[1]
        step = parsed[2] if len(parsed) == 3 else None
        if step == 0:
            raise FrameSelectionError(f"frame slice {token!r} has a zero step")
        selectors.append(slice(start, stop, step))
    return tuple(selectors)


def validate_frame_selection(selection: str) -> None:
    """Validate selector syntax without requiring a discovered frame count."""

    _selection_tokens(selection)


def parse_frame_selection(selection: str, frame_count: int) -> tuple[int, ...]:
    """Resolve a selector to canonical indices for a sequence of known length.

    Individual indices are zero-based canonical frame indices.  Slice entries
    follow Python's clipping and negative-index rules, including negative steps.
    Comma-separated entries are evaluated left to right.  Duplicate selections
    are rejected because running a frame twice is almost always a workflow error.
    """

    if type(frame_count) is not int:
        raise TypeError("frame_count must be an integer")
    if frame_count < 0:
        raise ValueError("frame_count must be non-negative")

    resolved: list[int] = []
    seen: set[int] = set()
    for selector in _selection_tokens(selection):
        indices: Sequence[int]
        if isinstance(selector, int):
            if selector >= frame_count:
                raise FrameSelectionError(
                    f"canonical frame index {selector} is outside 0:{frame_count}"
                )
            indices = (selector,)
        else:
            indices = range(*selector.indices(frame_count))
        for index in indices:
            if index in seen:
                raise FrameSelectionError(
                    f"canonical frame index {index} is selected more than once"
                )
            seen.add(index)
            resolved.append(index)

    if not resolved:
        raise FrameSelectionError("frame selection resolved to no frames")
    return tuple(resolved)


def resolve_frame_selection(
    selection: str, frames: Sequence[FrameSource]
) -> tuple[FrameSource, ...]:
    """Return selected discovered records in selector evaluation order.

    Discovery assigns canonical indices as consecutive zero-based positions.
    Requiring that invariant here prevents a slice position from silently being
    mistaken for a source-directory number or a caller-provided sparse index.
    """

    records = tuple(frames)
    if any(not isinstance(frame, FrameSource) for frame in records):
        raise TypeError("frames must contain only FrameSource records")
    canonical_indices = tuple(frame.index for frame in records)
    expected = tuple(range(len(records)))
    if canonical_indices != expected:
        raise FrameSelectionError(
            "FrameSource records must be in canonical order with consecutive zero-based indices"
        )
    indices = parse_frame_selection(selection, len(records))
    return tuple(records[index] for index in indices)


def resolve_selected_frames(
    frames: Sequence[FrameSource], selection: str
) -> tuple[FrameSource, ...]:
    """Sequence-first convenience wrapper around :func:`resolve_frame_selection`."""

    return resolve_frame_selection(selection, frames)


__all__ = [
    "FrameSelectionError",
    "parse_frame_selection",
    "resolve_frame_selection",
    "resolve_selected_frames",
    "validate_frame_selection",
]
