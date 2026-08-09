from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from electrace.volume import VaspGridError, read_vasp_grid


def _write_grid(
    path: Path,
    dimensions: str,
    values: str,
    *,
    cell: tuple[str, str, str] = ("1 0 0", "0 1 0", "0 0 1"),
    species: str = "H",
    counts: str = "1",
    mode: str = "Direct",
    positions: tuple[str, ...] = ("0 0 0",),
) -> Path:
    document = ["grid", "1", *cell, species, counts, mode, *positions, "", dimensions, values]
    path.write_text("\n".join(document), encoding="utf-8")
    return path


def test_non_cubic_grid_uses_vasp_x_fastest_order(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "ELFCAR",
        "2 3 2",
        "0 1 2 3 4 5 6 7 8 9 10 11",
        cell=("2 0 0", "0.5 3 0", "0 0.25 4"),
        species="C H",
        counts="1 1",
        positions=("0 0 0", "0.5 0.5 0.5"),
    )

    grid = read_vasp_grid(source)

    assert grid.shape == (2, 3, 2)
    assert grid.metadata.cell == (
        (2.0, 0.0, 0.0),
        (0.5, 3.0, 0.0),
        (0.0, 0.25, 4.0),
    )
    assert grid.data[1, 0, 0] == pytest.approx(1.0)
    assert grid.data[0, 1, 0] == pytest.approx(2.0)
    assert grid.data[1, 2, 1] == pytest.approx(11.0)


def test_periodic_trilinear_sampling(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "grid",
        "2 2 2",
        "0 1 10 11 100 101 110 111",
    )

    grid = read_vasp_grid(source)

    assert grid.sample((0.25, 0.25, 0.25)) == pytest.approx(55.5)
    assert grid.sample((1.0, 0.0, 0.0)) == pytest.approx(0.0)


def test_cartesian_positions_follow_row_vector_convention(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "triclinic-grid",
        "1 1 1",
        "0.5",
        cell=("2 0 0", "0.5 3 0", "0 0.25 4"),
        species="C",
        mode="Cartesian",
        positions=("0.55 1 1.6",),
    )

    grid = read_vasp_grid(source)

    np.testing.assert_allclose(grid.metadata.fractional_positions[0], (0.2, 0.3, 0.4))


def test_malformed_grid_is_rejected(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "bad-grid",
        "2 2 1",
        "1 2 3",
    )

    with pytest.raises(VaspGridError, match="missing values"):
        read_vasp_grid(source)
