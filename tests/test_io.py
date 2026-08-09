from __future__ import annotations

import json
from pathlib import Path

import pytest

from electrace.io import (
    DescriptorError,
    descriptor_from_dict,
    discover_neb,
    discover_static_series,
    dump_descriptor,
    load_descriptor,
    load_path,
    parse_poscar,
    read_poscar,
    read_vasp_energy,
)


def _poscar(*, x: float = 0.0, cartesian: bool = False) -> str:
    return (
        "C N test\n"
        "1.0\n"
        "2.0 0.0 0.0\n"
        "0.0 2.0 0.0\n"
        "0.0 0.0 2.0\n"
        "C N\n"
        "1 1\n"
        f"{'Cartesian' if cartesian else 'Direct'}\n"
        f"{x} 0.0 0.0\n"
        "1.0 1.0 1.0\n"
    )


def _write_frame(directory: Path, *, x: float = 0.0, energy: float | None = None) -> None:
    directory.mkdir(parents=True)
    (directory / "POSCAR").write_text(_poscar(x=x), encoding="utf-8")
    if energy is not None:
        (directory / "OUTCAR").write_text(
            f" free  energy   TOTEN  =       {energy: .8f} eV\n",
            encoding="utf-8",
        )


def _canonical_feature(*, frame_index: int = 0, frame_label: str = "00") -> dict[str, object]:
    return {
        "schema_version": "electrace.features.v1",
        "producer": {"name": "critic2", "version": "1.2"},
        "frame": {"index": frame_index, "label": frame_label},
        "features": [
            {
                "id": "bcp-1",
                "kind": "bond_critical_point",
                "fractional_position": [0.25, 0.0, 0.0],
                "neighbor_atom_indices": [0, 1],
                "attributes": {"rho": 0.14},
            }
        ],
        "metadata": {"grid": "fine"},
    }


def test_poscar_parsing_handles_direct_cartesian_and_negative_scale(tmp_path: Path) -> None:
    direct = tmp_path / "POSCAR"
    direct.write_text(_poscar(x=0.25), encoding="utf-8")
    parsed = parse_poscar(direct)
    assert parsed.fractional_positions[0] == (0.25, 0.0, 0.0)
    assert [atom.element for atom in read_poscar(direct).atoms] == ["C", "N"]

    cartesian = tmp_path / "cartesian.vasp"
    cartesian.write_text(_poscar(x=1.0, cartesian=True), encoding="utf-8")
    assert parse_poscar(cartesian).fractional_positions[0] == pytest.approx((0.5, 0.0, 0.0))

    scaled = tmp_path / "scaled.vasp"
    scaled.write_text(
        _poscar().replace("1.0\n2.0 0.0 0.0", "-64.0\n2.0 0.0 0.0", 1),
        encoding="utf-8",
    )
    cell = parse_poscar(scaled).cell
    assert cell[0][0] * cell[1][1] * cell[2][2] == pytest.approx(64.0)

    vasp4 = tmp_path / "POSCAR.v4"
    vasp4.write_text(_poscar().replace("C N\n1 1", "1 1", 1), encoding="utf-8")
    assert parse_poscar(vasp4).elements == ("X1", "X2")

    oszicar = tmp_path / "OSZICAR"
    oszicar.write_text(" 1 F= -1.0 E0= -1.2 d E =0\n", encoding="utf-8")
    assert read_vasp_energy(oszicar) == -1.2


def test_discovery_orders_neb_and_pressure_series(tmp_path: Path) -> None:
    neb = tmp_path / "neb"
    _write_frame(neb / "10", energy=-8.0)
    _write_frame(neb / "8", energy=-6.0)
    _write_frame(neb / "9", energy=-7.0)
    discovered = discover_neb(neb)
    assert [frame.label for frame in discovered.frames] == ["8", "9", "10"]
    assert [frame.index for frame in discovered.frames] == [0, 1, 2]
    assert [frame.energy_ev for frame in discovered.frames] == [-6.0, -7.0, -8.0]

    pressure = tmp_path / "pressure"
    for label in ("p20", "5GPa", "100kbar"):
        _write_frame(pressure / label)
    series = discover_static_series(pressure, require_pressure=True)
    assert [frame.pressure_gpa for frame in series.frames] == [5.0, 10.0, 20.0]


def test_canonical_descriptor_round_trip_and_validation(tmp_path: Path) -> None:
    document = descriptor_from_dict(_canonical_feature())
    loaded = load_descriptor(dump_descriptor(document, tmp_path / "features.json"))

    assert loaded.producer == "critic2"
    assert loaded.features[0].kind == "bond_critical_point"
    assert loaded.features[0].neighbor_atom_indices == (0, 1)

    duplicate = _canonical_feature()
    duplicate["features"] = [duplicate["features"][0], duplicate["features"][0]]  # type: ignore[index]
    with pytest.raises(DescriptorError, match="unique"):
        descriptor_from_dict(duplicate)


def test_bader_baderkit_lobster_and_chargemol_adapters(tmp_path: Path) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(_poscar(x=0.95), encoding="utf-8")
    frame = read_poscar(structure, label="00")

    acf = tmp_path / "ACF.dat"
    acf.write_text(
        "1 0 0 0 5.8 0 6.0\n2 0 0 0 7.1 0 5.5\n",
        encoding="utf-8",
    )
    assert [feature.charge for feature in load_descriptor(acf, frame=frame).features] == [
        5.8,
        7.1,
    ]

    baderkit = tmp_path / "bader.json"
    baderkit.write_text(
        json.dumps(
            {
                "atom_results": {
                    "basin_atoms": [0, 2],
                    "atom_charges": [5.8, 7.1],
                    "atom_volumes": [6.0, 5.5],
                },
                "maxima_results": {
                    "maxima_frac": [[0.95, 0.0, 0.0], [0.5, 0.5, 0.5]],
                    "basin_charges": [5.8, 0.2],
                    "basin_volumes": [6.0, 0.4],
                },
            }
        ),
        encoding="utf-8",
    )
    bader_features = load_descriptor(baderkit, frame=frame).features
    assert [feature.kind for feature in bader_features] == [
        "atomic_basin",
        "non_nuclear_attractor",
        "atomic_charge",
        "atomic_charge",
    ]

    lobster = tmp_path / "ICOHPLIST.lobster"
    lobster.write_text(
        "No. atom1 atom2 distance translation ICOHP\n1 C1 N2 1.20 0 0 0 -4.75\n",
        encoding="utf-8",
    )
    bond = load_descriptor(lobster, frame=frame).features[0]
    assert bond.attributes["icohp"] == -4.75
    assert bond.neighbor_atom_indices == (0, 1)
    assert bond.attributes["lattice_translation"] == [0, 0, 0]

    icobi = tmp_path / "ICOBILIST.lobster"
    icobi.write_text("1 C1 N2 1.20 0.42\n", encoding="utf-8")
    assert load_descriptor(icobi, frame=frame).features[0].attributes["icobi"] == 0.42

    chargemol = tmp_path / "DDEC6_even_tempered_net_atomic_charges.xyz"
    chargemol.write_text(
        "2\nDDEC6 charges\nC 0 0 0 0.30\nN 1 1 1 -0.30\n",
        encoding="utf-8",
    )
    ddec = load_descriptor(chargemol, frame=frame)
    assert [feature.charge for feature in ddec.features] == [0.3, -0.3]


def test_badelf_maps_appended_nna_site(tmp_path: Path) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(_poscar(x=0.95), encoding="utf-8")
    frame = read_poscar(structure, label="00")
    path = tmp_path / "badelf.json"
    path.write_text(
        json.dumps(
            {
                "method_kwargs": {"version": "0.10.0"},
                "atom_results": {
                    "atom_charges": [5.7, 7.0, 0.65],
                    "atom_volumes": [5.9, 5.4, 0.7],
                    "species": ["C", "N", "X"],
                },
                "nna_results": {
                    "num_nnas": 1,
                    "nna_structure": {
                        "sites": [
                            {"abc": [0.95, 0, 0]},
                            {"abc": [0, 0, 0]},
                            {"abc": [0.5, 0.5, 0.5]},
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    document = load_descriptor(path, frame=frame)
    nna = document.features[-1]
    assert nna.kind == "non_nuclear_attractor"
    assert nna.fractional_position == pytest.approx((0.5, 0.5, 0.5))
    assert nna.charge == pytest.approx(0.65)


def test_critic2_cpreport_joins_topology_and_position(tmp_path: Path) -> None:
    path = tmp_path / "critic2.features.json"
    path.write_text(
        json.dumps(
            {
                "structure": {"number_of_cell_atoms": 2},
                "critical_points": {
                    "nonequivalent_cps": [
                        {
                            "id": 3,
                            "signature": -1,
                            "is_nucleus": False,
                            "field": 0.2,
                            "hessian": [1, 0, 0, 0, 2, 0, 0, 0, -4],
                        }
                    ],
                    "cell_cps": [
                        {
                            "id": 3,
                            "nonequivalent_id": 3,
                            "fractional_coordinates": [0.25, 0.25, 0.25],
                            "attractors": [{"cell_id": 1}, {"cell_id": 2}],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    feature = load_descriptor(path).features[0]
    assert feature.kind == "bond_critical_point"
    assert feature.neighbor_atom_indices == (0, 1)
    assert feature.attributes["field"] == pytest.approx(0.2)
    assert feature.attributes["laplacian"] == pytest.approx(-1.0)


def test_load_path_combines_frames_energy_and_external_descriptors(tmp_path: Path) -> None:
    root = tmp_path / "neb"
    descriptors = tmp_path / "descriptors"
    _write_frame(root / "00", energy=-10.0)
    _write_frame(root / "01", x=0.1, energy=-9.0)
    descriptors.mkdir()
    for index, label in enumerate(("00", "01")):
        payload = _canonical_feature(frame_index=index, frame_label=label)
        payload["features"][0]["id"] = f"bcp-{index}"  # type: ignore[index]
        (descriptors / f"{label}.json").write_text(json.dumps(payload), encoding="utf-8")

    frames = load_path(root, descriptor_dir=descriptors)

    assert [frame.energy for frame in frames] == [-10.0, -9.0]
    assert [frame.features[0].id for frame in frames] == ["bcp-0", "bcp-1"]
