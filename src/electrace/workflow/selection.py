"""Frame selection for discovered workflow paths."""

from __future__ import annotations


class FrameSelectionError(ValueError):
    """A frame selector is malformed or resolves to no frames."""


def frame_selectors(selection: str) -> tuple[int | slice, ...]:
    if selection == "all":
        return (slice(None),)
    if "all" in {part.strip() for part in selection.split(",")}:
        raise FrameSelectionError("'all' cannot be combined with other selectors")

    result: list[int | slice] = []
    for token in (part.strip() for part in selection.split(",")):
        if not token:
            raise FrameSelectionError("frame selection contains an empty token")
        if ":" not in token:
            try:
                index = int(token)
            except ValueError as exc:
                raise FrameSelectionError(f"invalid frame index: {token!r}") from exc
            if index < 0:
                raise FrameSelectionError("explicit frame indices must be non-negative")
            result.append(index)
            continue
        parts = token.split(":")
        if len(parts) not in {2, 3}:
            raise FrameSelectionError(f"invalid frame slice: {token!r}")
        try:
            values = [int(part) if part.strip() else None for part in parts]
        except ValueError as exc:
            raise FrameSelectionError(f"invalid frame slice: {token!r}") from exc
        if len(values) == 2:
            values.append(None)
        if values[2] == 0:
            raise FrameSelectionError("frame slice step must not be zero")
        result.append(slice(*values))
    return tuple(result)


def parse_frame_selection(selection: str, frame_count: int) -> tuple[int, ...]:
    resolved: list[int] = []
    for selector in frame_selectors(selection):
        if isinstance(selector, int):
            if selector >= frame_count:
                raise FrameSelectionError(f"frame index {selector} is outside 0:{frame_count}")
            resolved.append(selector)
        else:
            resolved.extend(range(*selector.indices(frame_count)))
    if not resolved:
        raise FrameSelectionError("frame selection resolved to no frames")
    if len(resolved) != len(set(resolved)):
        raise FrameSelectionError("frame selection contains duplicates")
    return tuple(resolved)
