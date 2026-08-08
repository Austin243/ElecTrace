from __future__ import annotations

import textwrap
from pathlib import Path

import numpy as np
import pytest

from electrace.volume import VaspGridError, read_vasp_grid


def _write_grid(path: Path, document: str) -> Path:
    path.write_text(textwrap.dedent(document).lstrip(), encoding="utf-8")
    return path


def test_non_cubic_x_fastest_order_consecutive_dataset_and_summary(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "ELFCAR",
        """
        non-cubic two-channel grid
        1.0
        2.0 0.0 0.0
        0.5 3.0 0.0
        0.0 0.25 4.0
        C H
        1 1
        Direct
        0.0 0.0 0.0
        0.5 0.5 0.5

        2 3 2
        0 1 2 3 4
        5 6 7 8 9
        10 11

        2 3 2
        100 101 102 103
        104 105 106 107
        108 109 110 111
        """,
    )

    first = read_vasp_grid(source)
    second = read_vasp_grid(source, dataset=1)

    assert first.shape == (2, 3, 2)
    assert first.metadata.cell == (
        (2.0, 0.0, 0.0),
        (0.5, 3.0, 0.0),
        (0.0, 0.25, 4.0),
    )
    assert first.metadata.elements == ("C", "H")
    assert first.metadata.dataset_count == 2
    # VASP writes x fastest, then y, then z.
    assert first.data[0, 0, 0] == pytest.approx(0.0)
    assert first.data[1, 0, 0] == pytest.approx(1.0)
    assert first.data[0, 1, 0] == pytest.approx(2.0)
    assert first.data[1, 2, 1] == pytest.approx(11.0)
    assert second.data[0, 0, 0] == pytest.approx(100.0)
    assert second.data[1, 2, 1] == pytest.approx(111.0)
    assert second.data.flags.writeable is False
    assert second.summary() == {
        "source": str(source.resolve()),
        "dataset_index": 1,
        "dataset_count": 2,
        "shape": [2, 3, 2],
        "point_count": 12,
        "cell_volume": pytest.approx(24.0),
        "minimum": 100.0,
        "maximum": 111.0,
        "mean": 105.5,
    }


def test_periodic_trilinear_sampling_and_vasp4_blank_lines(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "vasp4-grid",
        """
        VASP 4 periodic interpolation

        1
        1 0 0
        0 1 0
        0 0 1

        1
        Direct

        0 0 0

        2 2 2
        0 1 10 11
        100 101 110 111
        """,
    )

    grid = read_vasp_grid(source)

    assert grid.metadata.symbols_inferred is True
    assert grid.metadata.species == ("X1",)
    assert grid.sample((0.0, 0.0, 0.0)) == pytest.approx(0.0)
    assert grid.sample((0.5, 0.5, 0.5)) == pytest.approx(111.0)
    assert grid.sample((0.25, 0.25, 0.25)) == pytest.approx(55.5)
    assert grid.sample((1.0, 0.0, 0.0)) == pytest.approx(0.0)
    assert grid.sample((-0.5, 0.0, 0.0)) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="three finite numbers"):
        grid.sample((float("nan"), 0.0, 0.0))


def test_selective_cartesian_coordinates_and_positive_scale(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "CHGCAR",
        """
        scaled Cartesian structure
        2.0
        1 0 0
        0 2 0
        0 0 3
        Si O
        1 1
        Selective dynamics
        Cartesian
        0.5 1.0 1.5 T F T
        0.0 0.0 0.0 F F F

        1 1 1
        1.25D+00
        """,
    )

    grid = read_vasp_grid(source)

    assert grid.metadata.coordinate_mode == "cartesian"
    assert grid.metadata.cell == ((2.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 6.0))
    np.testing.assert_allclose(
        grid.metadata.fractional_positions,
        ((0.5, 0.5, 0.5), (0.0, 0.0, 0.0)),
    )
    assert grid.metadata.selective_dynamics == ((True, False, True), (False, False, False))
    assert grid.data[0, 0, 0] == pytest.approx(1.25)


def test_cartesian_positions_use_row_vector_cell_convention(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "triclinic-grid",
        """
        row-vector Cartesian conversion
        1
        2.0 0.0 0.0
        0.5 3.0 0.0
        0.0 0.25 4.0
        C
        1
        Cartesian
        0.55 1.0 1.6

        1 1 1
        0.5
        """,
    )

    grid = read_vasp_grid(source)

    # (0.2, 0.3, 0.4) @ cell = (0.55, 1.0, 1.6).
    np.testing.assert_allclose(grid.metadata.fractional_positions[0], (0.2, 0.3, 0.4))


@pytest.mark.parametrize(
    ("scale", "expected_cell"),
    [
        ("-8", ((2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 2.0))),
        ("2 3 4", ((2.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 4.0))),
    ],
)
def test_negative_volume_and_component_scaling(
    tmp_path: Path,
    scale: str,
    expected_cell: tuple[tuple[float, float, float], ...],
) -> None:
    source = _write_grid(
        tmp_path / "scaled-grid",
        f"""
        scale variants
        {scale}
        1 0 0
        0 1 0
        0 0 1
        H
        1
        Direct
        0.25 0.5 0.75

        1 1 1
        2.0
        """,
    )

    grid = read_vasp_grid(source)

    assert grid.metadata.cell == expected_cell
    assert grid.metadata.fractional_positions == ((0.25, 0.5, 0.75),)


def _minimal_document(data_and_tail: str, *, shape: str = "2 2 1") -> str:
    return f"""\
minimal malformed grid
1
1 0 0
0 1 0
0 0 1
H
1
Direct
0 0 0

{shape}
{data_and_tail}
"""


@pytest.mark.parametrize(
    ("data_and_tail", "match"),
    [
        ("1 2 3", "missing values for dataset 0"),
        ("1 2 nan 4", "non-finite number"),
        ("1 2 3 4\naugmentation occupancies 1 1", "augmentation blocks are not supported"),
        ("1 2 3 4\nunknown tail", "unsupported content follows"),
        ("1 2 3 4\n1 1 1\n5", "shape .* differs"),
        ("1 2 3 4 5", "extra values"),
    ],
)
def test_rejects_truncated_nonfinite_augmentation_and_ambiguous_content(
    tmp_path: Path,
    data_and_tail: str,
    match: str,
) -> None:
    source = _write_grid(tmp_path / "bad-grid", _minimal_document(data_and_tail))

    with pytest.raises(VaspGridError, match=match):
        read_vasp_grid(source)


def test_max_points_and_dataset_bounds_are_enforced(tmp_path: Path) -> None:
    oversized = _write_grid(
        tmp_path / "oversized",
        _minimal_document("", shape="11 10 10"),
    )
    valid = _write_grid(
        tmp_path / "single",
        _minimal_document("1 2 3 4"),
    )

    with pytest.raises(VaspGridError, match="1100 points, exceeding max_points=1099"):
        read_vasp_grid(oversized, max_points=1099)
    with pytest.raises(VaspGridError, match=r"dataset 1 is unavailable.*1 dataset"):
        read_vasp_grid(valid, dataset=1)
    with pytest.raises(ValueError, match="max_points must be a positive integer"):
        read_vasp_grid(valid, max_points=0)


def test_data_order_matches_explicit_numpy_reference(tmp_path: Path) -> None:
    source = _write_grid(
        tmp_path / "order",
        _minimal_document("0 1 2 3 4 5", shape="3 1 2"),
    )

    grid = read_vasp_grid(source)
    expected = np.array([0, 1, 2, 3, 4, 5], dtype=float).reshape((3, 1, 2), order="F")

    np.testing.assert_array_equal(grid.data, expected)


def test_reader_streams_instead_of_materializing_the_text_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_grid(
        tmp_path / "streamed-grid",
        _minimal_document("1 2 3 4"),
    )

    def reject_read_text(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("read_vasp_grid must not materialize the full text file")

    monkeypatch.setattr(Path, "read_text", reject_read_text)

    grid = read_vasp_grid(source)

    np.testing.assert_array_equal(grid.data.ravel(order="F"), (1.0, 2.0, 3.0, 4.0))
