from __future__ import annotations

import pytest

from electrace.models import Atom, Feature, Frame


def test_fixed_shape_validation_consumes_only_one_excess_item() -> None:
    coordinates = iter(range(100))
    with pytest.raises(ValueError, match="exactly three"):
        Atom(0, "H", coordinates)
    assert next(coordinates) == 4

    rows = iter([(1.0, 0.0, 0.0)] * 100)
    with pytest.raises(ValueError, match="exactly three row vectors"):
        Frame(0, "0", rows)
    assert next(rows) == (1.0, 0.0, 0.0)


def test_voxel_indices_are_deduplicated_and_sorted() -> None:
    feature = Feature(
        "basin",
        "elf_basin",
        (0.0, 0.0, 0.0),
        voxel_indices=((2, 1, 0), (0, 1, 2), (2, 1, 0)),
    )

    assert feature.voxel_indices == ((0, 1, 2), (2, 1, 0))
