from __future__ import annotations

import json
import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import electrace.io.descriptors as descriptor_module
from electrace.io import (
    DescriptorError,
    DiscoveryError,
    VaspParseError,
    descriptor_from_dict,
    discover_neb,
    discover_static_series,
    dump_descriptor,
    load_descriptor,
    load_path,
    parse_poscar,
    pressure_from_label,
    pressure_from_outcar,
    read_poscar,
    read_vasp_energy,
)


def _poscar(*, x: float = 0.0, cartesian: bool = False, selective: bool = False) -> str:
    mode = "Cartesian" if cartesian else "Direct"
    selective_line = "Selective dynamics\n" if selective else ""
    flags = " T F T" if selective else ""
    return (
        "C N test\n"
        "1.0\n"
        "2.0 0.0 0.0\n"
        "0.0 2.0 0.0\n"
        "0.0 0.0 2.0\n"
        "C N\n"
        "1 1\n"
        f"{selective_line}"
        f"{mode}\n"
        f"{x} 0.0 0.0{flags}\n"
        f"1.0 1.0 1.0{flags}\n"
    )


def _write_frame(directory: Path, *, x: float = 0.0, energy: float | None = None) -> None:
    directory.mkdir(parents=True)
    (directory / "POSCAR").write_text(_poscar(x=x), encoding="utf-8")
    if energy is not None:
        (directory / "OUTCAR").write_text(
            f" free  energy   TOTEN  =       {energy + 1.0: .8f} eV\n"
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
                "voxel_indices": [[1, 2, 3], [2, 3, 4]],
                "attributes": {"rho": 0.14},
            }
        ],
        "metadata": {"grid": "fine"},
    }


def test_parse_poscar_direct_selective_and_core_frame(tmp_path: Path) -> None:
    path = tmp_path / "POSCAR"
    path.write_text(_poscar(x=0.25, selective=True), encoding="utf-8")

    parsed = parse_poscar(path)
    frame = read_poscar(path, index=3, label="image-3", energy=-12.5)

    assert parsed.cell == ((2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (0.0, 0.0, 2.0))
    assert parsed.fractional_positions[0] == (0.25, 0.0, 0.0)
    assert parsed.selective_dynamics[0] == (True, False, True)
    assert [atom.element for atom in frame.atoms] == ["C", "N"]
    assert frame.atoms[0].attributes["selective_dynamics"] == (True, False, True)
    assert frame.energy == -12.5


def test_parse_cartesian_and_negative_volume_scale(tmp_path: Path) -> None:
    cartesian = tmp_path / "cartesian.vasp"
    cartesian.write_text(_poscar(x=1.0, cartesian=True), encoding="utf-8")
    assert parse_poscar(cartesian).fractional_positions[0] == pytest.approx((0.5, 0.0, 0.0))

    volume_scaled = tmp_path / "volume.vasp"
    volume_scaled.write_text(
        _poscar().replace("1.0\n2.0 0.0 0.0", "-64.0\n2.0 0.0 0.0", 1),
        encoding="utf-8",
    )
    parsed = parse_poscar(volume_scaled)
    determinant = parsed.cell[0][0] * parsed.cell[1][1] * parsed.cell[2][2]
    assert determinant == pytest.approx(64.0)

    component_scaled = tmp_path / "components.vasp"
    component_scaled.write_text(
        _poscar(cartesian=True)
        .replace("1.0\n2.0 0.0 0.0", "2.0 3.0 4.0\n2.0 0.0 0.0", 1)
        .replace("0.0 2.0 0.0", "1.0 2.0 0.0", 1),
        encoding="utf-8",
    )
    component_data = parse_poscar(component_scaled)
    assert component_data.cell[1] == (2.0, 6.0, 0.0)


def test_parse_vasp4_placeholder_symbols_and_reject_bad_input(tmp_path: Path) -> None:
    vasp4 = tmp_path / "POSCAR.v4"
    vasp4.write_text(_poscar().replace("C N\n1 1", "1 1", 1), encoding="utf-8")
    parsed = parse_poscar(vasp4)
    assert parsed.elements == ("X1", "X2")
    assert parsed.symbols_inferred is True

    bad = tmp_path / "bad"
    bad.write_text("not a POSCAR\n", encoding="utf-8")
    with pytest.raises(VaspParseError, match="too short"):
        parse_poscar(bad)


def test_energy_and_pressure_extraction_use_last_records(tmp_path: Path) -> None:
    outcar = tmp_path / "OUTCAR"
    outcar.write_text(
        "free energy TOTEN = -1.0 eV\n"
        " external pressure = 100.0 kB  Pullay stress = 0.0 kB\n"
        "free energy TOTEN = -2.5E+01 eV\n"
        " external pressure = 225.0 kB\n",
        encoding="utf-8",
    )
    oszicar = tmp_path / "OSZICAR"
    oszicar.write_text(
        " 1 F= -.10 E0= -.20 d E =0\n 2 F= 1.0 E0= 0.0 d E =0\n",
        encoding="utf-8",
    )

    assert read_vasp_energy(outcar) == -25.0
    assert read_vasp_energy(oszicar) == 0.0
    assert pressure_from_outcar(outcar) == 22.5
    assert pressure_from_label("run_pressure_7.5_gpa") == 7.5
    assert pressure_from_label("100kbar") == 10.0
    assert pressure_from_label("relax") is None


def test_neb_discovery_orders_numeric_directories_and_validates_gaps(tmp_path: Path) -> None:
    _write_frame(tmp_path / "10", energy=-8.0)
    _write_frame(tmp_path / "8", energy=-6.0)
    _write_frame(tmp_path / "9", energy=-7.0)
    discovered = discover_neb(tmp_path)
    assert [frame.label for frame in discovered.frames] == ["8", "9", "10"]
    assert [frame.index for frame in discovered.frames] == [0, 1, 2]
    assert [frame.metadata["directory_index"] for frame in discovered.frames] == [8, 9, 10]
    assert [frame.energy_ev for frame in discovered.frames] == [-6.0, -7.0, -8.0]

    (tmp_path / "9" / "POSCAR").unlink()
    with pytest.raises(DiscoveryError, match="no usable CONTCAR or POSCAR"):
        discover_neb(tmp_path)
    non_strict = discover_neb(tmp_path, strict=False)
    assert [frame.label for frame in non_strict.frames] == ["8", "10"]
    assert [frame.index for frame in non_strict.frames] == [0, 1]


def test_nonzero_neb_directory_numbers_use_canonical_descriptor_frame_indices(
    tmp_path: Path,
) -> None:
    for canonical_index, label in enumerate(("08", "09")):
        directory = tmp_path / label
        _write_frame(directory)
        payload = _canonical_feature(
            frame_index=canonical_index,
            frame_label=label,
        )
        payload["features"][0]["id"] = f"feature-{canonical_index}"  # type: ignore[index]
        (directory / "features.json").write_text(json.dumps(payload), encoding="utf-8")

    frames = load_path(tmp_path)

    assert [frame.index for frame in frames] == [0, 1]
    assert [frame.label for frame in frames] == ["08", "09"]
    assert [frame.metadata["directory_index"] for frame in frames] == [8, 9]
    assert [frame.features[0].id for frame in frames] == ["feature-0", "feature-1"]


def test_neb_discovery_rejects_normalized_duplicate_indices(tmp_path: Path) -> None:
    _write_frame(tmp_path / "01")
    _write_frame(tmp_path / "1")
    with pytest.raises(DiscoveryError, match="ambiguous NEB indices"):
        discover_neb(tmp_path)


def test_static_pressure_series_orders_by_physical_pressure(tmp_path: Path) -> None:
    _write_frame(tmp_path / "p20")
    _write_frame(tmp_path / "5GPa")
    _write_frame(tmp_path / "100kbar")

    discovered = discover_static_series(tmp_path, require_pressure=True)
    assert discovered.kind.value == "pressure"
    assert [frame.pressure_gpa for frame in discovered.frames] == [5.0, 10.0, 20.0]
    assert [frame.label for frame in discovered.frames] == ["5GPa", "100kbar", "p20"]


def test_ordered_mode_alias_loads_non_pressure_static_series(tmp_path: Path) -> None:
    _write_frame(tmp_path / "state10")
    _write_frame(tmp_path / "state2")
    frames = load_path(tmp_path, mode="ordered")
    assert [frame.label for frame in frames] == ["state2", "state10"]


def test_custom_structure_names_and_fallback_order_reach_discovery(tmp_path: Path) -> None:
    for label in ("00", "01"):
        directory = tmp_path / label
        directory.mkdir()
        (directory / "START.vasp").write_text(_poscar(x=0.1), encoding="utf-8")
    (tmp_path / "00" / "FINAL.vasp").write_text(_poscar(x=0.2), encoding="utf-8")

    preferred = discover_neb(
        tmp_path,
        structure_name="FINAL.vasp",
        fallback_structure_name="START.vasp",
    )
    reversed_order = discover_neb(
        tmp_path,
        prefer_contcar=False,
        structure_name="FINAL.vasp",
        fallback_structure_name="START.vasp",
    )

    assert [frame.structure.name for frame in preferred.frames] == [
        "FINAL.vasp",
        "START.vasp",
    ]
    assert [frame.structure.name for frame in reversed_order.frames] == [
        "START.vasp",
        "START.vasp",
    ]


def test_load_path_descriptor_source_policies_are_explicit_and_auditable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "series"
    descriptors = tmp_path / "external"
    _write_frame(root / "state")
    descriptors.mkdir()

    local = _canonical_feature(frame_label="state")
    local["features"][0]["id"] = "local"  # type: ignore[index]
    (root / "state" / "electronic.json").write_text(json.dumps(local), encoding="utf-8")
    external = _canonical_feature(frame_label="state")
    external["features"][0]["id"] = "external"  # type: ignore[index]
    external_file = descriptors / "state.json"
    external_file.write_text(json.dumps(external), encoding="utf-8")

    automatic = load_path(
        root,
        descriptor_dir=descriptors,
        descriptor_name="electronic.json",
        mode="static",
    )
    local_only = load_path(
        root,
        descriptor_dir=descriptors,
        descriptor_name="electronic.json",
        descriptor_source="local",
        mode="static",
    )
    merged = load_path(
        root,
        descriptor_dir=descriptors,
        descriptor_name="electronic.json",
        descriptor_source="merge",
        mode="static",
    )

    assert [feature.id for feature in automatic[0].features] == ["external"]
    assert automatic[0].features[0].attributes["descriptor_producer"] == "critic2"
    assert [feature.id for feature in local_only[0].features] == ["local"]
    assert [feature.id for feature in merged[0].features] == ["local", "external"]
    assert automatic[0].metadata["descriptor_source_policy"] == "external"
    assert local_only[0].metadata["descriptor_source_policy"] == "local"
    assert merged[0].metadata["descriptor_sources"] == [
        str((root / "state" / "electronic.json").resolve()),
        str(external_file.resolve()),
    ]

    external_file.unlink()
    empty = load_path(
        root,
        descriptor_dir=descriptors,
        descriptor_source="external",
        mode="static",
    )[0]
    assert empty.features == ()
    assert empty.metadata["descriptor_source_policy"] == "external"
    assert empty.metadata["descriptor_sources"] == []


def test_load_path_rejects_ambiguous_or_unsafe_descriptor_selection(tmp_path: Path) -> None:
    _write_frame(tmp_path / "state")
    with pytest.raises(DescriptorError, match="descriptor_dir is required"):
        load_path(tmp_path, descriptor_source="merge", mode="static")
    with pytest.raises(DescriptorError, match="basename"):
        load_path(tmp_path, descriptor_name="../features.json", mode="static")
    with pytest.raises(DiscoveryError, match="basename"):
        load_path(tmp_path, structure_name="../CONTCAR", mode="static")


def test_canonical_descriptor_roundtrip_and_strict_validation(tmp_path: Path) -> None:
    document = descriptor_from_dict(_canonical_feature())
    destination = dump_descriptor(document, tmp_path / "features.json")
    loaded = load_descriptor(destination)

    assert loaded.producer == "critic2"
    assert loaded.features[0].kind == "bond_critical_point"
    assert loaded.features[0].neighbor_atom_indices == (0, 1)
    assert loaded.features[0].voxel_indices == ((1, 2, 3), (2, 3, 4))

    duplicate = _canonical_feature()
    duplicate["features"] = [duplicate["features"][0], duplicate["features"][0]]  # type: ignore[index]
    with pytest.raises(DescriptorError, match="unique"):
        descriptor_from_dict(duplicate)

    non_finite = _canonical_feature()
    non_finite["features"][0]["charge"] = math.inf  # type: ignore[index]
    with pytest.raises(DescriptorError, match="finite"):
        descriptor_from_dict(non_finite)

    duplicate_key = tmp_path / "duplicate-key.json"
    duplicate_key.write_text(
        '{"schema_version":"electrace.features.v1","producer":{"name":"x","name":"y"},"features":[]}',
        encoding="utf-8",
    )
    with pytest.raises(DescriptorError, match="duplicate object key"):
        load_descriptor(duplicate_key)

    unknown_producer = _canonical_feature()
    unknown_producer["producer"]["command"] = "critic2 input.cri"  # type: ignore[index]
    with pytest.raises(DescriptorError, match="producer contains unknown fields: command"):
        descriptor_from_dict(unknown_producer)

    unknown_frame = _canonical_feature()
    unknown_frame["frame"]["source"] = "00/CONTCAR"  # type: ignore[index]
    with pytest.raises(DescriptorError, match="frame contains unknown fields: source"):
        descriptor_from_dict(unknown_frame)

    missing_convention = _canonical_feature()
    missing_convention["features"][0]["charge"] = 0.2  # type: ignore[index]
    with pytest.raises(DescriptorError, match=r"metadata\.charge_convention"):
        descriptor_from_dict(missing_convention)

    missing_convention["metadata"]["charge_convention"] = (  # type: ignore[index]
        "Signed net charge in elementary-charge units."
    )
    assert descriptor_from_dict(missing_convention).features[0].charge == pytest.approx(0.2)


def test_descriptor_limit_is_enforced_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = (tmp_path / "features.json").resolve()
    limit = 64
    descriptor.write_bytes(b"{" + b" " * limit)
    real_stat = Path.stat

    def stale_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        result = real_stat(path, *args, **kwargs)
        if path == descriptor:
            return SimpleNamespace(st_mode=result.st_mode, st_size=limit)
        return result

    monkeypatch.setattr(descriptor_module, "MAX_DESCRIPTOR_BYTES", limit)
    monkeypatch.setattr(Path, "stat", stale_stat)

    with pytest.raises(DescriptorError, match="safety limit"):
        load_descriptor(descriptor)


@pytest.mark.parametrize(
    "filename",
    ["features.json", "ICOHPLIST.lobster", "ACF.dat", "DDEC6_charges.xyz"],
)
def test_descriptor_text_formats_require_utf8(tmp_path: Path, filename: str) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(_poscar(), encoding="utf-8")
    frame = read_poscar(structure)
    descriptor = tmp_path / filename
    descriptor.write_bytes(b"valid-prefix\n\xff\n")

    with pytest.raises(DescriptorError, match="not valid UTF-8"):
        load_descriptor(descriptor, frame=frame)


def test_deeply_nested_descriptor_json_has_a_bounded_error(tmp_path: Path) -> None:
    descriptor = tmp_path / "features.json"
    descriptor.write_text("[" * 10_000 + "0" + "]" * 10_000, encoding="utf-8")

    with pytest.raises(DescriptorError, match="cannot parse descriptor JSON"):
        load_descriptor(descriptor)


@pytest.mark.parametrize("target_exists", [False, True])
def test_dump_descriptor_never_follows_leaf_symlinks(tmp_path: Path, target_exists: bool) -> None:
    target = tmp_path / "outside.json"
    if target_exists:
        target.write_text("outside\n", encoding="utf-8")
    destination = tmp_path / "features.json"
    destination.symlink_to(target)
    document = descriptor_from_dict(_canonical_feature())

    with pytest.raises(OSError, match="symbolic link"):
        dump_descriptor(document, destination, overwrite=True)

    assert destination.is_symlink()
    assert target.exists() is target_exists
    if target_exists:
        assert target.read_text(encoding="utf-8") == "outside\n"


def test_dump_descriptor_is_atomic_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "features.json"
    destination.write_text("original\n", encoding="utf-8")
    document = descriptor_from_dict(_canonical_feature())
    write_text = Path.write_text

    def fail_temporary_write(path: Path, data: str, **kwargs: Any) -> int:
        if path.name.startswith(f".{destination.name}."):
            write_text(path, "partial\n", **kwargs)
            raise OSError("simulated write failure")
        return write_text(path, data, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temporary_write)

    with pytest.raises(OSError, match="simulated write failure"):
        dump_descriptor(document, destination, overwrite=True)

    assert destination.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_charge_convention_schema_matches_runtime_nonwhitespace_rule() -> None:
    schema_path = Path(__file__).parents[1] / "schemas" / "electrace.features.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    convention_schema = schema["allOf"][0]["then"]["properties"]["metadata"]["properties"][
        "charge_convention"
    ]
    pattern = convention_schema["pattern"]

    assert re.search(pattern, "   ") is None
    assert re.search(pattern, "signed net charge") is not None

    payload = _canonical_feature()
    payload["features"][0]["charge"] = 0.2  # type: ignore[index]
    payload["metadata"]["charge_convention"] = "   "  # type: ignore[index]
    with pytest.raises(DescriptorError, match=r"metadata\.charge_convention"):
        descriptor_from_dict(payload)


def test_native_baderkit_critic2_lobster_and_chargemol_adapters(tmp_path: Path) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(_poscar(x=0.95), encoding="utf-8")
    frame = read_poscar(structure, label="00")

    bader = tmp_path / "bader.json"
    bader.write_text(
        json.dumps(
            {
                "maxima_frac": [[0.95, 0.0, 0.0], [0.5, 0.5, 0.5]],
                "basin_charges": [5.8, 0.2],
                "basin_volumes": [6.0, 0.4],
                "basin_atoms": [0, 2],
                "atom_charges": [5.8, 7.1],
                "atom_volumes": [6.0, 5.5],
            }
        ),
        encoding="utf-8",
    )
    bader_document = load_descriptor(bader, frame=frame)
    assert [feature.kind for feature in bader_document.features] == [
        "atomic_basin",
        "non_nuclear_attractor",
        "atomic_charge",
        "atomic_charge",
    ]

    critic = tmp_path / "critic2.json"
    critic.write_text(
        json.dumps(
            {
                "critical_points": [
                    {
                        "id": "cp7",
                        "type": "BCP",
                        "frac": [0.99, 0.0, 0.0],
                        "atoms": [0, 1],
                        "rho": 0.2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    critic_document = load_descriptor(critic, producer="critic2")
    assert critic_document.features[0].kind == "bond_critical_point"
    assert critic_document.features[0].attributes["rho"] == 0.2

    lobster = tmp_path / "ICOHPLIST.lobster"
    lobster.write_text(
        "No. atom1 atom2 distance translation ICOHP\n1 C1 N2 1.20 0 0 0 -4.75\n",
        encoding="utf-8",
    )
    lobster_feature = load_descriptor(lobster, frame=frame).features[0]
    assert lobster_feature.attributes["icohp"] == -4.75
    assert lobster_feature.attributes["icohp_ev"] == -4.75
    assert lobster_feature.attributes["descriptor_family"] == "icohp"
    assert lobster_feature.attributes["lattice_translation"] == [0, 0, 0]
    # Explicit zero-translation midpoint between fractional x=0.95 and x=1.0.
    assert lobster_feature.fractional_position[0] == pytest.approx(0.975)

    chargemol = tmp_path / "DDEC6_even_tempered_net_atomic_charges.xyz"
    chargemol.write_text(
        "2\nDDEC6 charges\nC 0 0 0 0.30\nN 1 1 1 -0.30\n",
        encoding="utf-8",
    )
    ddec = load_descriptor(chargemol, frame=frame)
    assert [feature.charge for feature in ddec.features] == [0.3, -0.3]
    assert all(
        feature.attributes["charge_definition"] == "ddec_net_atomic_charge"
        for feature in ddec.features
    )
    chargemol_json = tmp_path / "chargemol.json"
    chargemol_json.write_text(
        json.dumps({"atoms": [{"index": 0, "charge": 0.3}, {"index": 1, "charge": -0.3}]}),
        encoding="utf-8",
    )
    ddec_json = load_descriptor(chargemol_json, frame=frame)
    assert all(
        feature.attributes["charge_definition"] == "ddec_net_atomic_charge"
        for feature in ddec_json.features
    )

    lobster_json = tmp_path / "lobster.json"
    lobster_json.write_text(
        json.dumps({"bonds": [{"atom1": 1, "atom2": 2, "icohp": -4.75, "icobi": 0.42}]}),
        encoding="utf-8",
    )
    multi_family = load_descriptor(lobster_json, frame=frame).features[0]
    assert multi_family.attributes["descriptor_family"] == "icohp+icobi"


@pytest.mark.parametrize(
    ("filename", "descriptor", "value"),
    [
        ("ICOHPLIST.lobster", "icohp_ev", -4.2),
        ("ICOBILIST.lobster", "icobi", 0.31),
    ],
)
def test_lobster_native_translation_selects_atom2_image_for_midpoint(
    tmp_path: Path, filename: str, descriptor: str, value: float
) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(
        _poscar(x=0.1).replace("1.0 1.0 1.0", "0.2 0.0 0.0", 1),
        encoding="utf-8",
    )
    frame = read_poscar(structure, label="00")
    native = tmp_path / filename
    native.write_text(
        f"No. atom1 atom2 distance translation value\n1 C1 N2 2.20 1 0 0 {value}\n",
        encoding="utf-8",
    )

    feature = load_descriptor(native, frame=frame).features[0]

    # Atom 2 is at fractional x=0.2 in cell +1, so the unwrapped midpoint
    # with atom 1 at x=0.1 is 0.65. A minimum-image implementation gives 0.15.
    assert feature.fractional_position == pytest.approx((0.65, 0.0, 0.0))
    assert feature.attributes[descriptor] == pytest.approx(value)
    assert feature.attributes["lattice_translation"] == [1, 0, 0]
    assert feature.attributes["midpoint_convention"] == "explicit_atom2_lattice_translation"


def test_lobster_simplified_row_keeps_minimum_image_midpoint(tmp_path: Path) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(
        _poscar(x=0.1).replace("1.0 1.0 1.0", "0.2 0.0 0.0", 1),
        encoding="utf-8",
    )
    frame = read_poscar(structure, label="00")
    native = tmp_path / "simplified-ICOHPLIST.lobster"
    native.write_text("1 C1 N2 0.20 -1.5\n", encoding="utf-8")

    feature = load_descriptor(native, frame=frame).features[0]

    assert feature.fractional_position == pytest.approx((0.15, 0.0, 0.0))
    assert feature.attributes["midpoint_convention"] == "minimum_image"
    assert "lattice_translation" not in feature.attributes


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("2 C1 N2 2.20 1 0 -4.2", "7 columns"),
        ("2 C1 N2 2.20 1.0 0 0 -4.2", "lattice translation"),
        ("2 C1 N2 nan 1 0 0 -4.2", "distance must be finite"),
        ("2 C1 N2 2.20 1 0 0 nan", "ICOHP must be finite"),
    ],
)
def test_lobster_native_malformed_record_is_not_silently_dropped(
    tmp_path: Path, row: str, message: str
) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(_poscar(x=0.1), encoding="utf-8")
    frame = read_poscar(structure, label="00")
    native = tmp_path / "ICOHPLIST.lobster"
    native.write_text(
        f"No. atom1 atom2 distance translation ICOHP\n1 C1 N2 1.80 0 0 0 -1.0\n{row}\n",
        encoding="utf-8",
    )

    with pytest.raises(DescriptorError, match=message):
        load_descriptor(native, frame=frame)


def test_lobster_json_translation_uses_same_periodic_image_contract(tmp_path: Path) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(
        _poscar(x=0.1).replace("1.0 1.0 1.0", "0.2 0.0 0.0", 1),
        encoding="utf-8",
    )
    frame = read_poscar(structure, label="00")
    lobster_json = tmp_path / "lobster.json"
    lobster_json.write_text(
        json.dumps(
            {"bonds": [{"atom1": 1, "atom2": 2, "icohp": -2.0, "cell_translation": [1, 0, 0]}]}
        ),
        encoding="utf-8",
    )

    feature = load_descriptor(lobster_json, frame=frame).features[0]

    assert feature.fractional_position == pytest.approx((0.65, 0.0, 0.0))
    assert feature.attributes["lattice_translation"] == [1, 0, 0]

    lobster_json.write_text(
        json.dumps(
            {"bonds": [{"atom1": 1, "atom2": 2, "icohp": -2.0, "translation": [1.0, 0, 0]}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(DescriptorError, match="translation must contain exactly three integers"):
        load_descriptor(lobster_json, frame=frame)


def test_official_baderkit_bader_json_nested_results(tmp_path: Path) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(_poscar(x=0.95), encoding="utf-8")
    frame = read_poscar(structure, label="00")

    bader = tmp_path / "bader.json"
    bader.write_text(
        json.dumps(
            {
                "method_kwargs": {"version": "0.10.0", "method": "neargrid"},
                "atom_results": {
                    "atom_charges": [5.8, 7.1],
                    "atom_volumes": [6.0, 5.5],
                    "oxidation_states": [0.2, -0.1],
                    "basin_atoms": [0, 2],
                },
                "maxima_results": {
                    "maxima_frac": [[0.95, 0.0, 0.0], [0.5, 0.5, 0.5]],
                    "basin_charges": [5.8, 0.2],
                    "basin_volumes": [6.0, 0.4],
                },
                "base_summary_props": {"spin_system": "total"},
            }
        ),
        encoding="utf-8",
    )

    document = load_descriptor(bader, frame=frame)

    assert document.producer_version == "0.10.0"
    assert document.metadata["native_format"] == "bader.json"
    assert [feature.kind for feature in document.features] == [
        "atomic_basin",
        "non_nuclear_attractor",
        "atomic_charge",
        "atomic_charge",
    ]
    assert document.features[0].neighbor_atom_indices == (0,)
    assert document.features[1].neighbor_atom_indices == ()
    assert [feature.charge for feature in document.features[2:]] == [5.8, 7.1]
    assert all(
        feature.attributes["charge_definition"] == "bader_electron_population"
        for feature in document.features[2:]
    )
    assert [feature.attributes["oxidation_state"] for feature in document.features[2:]] == [
        0.2,
        -0.1,
    ]

    oxidation_only = tmp_path / "renamed-bader-results.json"
    oxidation_only.write_text(
        json.dumps(
            {
                "atom_results": {"oxidation_states": [1.25, -1.25]},
                "maxima_results": {},
            }
        ),
        encoding="utf-8",
    )
    oxidation_document = load_descriptor(oxidation_only, frame=frame)
    assert [feature.charge for feature in oxidation_document.features] == [1.25, -1.25]
    assert all(
        feature.attributes["charge_definition"] == "bader_oxidation_state"
        for feature in oxidation_document.features
    )
    assert oxidation_document.metadata["native_format"] == "renamed-bader-results.json"


def test_official_baderkit_badelf_json_maps_appended_nna_sites(tmp_path: Path) -> None:
    structure = tmp_path / "POSCAR"
    structure.write_text(_poscar(x=0.95), encoding="utf-8")
    frame = read_poscar(structure, label="00")

    badelf = tmp_path / "badelf.json"
    badelf.write_text(
        json.dumps(
            {
                "method_kwargs": {"version": "0.10.0", "partition_method": "badelf"},
                "atom_results": {
                    "atom_charges": [5.7, 7.0, 0.65],
                    "atom_volumes": [5.9, 5.4, 0.7],
                    "oxidation_states": [0.3, 0.0, -0.65],
                    "species": ["C", "N", "Xmc"],
                    "maxima_elf_values": [0.81, 0.88, 0.96],
                    "total_electron_number": 13.35,
                },
                "nna_results": {
                    "nna_dimensionality": 2,
                    "num_nnas": 1,
                    "nnas_per_formula": 0.65,
                    "nna_structure": {
                        "@module": "pymatgen.core.structure",
                        "@class": "Structure",
                        "sites": [
                            {"species": [{"element": "C", "occu": 1}], "abc": [0.95, 0, 0]},
                            {"species": [{"element": "N", "occu": 1}], "abc": [0, 0, 0]},
                            {
                                "species": [{"element": "Xmc", "occu": 1}],
                                "abc": [0.5, 0.5, 0.5],
                            },
                        ],
                    },
                },
                "base_summary_props": {"spin_system": "total"},
            }
        ),
        encoding="utf-8",
    )

    document = load_descriptor(badelf, frame=frame)

    assert document.producer_version == "0.10.0"
    assert document.metadata["native_format"] == "badelf.json"
    assert [feature.kind for feature in document.features] == [
        "atomic_charge",
        "atomic_charge",
        "non_nuclear_attractor",
    ]
    nna = document.features[-1]
    assert nna.id == "baderkit:nna:0"
    assert nna.fractional_position == pytest.approx((0.5, 0.5, 0.5))
    assert nna.neighbor_atom_indices == ()
    assert nna.charge == pytest.approx(0.65)
    assert nna.volume == pytest.approx(0.7)
    assert nna.attributes["charge_definition"] == "bader_electron_population"
    assert nna.attributes["species"] == "Xmc"
    assert nna.attributes["maxima_elf_value"] == pytest.approx(0.96)
    assert nna.attributes["oxidation_state"] == pytest.approx(-0.65)


def test_critic2_native_cpreport_json_is_joined_conservatively(tmp_path: Path) -> None:
    report = tmp_path / "critic2.features.json"
    report.write_text(
        json.dumps(
            {
                "units": "bohr",
                "metadata": {
                    "charge_convention": (
                        "critic2 critical-point integrated population in elementary-charge units"
                    )
                },
                "structure": {"number_of_cell_atoms": 2},
                "field": {"id": 1, "name": "rho", "source": "CHGCAR"},
                "critical_points": {
                    "number_of_nonequivalent_cps": 4,
                    "nonequivalent_cps": [
                        {"id": 1, "signature": -3, "is_nucleus": True},
                        {"id": 2, "signature": -3, "is_nucleus": True},
                        {
                            "id": 3,
                            "name": "b1",
                            "rank": 3,
                            "signature": -1,
                            "is_nucleus": False,
                            "field": 0.2,
                            "charge": 0.25,
                            "volume": 1.5,
                            "hessian": [1.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, -4.0],
                        },
                        {
                            "id": 4,
                            "name": "nna1",
                            "rank": 3,
                            "signature": -3,
                            "is_nucleus": False,
                            "field": 0.1,
                            "volume": 0.5,
                        },
                    ],
                    "number_of_cell_cps": 4,
                    "cell_cps": [
                        {
                            "id": 1,
                            "signature": -3,
                            "nonequivalent_id": 1,
                            "fractional_coordinates": [0.0, 0.0, 0.0],
                        },
                        {
                            "id": 2,
                            "signature": -3,
                            "nonequivalent_id": 2,
                            "fractional_coordinates": [0.5, 0.5, 0.5],
                        },
                        {
                            "id": 3,
                            "signature": -1,
                            "nonequivalent_id": 3,
                            "fractional_coordinates": [0.25, 0.25, 0.25],
                            "attractors": [{"cell_id": 1}, {"cell_id": 2}],
                        },
                        {
                            "id": 4,
                            "signature": -3,
                            "nonequivalent_id": 4,
                            "fractional_coordinates": [0.75, 0.75, 0.75],
                            "volume": 0.75,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    document = load_descriptor(report)

    assert [feature.id for feature in document.features] == ["critic2:cp:3", "critic2:cp:4"]
    assert [feature.kind for feature in document.features] == [
        "bond_critical_point",
        "non_nuclear_attractor",
    ]
    assert document.features[0].neighbor_atom_indices == (0, 1)
    assert document.features[0].charge == pytest.approx(0.25)
    assert document.features[0].volume == pytest.approx(1.5)
    # The explicit cell value overrides the nonequivalent definition's 0.5.
    assert document.features[1].volume == pytest.approx(0.75)
    assert document.features[0].attributes["field"] == pytest.approx(0.2)
    assert document.features[0].attributes["laplacian"] == pytest.approx(-1.0)
    assert document.features[0].attributes["hessian"] == [
        1.0,
        0.0,
        0.0,
        0.0,
        2.0,
        0.0,
        0.0,
        0.0,
        -4.0,
    ]
    assert document.metadata == {
        "native_format": "cpreport.json",
        "units": "bohr",
        "charge_convention": (
            "critic2 critical-point integrated population in elementary-charge units"
        ),
    }

    malformed = tmp_path / "critic2-malformed.json"
    malformed.write_text(
        json.dumps({"critical_points": {"cell_cps": "not-an-array"}}),
        encoding="utf-8",
    )
    with pytest.raises(DescriptorError, match="CPREPORT JSON must contain"):
        load_descriptor(malformed)


def test_load_path_combines_structure_energy_and_external_descriptors(tmp_path: Path) -> None:
    root = tmp_path / "neb"
    descriptors = tmp_path / "descriptors"
    _write_frame(root / "00", x=0.0, energy=-10.0)
    _write_frame(root / "01", x=0.1, energy=-9.0)
    descriptors.mkdir()
    for index, label in enumerate(("00", "01")):
        payload = _canonical_feature(frame_index=index, frame_label=label)
        payload["features"][0]["id"] = f"bcp-{index}"  # type: ignore[index]
        if index == 0:
            payload["metadata"]["warnings"] = ["grid convergence was marginal"]  # type: ignore[index]
        (descriptors / f"{label}.json").write_text(json.dumps(payload), encoding="utf-8")

    frames = load_path(root, descriptor_dir=descriptors)

    assert [frame.index for frame in frames] == [0, 1]
    assert [frame.energy for frame in frames] == [-10.0, -9.0]
    assert [frame.features[0].id for frame in frames] == ["bcp-0", "bcp-1"]
    assert frames[0].metadata["series_kind"] == "neb"
    assert frames[0].metadata["descriptor_producers"] == ["critic2"]
    assert frames[0].metadata["descriptor_documents"] == [
        {
            "schema_version": "electrace.features.v1",
            "producer": {"name": "critic2", "version": "1.2"},
            "metadata": {
                "grid": "fine",
                "warnings": ["grid convergence was marginal"],
            },
        }
    ]
    assert frames[0].features[0].attributes["descriptor_producer"] == "critic2"


def test_load_path_rejects_descriptor_frame_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "series"
    _write_frame(root / "only")
    descriptor = tmp_path / "features.json"
    descriptor.write_text(
        json.dumps(_canonical_feature(frame_index=2, frame_label="other")), encoding="utf-8"
    )
    with pytest.raises(DescriptorError, match="does not match"):
        load_path(root, descriptor_dir=descriptor, mode="static")


def test_load_path_rejects_conflicting_per_feature_producer_tag(tmp_path: Path) -> None:
    root = tmp_path / "series"
    _write_frame(root / "only")
    payload = _canonical_feature(frame_label="only")
    payload["features"][0]["attributes"]["descriptor_producer"] = "lobster"  # type: ignore[index]
    (root / "only" / "features.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DescriptorError, match="conflicting with document producer"):
        load_path(root, mode="static")


def test_load_path_propagates_charge_convention_and_rejects_conflict(
    tmp_path: Path,
) -> None:
    root = tmp_path / "series"
    _write_frame(root / "only")
    payload = _canonical_feature(frame_label="only")
    feature = payload["features"][0]  # type: ignore[index]
    feature["charge"] = 0.2  # type: ignore[index]
    payload["metadata"]["charge_convention"] = "Signed net feature charge."  # type: ignore[index]
    descriptor = root / "only" / "features.json"
    descriptor.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_path(root, mode="static")

    assert loaded[0].features[0].attributes["charge_convention"] == "Signed net feature charge."

    feature["attributes"]["charge_convention"] = "Integrated electron population."  # type: ignore[index]
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DescriptorError, match="conflicting with document convention"):
        load_path(root, mode="static")
