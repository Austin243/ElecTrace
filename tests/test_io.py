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
from electrace.io.discovery import DiscoveredPath, DiscoveryError, discover_path


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
    series = discover_static_series(pressure, mode="pressure")
    assert [frame.pressure_gpa for frame in series.frames] == [5.0, 10.0, 20.0]


def _with_stress(root: Path, labels: list[str], stresses: list[float]) -> None:
    for label, stress in zip(labels, stresses, strict=True):
        _write_frame(root / label)
        (root / label / "OUTCAR").write_text(
            f"  external pressure = {stress} kB  Pullay stress = 0.00 kB\n", encoding="utf-8"
        )


def _labels(found: DiscoveredPath) -> list[str]:
    return [frame.label for frame in found.frames]


_STRESSES = [12.31, -4.2, 33.1, 58.7, 21.45, 7.02, -1.5]


@pytest.mark.parametrize("mode", ["auto", "static", "ordered"])
def test_outcar_stress_does_not_reorder_named_frames(tmp_path: Path, mode: str) -> None:
    # Every VASP run prints a residual stress, so it says nothing about the path order.
    labels = [f"image_{number}" for number in range(7)]
    _with_stress(tmp_path, labels, _STRESSES)

    found = discover_path(tmp_path, mode=mode)

    assert _labels(found) == labels
    assert found.kind == "static"
    assert found.warnings == ()
    assert found.frames[0].pressure_gpa == pytest.approx(1.231)


def test_series_kind_static_keeps_numeric_names_in_order(tmp_path: Path) -> None:
    labels = [f"{number:02d}" for number in range(7)]
    _with_stress(tmp_path, labels, _STRESSES)

    found = discover_path(tmp_path, mode="static")
    assert (_labels(found), found.kind, found.warnings) == (labels, "static", ())

    # The counterexample: numeric names under auto were always a safe NEB.
    found = discover_path(tmp_path)
    assert (_labels(found), found.kind, found.warnings) == (labels, "neb", ())


def test_explicit_pressure_series_sorts_by_stress_and_says_so(tmp_path: Path) -> None:
    _with_stress(tmp_path, ["a", "b", "c"], [300.0, 100.0, 200.0])

    found = discover_path(tmp_path, mode="pressure")

    assert _labels(found) == ["b", "c", "a"]
    assert found.kind == "pressure"
    assert len(found.warnings) == 1
    assert "b, c, a" in found.warnings[0] and "a, b, c" in found.warnings[0]


def test_explicit_pressure_series_requires_every_pressure(tmp_path: Path) -> None:
    _with_stress(tmp_path, ["a", "b"], [300.0, 100.0])
    _write_frame(tmp_path / "c")

    with pytest.raises(DiscoveryError, match="pressure is missing for static frames: c"):
        discover_path(tmp_path, mode="pressure")


def test_names_stating_a_pressure_stay_a_pressure_series(tmp_path: Path) -> None:
    labels = ["5GPa", "10GPa", "200kbar"]
    for label in labels:
        _write_frame(tmp_path / label)

    for mode in ("auto", "pressure"):
        found = discover_path(tmp_path, mode=mode)
        assert (_labels(found), found.kind, found.warnings) == (labels, "pressure", ())
    found = discover_path(tmp_path, mode="ordered")
    assert (_labels(found), found.kind, found.warnings) == (labels, "pressure", ())
    found = discover_path(tmp_path, mode="static")
    assert (_labels(found), found.kind, found.warnings) == (labels, "static", ())


def test_pressure_order_that_differs_from_the_names_is_warned(tmp_path: Path) -> None:
    for label in ("-5GPa", "5GPa", "10GPa"):
        _write_frame(tmp_path / label)

    found = discover_path(tmp_path)
    assert _labels(found) == ["-5GPa", "5GPa", "10GPa"]
    assert found.kind == "pressure"
    assert len(found.warnings) == 1
    assert "-5GPa, 5GPa, 10GPa" in found.warnings[0]
    assert "5GPa, 10GPa, -5GPa" in found.warnings[0]

    # series_kind says which order wins, and the name order is still not set by numbers alone.
    found = discover_path(tmp_path, mode="ordered")
    assert _labels(found) == ["5GPa", "10GPa", "-5GPa"]
    assert found.kind == "pressure"
    assert len(found.warnings) == 1


@pytest.mark.parametrize(
    ("labels", "order"),
    [
        (["IS", "TS", "FS"], ["FS", "IS", "TS"]),
        (["reactant", "ts", "product"], ["product", "reactant", "ts"]),
        (["before", "after"], ["after", "before"]),
        (["img_-1", "img_0", "img_1"], ["img_0", "img_1", "img_-1"]),
        (["a1", "a2", "b1"], ["a1", "a2", "b1"]),
        (["strain0.25", "strain0.5", "strain0.75"], ["strain0.5", "strain0.25", "strain0.75"]),
    ],
)
def test_order_that_numbers_do_not_set_is_warned(
    tmp_path: Path, labels: list[str], order: list[str]
) -> None:
    for label in labels:
        _write_frame(tmp_path / label)

    found = discover_path(tmp_path)

    assert _labels(found) == order
    assert found.kind == "static"
    assert len(found.warnings) == 1
    assert ", ".join(order) in found.warnings[0]


@pytest.mark.parametrize(
    "labels",
    [
        ["image_1", "image_2", "image_10"],
        ["00_start", "01_ts", "02_end"],
        ["neb-1", "neb-2", "neb-3"],
        ["run.1", "run.2", "run.10"],
        ["strain0.90", "strain0.95", "strain1.00"],
        ["Image1", "image2"],
        ["only"],
    ],
)
def test_numbered_names_need_no_order_warning(tmp_path: Path, labels: list[str]) -> None:
    for label in labels:
        _write_frame(tmp_path / label)

    found = discover_path(tmp_path)

    assert _labels(found) == labels
    assert found.warnings == ()


def test_neb_refuses_other_names_and_points_to_ordered(tmp_path: Path) -> None:
    for label in ("image_0", "image_1"):
        _write_frame(tmp_path / label)

    with pytest.raises(DiscoveryError, match='series_kind = "ordered"'):
        discover_path(tmp_path, mode="neb")


@pytest.mark.parametrize("stray", ["20261105123000", "1" + "0" * 30])
def test_stray_numeric_folder_gives_a_clean_error(tmp_path: Path, stray: str) -> None:
    for label in ("00", "01", "02", stray):
        _write_frame(tmp_path / label)

    with pytest.raises(DiscoveryError) as caught:
        discover_path(tmp_path)

    assert str(caught.value) == (
        f"NEB image indices are not contiguous; missing 3 to {int(stray) - 1}"
    )


def test_small_gap_lists_every_missing_image(tmp_path: Path) -> None:
    for label in ("00", "01", "05", "07"):
        _write_frame(tmp_path / label)

    with pytest.raises(DiscoveryError) as caught:
        discover_path(tmp_path)

    assert str(caught.value) == "NEB image indices are not contiguous; missing [2, 3, 4, 6]"


def test_permissive_discovery_keeps_a_stray_numeric_folder(tmp_path: Path) -> None:
    for label in ("00", "01", "20261105123000"):
        _write_frame(tmp_path / label)

    assert _labels(discover_path(tmp_path, strict=False)) == ["00", "01", "20261105123000"]


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
