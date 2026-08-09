from __future__ import annotations

from electrace.models import Feature


def test_voxel_indices_are_deduplicated_and_sorted() -> None:
    feature = Feature(
        "basin",
        "elf_basin",
        (0.0, 0.0, 0.0),
        voxel_indices=((2, 1, 0), (0, 1, 2), (2, 1, 0)),
    )

    assert feature.voxel_indices == ((0, 1, 2), (2, 1, 0))
