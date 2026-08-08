"""Deterministic, dependency-free ElecTrace demonstration data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from electrace.config import write_default_config


def _poscar(c_position: float, n_position: float) -> str:
    return f"""ElecTrace synthetic C-N path
1.0
10.0 0.0 0.0
0.0 10.0 0.0
0.0 0.0 10.0
C N
1 1
Direct
{c_position:.8f} 0.50000000 0.50000000
{n_position:.8f} 0.50000000 0.50000000
"""


def _feature(
    feature_id: str,
    kind: str,
    x: float,
    *,
    charge: float | None,
    volume: float | None,
    neighbors: list[int],
    **attributes: Any,
) -> dict[str, Any]:
    return {
        "id": feature_id,
        "kind": kind,
        "fractional_position": [x, 0.5, 0.5],
        "charge": charge,
        "volume": volume,
        "neighbor_atom_indices": neighbors,
        "attributes": attributes,
    }


def generate_demo(root: str | Path, *, overwrite: bool = False) -> Path:
    """Generate a six-frame synthetic path with known electronic events.

    The data are a software integration fixture, not a physical DFT result. Two
    atom-associated ELF basins fuse, a bond critical point appears, and a
    non-nuclear attractor is born late in the path. Frame-local feature IDs are
    deliberately changed so successful tracking cannot rely on string identity.
    """

    destination = Path(root)
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"Demo destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    c_positions = [0.35, 0.365, 0.385, 0.405, 0.420, 0.430]
    n_positions = [0.65, 0.635, 0.615, 0.595, 0.580, 0.570]
    energies = [0.00, 0.16, 0.48, 0.71, 0.28, -0.08]

    for index, (c_x, n_x, energy) in enumerate(
        zip(c_positions, n_positions, energies, strict=True)
    ):
        label = f"{index:02d}"
        frame_dir = destination / label
        frame_dir.mkdir(parents=True, exist_ok=True)
        structure = _poscar(c_x, n_x)
        (frame_dir / "POSCAR").write_text(structure, encoding="utf-8")
        (frame_dir / "CONTCAR").write_text(structure, encoding="utf-8")
        (frame_dir / "OUTCAR").write_text(
            f" free  energy   TOTEN  =      {energy: .8f} eV\n",
            encoding="utf-8",
        )

        features: list[dict[str, Any]] = []
        if index < 3:
            features.extend(
                [
                    _feature(
                        f"elf-C-local-{index}",
                        "elf_basin",
                        c_x + 0.018,
                        charge=1.25 - 0.05 * index,
                        volume=5.2 - 0.15 * index,
                        neighbors=[0],
                        elf_maximum=0.91 - 0.01 * index,
                    ),
                    _feature(
                        f"elf-N-local-{index}",
                        "elf_basin",
                        n_x - 0.018,
                        charge=1.50 + 0.05 * index,
                        volume=5.5 - 0.15 * index,
                        neighbors=[1],
                        elf_maximum=0.93 + 0.005 * index,
                    ),
                ]
            )
        else:
            features.append(
                _feature(
                    f"elf-CN-local-{index}",
                    "elf_basin",
                    (c_x + n_x) / 2,
                    # At the known merge, the synthetic population and volume
                    # exactly equal the two predecessor basins.  This makes the
                    # integration fixture an unambiguous positive control for
                    # the matcher's channel-wise conservation gate.
                    charge=2.75 + 0.03 * (index - 3),
                    volume=10.1 + 0.1 * (index - 3),
                    neighbors=[0, 1],
                    elf_maximum=0.86 + 0.01 * (index - 3),
                )
            )

        if index >= 2:
            features.append(
                _feature(
                    f"bcp-local-{index}",
                    "bond_critical_point",
                    (c_x + n_x) / 2,
                    charge=None,
                    volume=None,
                    neighbors=[0, 1],
                    rho=0.06 + 0.025 * (index - 2),
                    laplacian=0.18 - 0.07 * (index - 2),
                    icohp=-(0.4 + 0.55 * (index - 2)),
                )
            )

        if index >= 4:
            features.append(
                _feature(
                    f"nna-local-{index}",
                    "non_nuclear_attractor",
                    0.5 + 0.012 * (index - 4),
                    charge=0.18 + 0.04 * (index - 4),
                    volume=1.1 + 0.15 * (index - 4),
                    neighbors=[0, 1],
                    rho=0.045 + 0.005 * (index - 4),
                )
            )

        descriptor = {
            "schema_version": "electrace.features.v1",
            "producer": {"name": "electrace-demo", "version": "0.1"},
            "frame": {"index": index, "label": label},
            "features": features,
            "metadata": {
                "synthetic": True,
                "scientific_interpretation_allowed": False,
                "energy_unit": "eV",
                "charge_convention": (
                    "Synthetic demo integrated feature population in elementary-charge units; "
                    "not a physical charge definition."
                ),
            },
        }
        (frame_dir / "features.json").write_text(
            json.dumps(descriptor, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    config_path = destination / "electrace.toml"
    write_default_config(config_path, input_root=".", overwrite=overwrite)
    config_text = config_path.read_text(encoding="utf-8").replace(
        'name = "ElecTrace analysis"',
        'name = "Synthetic C–N electronic rearrangement"',
        1,
    )
    config_path.write_text(config_text, encoding="utf-8")
    (destination / "README.txt").write_text(
        "Synthetic ElecTrace integration fixture.\n"
        "This directory contains no physical DFT result and must not be used for scientific claims.\n",
        encoding="utf-8",
    )
    return config_path
