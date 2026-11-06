"""critic2 input for the all-electron density with explicit periodic seeds.

critic2 1.2 and its current master build ``SEED PAIR`` and ``SEED TRIPLET`` seeds from
the atoms' in-cell positions without periodic images (``autocritic`` in
``src/autocp@proc.f90``), so a bond that crosses a cell face gets no seeds. Its default
seeding of a crystal subdivides the Wigner-Seitz cell instead. In 288-atom acetonitrile
cells, the in-cell pair seeds missed covalent C-H and N-H bond critical points across
cell faces, and the default seeding left Morse sums near 94.

This input seeds every atom pair within a distance, counting periodic images, with one
``SEED LINE`` holding the interior points ``SEED PAIR`` would use, and, optionally, every
atom triplet whose three distances are within that distance with a ``SEED POINT`` at its
barycenter. The density is AECCAR0 + AECCAR2 with ``SMOOTHRHO``, as in ElecTrace's
HCN example, and points where it is below 1e-5 a.u. are discarded as vacuum artifacts.
"""

from __future__ import annotations

import math
import textwrap
from itertools import product
from pathlib import Path

import numpy as np
import numpy.typing as npt

from electrace.geometry import as_cell
from electrace.io.vasp import element_symbol, parse_poscar

FloatArray = npt.NDArray[np.float64]

BOHR_ANGSTROM = 0.52917720859  # critic2's bohrtoa
DEFAULT_SEED_DISTANCE = 6.0  # bohr, critic2's length unit
DEFAULT_SEED_POINTS = 20
DEFAULT_SEED_TRIPLETS = True
PERIODIC_INPUT_NAME = "critic2.in"
ALL_ELECTRON_FILES = ("AECCAR0", "AECCAR2")


def seed_settings(distance: object, points: object, triplets: object) -> tuple[float, int, bool]:
    """Fill unset seed options with the defaults and validate the result."""

    if distance is None:
        distance = DEFAULT_SEED_DISTANCE
    if points is None:
        points = DEFAULT_SEED_POINTS
    if triplets is None:
        triplets = DEFAULT_SEED_TRIPLETS
    if (
        isinstance(distance, bool)
        or not isinstance(distance, (int, float))
        or not math.isfinite(distance)
        or distance <= 0
    ):
        raise ValueError("seed_distance must be a positive number of bohr")
    if isinstance(points, bool) or not isinstance(points, int) or points < 1:
        raise ValueError("seed_points must be a positive integer")
    if not isinstance(triplets, bool):
        raise TypeError("seed_triplets must be true or false")
    return float(distance), points, triplets


def _periodic_neighbors(
    cell: FloatArray, fractional: FloatArray, distance: float
) -> list[tuple[int, int, FloatArray]]:
    """Return (i, j, d) for every atom j >= i and image within *distance* of atom i.

    *d* is the fractional displacement from atom i to that image of atom j, with both
    images of a self-image pair (i == j). Any lattice vector ``f @ cell`` is at least
    ``|f_k| * h_k`` long, where ``h_k`` is the spacing of the lattice planes spanned by
    the other two cell vectors. After component rounding ``|d_k| <= 1/2``, so an image
    within *distance* has an integer shift of at most ``1/2 + distance / h_k`` on axis
    ``k``. Enumerating those shifts is exact for skewed cells and finds every image when
    a cell is smaller than twice the distance.
    """

    spacing = 1.0 / np.linalg.norm(np.linalg.inv(cell), axis=0)
    reach = [math.ceil(0.5 + distance / value) for value in spacing]
    shifts = np.array(list(product(*(range(-item, item + 1) for item in reach))), dtype=float)
    result: list[tuple[int, int, FloatArray]] = []
    for i in range(len(fractional)):
        delta = fractional[i:] - fractional[i]
        delta -= np.rint(delta)
        images = delta[:, None, :] + shifts[None, :, :]
        within = np.linalg.norm(images @ cell, axis=2) <= distance
        within[0] &= np.any(shifts != 0.0, axis=1)
        offsets, columns = np.nonzero(within)
        # Fancy indexing copies the kept rows, so `images` is freed for the next atom.
        for offset, displacement in zip(offsets, images[offsets, columns], strict=True):
            result.append((i, i + int(offset), displacement))
    return result


def periodic_seeds(
    cell: FloatArray, fractional: FloatArray, distance: float, points: int, triplets: bool
) -> tuple[list[str], int, int]:
    """Return critic2 seed lines and their pair and triplet counts for a cell (rows in bohr).

    Each pair within *distance* gets ``points`` evenly spaced interior points of the
    segment to its image, ``x_i + k / (points + 1) * d`` for k = 1..points, written as one
    ``SEED LINE`` from the first to the last point (critic2 includes both ends), or as a
    ``SEED POINT`` at the midpoint when ``points`` is 1, because ``SEED LINE`` with one
    point divides by zero. With *triplets*, each set of three atom images whose three
    distances are within *distance* gets a ``SEED POINT`` at its barycenter, once up to
    lattice translation. Positions are wrapped into [0, 1) first.
    """

    lattice = as_cell(cell)
    wrapped = fractional - np.floor(fractional)
    neighbors = _periodic_neighbors(lattice, wrapped, distance)
    pairs = [(i, j, d) for i, j, d in neighbors if j > i or _first_positive(d)]
    if not pairs:
        raise ValueError(
            f"no atom pair lies within seed_distance = {distance:g} bohr, so there is "
            "nothing to seed; critic2 would fall back to its default seeding"
        )
    lines: list[str] = []
    for i, _, d in pairs:
        if points == 1:
            lines.append(f"  SEED POINT X0 {_point(wrapped[i] + d / 2)}")
            continue
        start = wrapped[i] + d / (points + 1)
        end = wrapped[i] + d * points / (points + 1)
        lines.append(f"  SEED LINE X0 {_point(start)} X1 {_point(end)} NPTS {points}")
    barycenters = _triplet_barycenters(lattice, wrapped, neighbors, distance) if triplets else []
    lines.extend(f"  SEED POINT X0 {_point(point)}" for point in barycenters)
    return lines, len(pairs), len(barycenters)


def _point(values: FloatArray) -> str:
    return " ".join(f"{value:.10f}" for value in values)


def _first_positive(shift: FloatArray) -> bool:
    """Keep one of the two opposite translations of a self-image pair."""

    nonzero = shift[np.abs(shift) > 0.5]
    return bool(nonzero.size and nonzero[0] > 0)


def _triplet_barycenters(
    cell: FloatArray,
    fractional: FloatArray,
    neighbors: list[tuple[int, int, FloatArray]],
    distance: float,
) -> list[FloatArray]:
    """Barycenters of atom-image triplets whose three distances are within *distance*.

    A triplet is formed at its lowest atom index i from two images within *distance* of
    atom i that are within *distance* of each other. It is counted once: its sites, as
    (atom, lattice shift) after translating the smallest site to the origin cell, form
    a key that only lattice translation leaves unchanged.
    """

    by_atom: dict[int, list[tuple[int, FloatArray]]] = {}
    for i, j, d in neighbors:
        by_atom.setdefault(i, []).append((j, d))
    seen: set[tuple[tuple[int, int, int, int], ...]] = set()
    result: list[FloatArray] = []
    for i, sites in by_atom.items():
        displacements = np.array([d for _, d in sites])
        close = np.linalg.norm(
            (displacements[:, None, :] - displacements[None, :, :]) @ cell, axis=2
        )
        for a, b in zip(*np.nonzero(np.triu(close <= distance, 1)), strict=True):
            members = [
                (i, np.zeros(3)),
                (sites[a][0], displacements[a]),
                (sites[b][0], displacements[b]),
            ]
            key = sorted(
                (atom, *np.rint(fractional[i] + d - fractional[atom]).astype(int).tolist())
                for atom, d in members
            )
            origin = key[0][1:]
            canonical = tuple(
                (atom, x - origin[0], y - origin[1], z - origin[2]) for atom, x, y, z in key
            )
            if canonical in seen:
                continue
            seen.add(canonical)
            result.append(fractional[i] + (displacements[a] + displacements[b]) / 3)
    return result


def periodic_critic2_input(
    structure: str | Path,
    *,
    distance: float,
    points: int,
    triplets: bool,
    report_file: str,
    graph: bool,
) -> str:
    """Return the input text for the structure a VASP single point ran on.

    critic2 reads the crystal from the AECCAR0 header, so the seeds are placed from
    *structure*, the POSCAR or CONTCAR of the same calculation; their positions agree
    to the header's six decimals when the calculation did not move the atoms
    (NSW = 0). The ``CRYSTAL`` line names the structure's species in file order:
    critic2 uses them only when the AECCAR0 header has no species line and otherwise
    takes the header's names. A structure without species names writes none.
    """

    data = parse_poscar(structure)
    cell = np.asarray(data.cell, dtype=float) / BOHR_ANGSTROM
    fractional = np.asarray(data.fractional_positions, dtype=float)
    lines, pair_count, triplet_count = periodic_seeds(cell, fractional, distance, points, triplets)
    species = (
        ""
        if data.symbols_inferred
        else " " + " ".join(element_symbol(label) for label in data.species)
    )
    triplet_note = (
        f", and the barycenter of each atom triplet within {distance:g} bohr" if triplets else ""
    )
    description = (
        "QTAIM critical points of the all-electron density AECCAR0 + AECCAR2, written by "
        f"ElecTrace from {Path(structure).name}. Seeds: one line of {points} points per atom "
        f"pair within {distance:g} bohr, counting periodic images{triplet_note} "
        f"({pair_count} pairs, {triplet_count} triplets, "
        f"{pair_count * points + triplet_count} seeds)."
    )
    cpreport = f"CPREPORT {report_file}" + (" GRAPH" if graph else "")
    return (
        "".join(f"# {line}\n" for line in textwrap.wrap(description, 86))
        + f"CRYSTAL AECCAR0{species}\n"
        + "LOAD AECCAR0\n"
        + "LOAD AECCAR2\n"
        + 'LOAD AS "$1+$2" ID rhoae SMOOTHRHO\n'
        + "REFERENCE rhoae\n\n"
        + "# Ignore numerical stationary points in the vacuum region.\n"
        + 'AUTO DISCARD "$rhoae < 1e-5" \\\n'
        + " \\\n".join(lines)
        + f"\n{cpreport}\nEND\n"
    )
