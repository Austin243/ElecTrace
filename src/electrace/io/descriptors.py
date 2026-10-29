"""Normalization of plugin results into ``electrace.features.v1`` JSON.

The canonical descriptor document is::

    {
      "schema_version": "electrace.features.v1",
      "producer": {"name": "critic2", "version": "optional"},
      "frame": {"index": 0, "label": "00"},
      "features": [{
        "id": "bcp-1",
        "kind": "bond_critical_point",
        "fractional_position": [0.1, 0.2, 0.3],
        "charge": null,
        "volume": null,
        "neighbor_atom_indices": [0, 1],
        "voxel_indices": [[1, 2, 3]],
        "attributes": {}
      }],
      "metadata": {}
    }

``schema_version``, ``producer.name``, and ``features`` are required.  Every
feature requires a unique non-empty ``id``, a non-empty ``kind``, and three
finite fractional coordinates.  Neighbor atom indices are zero-based.  This
module also contains intentionally conservative adapters for BaderKit JSON,
critic2-derived JSON, LOBSTER ICOH(P|B)LIST files, Bader ``ACF.dat``, Chargemol
DDEC charge XYZ files, and Bader spin populations in ``spin/ACF.dat``, plus Bader net
charges from the ZVAL in a frame's ``POTCAR`` or ``OUTCAR``.  Native
formats that do not contain periodic coordinates require the associated
:class:`~electrace.models.Frame`.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from electrace._files import atomic_write_text
from electrace.models import Feature, Frame

from .vasp import VaspParseError, element_symbol, read_vasp_zval

SCHEMA_VERSION = "electrace.features.v1"
MAX_DESCRIPTOR_BYTES = 64 * 1024 * 1024


class DescriptorError(ValueError):
    """Raised when a plugin descriptor is malformed or ambiguous."""


def _descriptor_size_error(path: Path) -> DescriptorError:
    return DescriptorError(
        f"descriptor file exceeds the {MAX_DESCRIPTOR_BYTES // (1024 * 1024)} MiB "
        f"safety limit: {path}"
    )


def _read_descriptor_text(path: Path) -> str:
    """Read one UTF-8 descriptor while enforcing the byte limit during I/O."""

    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_DESCRIPTOR_BYTES + 1)
    except OSError as exc:
        raise DescriptorError(f"cannot read descriptor file {path}: {exc}") from exc
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        raise _descriptor_size_error(path)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DescriptorError(
            f"descriptor file is not valid UTF-8 at byte {exc.start}: {path}"
        ) from exc


@dataclass(frozen=True, slots=True)
class DescriptorDocument:
    """Validated normalized features from one producer for one optional frame."""

    producer: str
    features: tuple[Feature, ...]
    producer_version: str | None = None
    frame_index: int | None = None
    frame_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise DescriptorError(
                f"unsupported descriptor schema {self.schema_version!r}; expected {SCHEMA_VERSION!r}"
            )
        if not self.producer or not self.producer.strip():
            raise DescriptorError("descriptor producer name must not be empty")
        identifiers = [feature.id for feature in self.features]
        if len(identifiers) != len(set(identifiers)):
            raise DescriptorError("feature ids must be unique within a descriptor document")
        if self.frame_index is not None and self.frame_index < 0:
            raise DescriptorError("descriptor frame index must be non-negative")
        convention = self.metadata.get("charge_convention")
        if convention is not None and (not isinstance(convention, str) or not convention.strip()):
            raise DescriptorError("metadata.charge_convention must be a non-empty string")
        if any(feature.charge is not None for feature in self.features) and convention is None:
            raise DescriptorError(
                "descriptor documents containing charge must declare metadata.charge_convention"
            )

    def to_dict(self) -> dict[str, Any]:
        producer: dict[str, Any] = {"name": self.producer}
        if self.producer_version:
            producer["version"] = self.producer_version
        frame: dict[str, Any] = {}
        if self.frame_index is not None:
            frame["index"] = self.frame_index
        if self.frame_label is not None:
            frame["label"] = self.frame_label
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "producer": producer,
            "features": [feature.to_dict() for feature in self.features],
            "metadata": self.metadata,
        }
        if frame:
            result["frame"] = frame
        return result


def _finite(value: Any, context: str) -> float:
    if isinstance(value, bool):
        raise DescriptorError(f"{context} must be a number, not boolean")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise DescriptorError(f"{context} must be a number") from exc
    if not math.isfinite(parsed):
        raise DescriptorError(f"{context} must be finite")
    return parsed


def _position(value: Any, context: str) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise DescriptorError(f"{context} must be an array of three numbers")
    return tuple(_finite(item, context) for item in value)  # type: ignore[return-value]


def _neighbors(value: Any, context: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DescriptorError(f"{context} must be an array of zero-based integers")
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise DescriptorError(f"{context} must contain non-negative integers")
        result.append(item)
    if len(result) != len(set(result)):
        raise DescriptorError(f"{context} must not contain duplicates")
    return tuple(result)


def _feature_from_mapping(item: Any, index: int) -> Feature:
    if not isinstance(item, Mapping):
        raise DescriptorError(f"features[{index}] must be an object")
    if not isinstance(item.get("id"), str) or not str(item["id"]).strip():
        raise DescriptorError(f"features[{index}].id must be a non-empty string")
    if not isinstance(item.get("kind"), str) or not str(item["kind"]).strip():
        raise DescriptorError(f"features[{index}].kind must be a non-empty string")
    try:
        return Feature.from_dict(item)
    except (KeyError, TypeError, ValueError) as exc:
        raise DescriptorError(f"invalid features[{index}]: {exc}") from exc


def descriptor_from_dict(payload: Any) -> DescriptorDocument:
    """Validate and construct a canonical ``electrace.features.v1`` document."""

    if not isinstance(payload, Mapping):
        raise DescriptorError("descriptor root must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise DescriptorError(f"schema_version must be exactly {SCHEMA_VERSION!r}")
    producer = payload.get("producer")
    if (
        not isinstance(producer, Mapping)
        or not isinstance(producer.get("name"), str)
        or not producer["name"].strip()
    ):
        raise DescriptorError("producer must be an object with a non-empty name")
    features_payload = payload.get("features")
    if not isinstance(features_payload, list):
        raise DescriptorError("features must be an array")
    features = tuple(
        _feature_from_mapping(item, index) for index, item in enumerate(features_payload)
    )
    frame = payload.get("frame", {})
    if not isinstance(frame, Mapping):
        raise DescriptorError("frame must be an object when present")
    frame_index = frame.get("index")
    if frame_index is not None and (
        isinstance(frame_index, bool) or not isinstance(frame_index, int)
    ):
        raise DescriptorError("frame.index must be a non-negative integer")
    frame_label = frame.get("label")
    if frame_label is not None and not isinstance(frame_label, str):
        raise DescriptorError("frame.label must be a string")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise DescriptorError("metadata must be an object")
    version = producer.get("version")
    if version is not None and not isinstance(version, str):
        raise DescriptorError("producer.version must be a string")
    return DescriptorDocument(
        producer=producer["name"],
        producer_version=version,
        frame_index=frame_index,
        frame_label=frame_label,
        features=features,
        metadata=dict(metadata),
    )


def _frame_position(
    frame: Frame | None, atom_index: int, context: str
) -> tuple[float, float, float]:
    if frame is None:
        raise DescriptorError(f"{context} requires a Frame to supply periodic atom positions")
    try:
        atom = frame.atom_by_index(atom_index)
    except KeyError as exc:
        raise DescriptorError(f"{context} refers to unknown frame atom index {atom_index}") from exc
    return tuple(atom.fractional_position)  # type: ignore[return-value]


def _series_item(values: Any, index: int) -> Any:
    if (
        isinstance(values, Sequence)
        and not isinstance(values, (str, bytes))
        and index < len(values)
    ):
        return values[index]
    return None


def _baderkit_structure_positions(payload: Mapping[str, Any]) -> Sequence[Any] | None:
    """Return fractional sites from BaderKit's serialized pymatgen structure."""

    nna_results = payload.get("nna_results")
    if not isinstance(nna_results, Mapping):
        return None
    structure = nna_results.get("nna_structure")
    if not isinstance(structure, Mapping):
        return None
    sites = structure.get("sites")
    if not isinstance(sites, Sequence) or isinstance(sites, (str, bytes)):
        return None
    positions: list[Any] = []
    for site in sites:
        if not isinstance(site, Mapping):
            return None
        position = site.get("abc")
        if position is None:
            return None
        positions.append(position)
    return positions


def _parse_baderkit(
    payload: Mapping[str, Any], frame: Frame | None, *, native_format: str = "bader.json"
) -> DescriptorDocument:
    atom_results = payload.get("atom_results", {})
    maxima_results = payload.get("maxima_results", {})
    if not isinstance(atom_results, Mapping) or not isinstance(maxima_results, Mapping):
        raise DescriptorError("BaderKit JSON atom_results and maxima_results must be objects")
    positions = maxima_results.get("maxima_frac")
    charges = maxima_results.get("basin_charges")
    volumes = maxima_results.get("basin_volumes")
    basin_atoms = atom_results.get("basin_atoms")
    known_atom_indices = {atom.index for atom in frame.atoms} if frame is not None else None
    features: list[Feature] = []
    if isinstance(positions, Sequence) and not isinstance(positions, (str, bytes)):
        for index, raw_position in enumerate(positions):
            assigned = _series_item(basin_atoms, index)
            atom_index = (
                int(assigned)
                if isinstance(assigned, (int, float)) and not isinstance(assigned, bool)
                else None
            )
            is_nna = atom_index is None or (
                known_atom_indices is not None and atom_index not in known_atom_indices
            )
            kind = "non_nuclear_attractor" if is_nna else "atomic_basin"
            charge = _series_item(charges, index)
            volume = _series_item(volumes, index)
            features.append(
                Feature(
                    id=f"baderkit:basin:{index}",
                    kind=kind,
                    fractional_position=_position(raw_position, f"BaderKit basin {index} position"),
                    charge=None
                    if charge is None
                    else _finite(charge, f"BaderKit basin {index} charge"),
                    volume=None
                    if volume is None
                    else _finite(volume, f"BaderKit basin {index} volume"),
                    neighbor_atom_indices=(atom_index,)
                    if atom_index is not None and not is_nna
                    else (),
                    attributes={
                        "source_index": index,
                        "charge_quantity": "integrated_electron_population",
                        "charge_unit": "e",
                        "volume_unit": "angstrom^3",
                    },
                )
            )

    oxidation_states = atom_results.get("oxidation_states")
    atom_charges = atom_results.get("atom_charges")
    charge_definition = "bader_electron_population"
    if atom_charges is None:
        atom_charges = oxidation_states
        charge_definition = "bader_oxidation_state"
    atom_volumes = atom_results.get("atom_volumes")
    species = atom_results.get("species")
    maxima_elf_values = atom_results.get("maxima_elf_values")
    is_badelf = native_format.lower() == "badelf.json"
    structure_positions = _baderkit_structure_positions(payload) if is_badelf else None
    atom_count = len(frame.atoms) if frame is not None else None
    if isinstance(atom_charges, Sequence) and not isinstance(atom_charges, (str, bytes)):
        for index, charge in enumerate(atom_charges):
            if is_badelf and atom_count is not None and index >= atom_count:
                nna_index = index - atom_count
                if structure_positions is None or index >= len(structure_positions):
                    raise DescriptorError(
                        "BaderKit BadELF JSON contains NNA charge results without "
                        "matching nna_structure fractional coordinates"
                    )
                position = _position(
                    structure_positions[index], f"BaderKit BadELF NNA {nna_index} position"
                )
                identifier = f"baderkit:nna:{nna_index}"
                kind = "non_nuclear_attractor"
                neighbors: tuple[int, ...] = ()
            else:
                position = _frame_position(frame, index, "BaderKit atomic charges")
                identifier = f"baderkit:atom:{index}"
                kind = "atomic_charge"
                neighbors = (index,)
            attributes: dict[str, Any] = {
                "source_index": index,
                "charge_definition": charge_definition,
                "charge_unit": "e",
                "volume_unit": "angstrom^3",
            }
            raw_species = _series_item(species, index)
            if isinstance(raw_species, str):
                attributes["species"] = raw_species
            raw_elf_value = _series_item(maxima_elf_values, index)
            if raw_elf_value is not None:
                attributes["maxima_elf_value"] = _finite(
                    raw_elf_value, f"BaderKit atom/NNA {index} maximum ELF value"
                )
            raw_oxidation_state = _series_item(oxidation_states, index)
            if raw_oxidation_state is not None:
                attributes["oxidation_state"] = _finite(
                    raw_oxidation_state, f"BaderKit atom/NNA {index} oxidation state"
                )
            features.append(
                Feature(
                    id=identifier,
                    kind=kind,
                    fractional_position=position,
                    charge=_finite(charge, f"BaderKit atom {index} charge"),
                    volume=(
                        None
                        if _series_item(atom_volumes, index) is None
                        else _finite(
                            _series_item(atom_volumes, index), f"BaderKit atom {index} volume"
                        )
                    ),
                    neighbor_atom_indices=neighbors,
                    attributes=attributes,
                )
            )
    if not features:
        raise DescriptorError("BaderKit JSON contains no recognized basin or atom result arrays")
    method_kwargs = payload.get("method_kwargs")
    producer_version = method_kwargs.get("version") if isinstance(method_kwargs, Mapping) else None
    if not isinstance(producer_version, str):
        producer_version = None
    return DescriptorDocument(
        producer="baderkit",
        producer_version=producer_version,
        features=tuple(features),
        metadata={
            "native_format": native_format,
            "charge_convention": (
                "BaderKit atom_charges and basin_charges are positive integrated electron populations; "
                "oxidation_states, when used as fallback, are signed oxidation states."
            ),
        },
    )


def _critic2_kind(item: Mapping[str, Any]) -> str:
    signature = item.get("signature")
    if signature == -3 and item.get("is_nucleus") is False:
        return "non_nuclear_attractor"
    if signature == -1:
        return "bond_critical_point"
    if signature == 1:
        return "ring_critical_point"
    if signature == 3:
        return "cage_critical_point"
    return "other"


def _critic2_cpreport_records(
    payload: Mapping[str, Any],
) -> tuple[Sequence[Any], Mapping[int, Mapping[str, Any]], int | None]:
    """Return records and joins from critic2's native ``CPREPORT *.json``.

    The upstream format nests ``cell_cps`` and ``nonequivalent_cps`` below a
    ``critical_points`` object. Scientific values and the ``is_nucleus`` flag
    live on the non-equivalent records, while fractional positions and graph
    connectivity live on cell records.
    """

    critical_points = payload.get("critical_points")
    if not isinstance(critical_points, Mapping):
        raise DescriptorError("critic2 CPREPORT JSON requires a critical_points object")
    cell_records = critical_points.get("cell_cps")
    nonequivalent = critical_points.get("nonequivalent_cps")
    if not isinstance(cell_records, Sequence) or isinstance(cell_records, (str, bytes)):
        raise DescriptorError("critic2 CPREPORT JSON requires critical_points.cell_cps")
    joined: dict[int, Mapping[str, Any]] = {}
    if isinstance(nonequivalent, Sequence) and not isinstance(nonequivalent, (str, bytes)):
        for index, record in enumerate(nonequivalent):
            if not isinstance(record, Mapping):
                raise DescriptorError(
                    f"critic2 nonequivalent critical point {index} must be an object"
                )
            raw_id = record.get("id", index + 1)
            if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id < 1:
                raise DescriptorError(
                    f"critic2 nonequivalent critical point {index} has an invalid id"
                )
            joined[raw_id] = record
    structure = payload.get("structure")
    atom_count: int | None = None
    if isinstance(structure, Mapping):
        raw_atom_count = structure.get("number_of_cell_atoms")
        if isinstance(raw_atom_count, int) and not isinstance(raw_atom_count, bool):
            atom_count = raw_atom_count
    return cell_records, joined, atom_count


def _critic2_attractor_atoms(record: Mapping[str, Any], atom_count: int | None) -> tuple[int, ...]:
    """Map critic2 cell-CP attractors to zero-based cell-atom indices."""

    if atom_count is None:
        return ()
    attractors = record.get("attractors", ())
    if not isinstance(attractors, Sequence) or isinstance(attractors, (str, bytes)):
        raise DescriptorError("critic2 critical point attractors must be an array")
    result: list[int] = []
    for index, attractor in enumerate(attractors):
        if not isinstance(attractor, Mapping):
            raise DescriptorError(f"critic2 attractor {index} must be an object")
        cell_id = attractor.get("cell_id")
        if (
            isinstance(cell_id, int)
            and not isinstance(cell_id, bool)
            and 1 <= cell_id <= atom_count
        ):
            result.append(cell_id - 1)
    return tuple(dict.fromkeys(result))


def _critic2_hessian_trace(value: Any) -> float | None:
    """Return the finite trace of a flattened or nested 3x3 Hessian."""

    diagonal: tuple[Any, Any, Any]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) == 9:
            diagonal = (value[0], value[4], value[8])
        elif len(value) == 3 and all(
            isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) == 3
            for row in value
        ):
            diagonal = (value[0][0], value[1][1], value[2][2])
        else:
            return None
    else:
        return None
    if any(isinstance(item, bool) for item in diagonal):
        return None
    try:
        result = sum(float(item) for item in diagonal)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_critic2(payload: Mapping[str, Any]) -> DescriptorDocument:
    records, joined, atom_count = _critic2_cpreport_records(payload)
    features: list[Feature] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise DescriptorError(f"critic2 critical point {index} must be an object")
        nonequivalent_id = record.get("nonequivalent_id", record.get("id"))
        detail = (
            joined.get(nonequivalent_id, {})
            if isinstance(nonequivalent_id, int) and not isinstance(nonequivalent_id, bool)
            else {}
        )
        # A cell CP inherits scientific quantities from its nonequivalent
        # definition.  Explicit cell-record values are more specific and
        # intentionally override the shared definition when both are present.
        combined = {**detail, **record}
        raw_position = record.get("fractional_coordinates", detail.get("fractional_coordinates"))
        if raw_position is None:
            raise DescriptorError(f"critic2 critical point {index} has no fractional coordinates")
        if combined.get("signature") == -3 and combined.get("is_nucleus") is True:
            continue
        neighbors = _critic2_attractor_atoms(record, atom_count)
        attributes = {
            str(key): value
            for key, value in combined.items()
            if key
            not in {
                "id",
                "name",
                "fractional_coordinates",
                "cartesian_coordinates",
                "charge",
                "volume",
            }
        }
        if "laplacian" not in attributes:
            laplacian = _critic2_hessian_trace(attributes.get("hessian"))
            if laplacian is not None:
                attributes["laplacian"] = laplacian
        features.append(
            Feature(
                id=f"critic2:cp:{record.get('id', index + 1)}",
                kind=_critic2_kind(combined),
                fractional_position=_position(
                    raw_position, f"critic2 critical point {index} position"
                ),
                charge=(
                    None
                    if combined.get("charge") is None
                    else _finite(combined["charge"], "critic2 charge")
                ),
                volume=(
                    None
                    if combined.get("volume") is None
                    else _finite(combined["volume"], "critic2 volume")
                ),
                neighbor_atom_indices=_neighbors(
                    neighbors, f"critic2 critical point {index} atoms"
                ),
                attributes=attributes,
            )
        )
    metadata: dict[str, Any] = {
        "native_format": "cpreport.json",
        **({"units": payload["units"]} if isinstance(payload.get("units"), str) else {}),
    }
    payload_metadata = payload.get("metadata")
    if isinstance(payload_metadata, Mapping):
        for key in ("charge_convention", "warnings", "provenance"):
            if key in payload_metadata:
                metadata[key] = payload_metadata[key]
    if "charge_convention" in payload:
        metadata["charge_convention"] = payload["charge_convention"]
    return DescriptorDocument(
        producer="critic2",
        features=tuple(features),
        metadata=metadata,
    )


# LOBSTER labels an atom by element and one-based index (Na1). Orbital-resolved rows
# append the orbital to that label (Na1_3s, Fe1_3d_z^2); LCFO fragment labels such as
# AlN_1 are neither.
_ATOM_LABEL = re.compile(r"[A-Za-z]+(\d+)")
_ORBITAL_LABEL = re.compile(r"([A-Za-z]+\d+)_\S+")
_INTEGER_FIELD = re.compile(r"[+-]?\d+\Z")


def _atom_index(value: Any, context: str) -> int:
    if isinstance(value, bool):
        raise DescriptorError(f"{context} is not an atom index")
    if isinstance(value, int):
        raw = value
    elif isinstance(value, str):
        match = _ATOM_LABEL.fullmatch(value.strip())
        if match is None:
            raise DescriptorError(
                f"cannot read atom index from {value!r} in {context}; expected an atom "
                "label such as Na1"
            )
        raw = int(match.group(1))
    else:
        raise DescriptorError(f"cannot read atom index from {value!r} in {context}")
    result = raw - 1
    if result < 0:
        raise DescriptorError(f"{context} must use one-based positive atom labels")
    return result


def _periodic_midpoint(
    frame: Frame,
    first: int,
    second: int,
    *,
    lattice_translation: tuple[int, int, int] | None = None,
) -> tuple[float, float, float]:
    left = _frame_position(frame, first, "bond descriptor")
    right = _frame_position(frame, second, "bond descriptor")
    if lattice_translation is None:
        displacement = tuple(right[axis] - left[axis] for axis in range(3))
        displacement = tuple(value - round(value) for value in displacement)
    else:
        # LOBSTER's translation columns select the periodic image of atom 2.
        # They must take precedence over an independently chosen minimum image:
        # distinct listed bonds can join the same atom indices through different
        # lattice images.
        displacement = tuple(
            right[axis] + lattice_translation[axis] - left[axis] for axis in range(3)
        )
    return tuple((left[axis] + 0.5 * displacement[axis]) % 1.0 for axis in range(3))  # type: ignore[return-value]


_SPIN_LABEL = re.compile(r"for\s+spin\s+(\d+)", re.IGNORECASE)


def _atom_pair_rows(
    path: Path, rows: list[tuple[int, list[str]]], values: int
) -> list[tuple[int, list[str]]]:
    """Drop orbital-resolved rows after checking each against its atom-pair row.

    LOBSTER writes a bond's orbital rows directly below the atom-pair row that holds
    the bond total, with the same COHP#, distance, and translation. An orbital row
    anywhere else would mean a bond total is missing, so it is an error.
    """

    kept: list[tuple[int, list[str]]] = []
    for line_number, fields in rows:
        mu, nu = (_ORBITAL_LABEL.fullmatch(label) for label in fields[1:3])
        if mu is None or nu is None:
            kept.append((line_number, fields))
            continue
        identity = [fields[0], mu.group(1), nu.group(1), *fields[3:-values]]
        if not kept or kept[-1][1][:-values] != identity:
            raise DescriptorError(
                f"{path.name}:{line_number} is an orbital-resolved row that does not "
                "follow its atom-pair row"
            )
    return kept


def _lobster_rows(path: Path) -> tuple[list[tuple[int, list[str], list[str]]], int]:
    """Return LOBSTER bond rows as (line, identity columns, one value per spin).

    LOBSTER 3.x-5.0 writes one block per spin channel, each headed "for spin N".
    LOBSTER 5.1 writes both channels on one row below a header ending
    "for spin 1  for spin 2". A repeated, reordered, or unlabeled spin block is
    rejected rather than read as extra bonds. Orbital-resolved rows are dropped
    before spin blocks are paired; their count is returned with the rows.
    """

    blocks: list[list[tuple[int, list[str]]]] = []
    two_channels = False
    for line_number, line in enumerate(_read_descriptor_text(path).splitlines(), start=1):
        fields = line.split()
        if not fields:
            continue
        if fields[0].lstrip("+-").isdigit():
            if not blocks:
                blocks.append([])
            blocks[-1].append((line_number, fields))
            continue
        labels = [int(label) for label in _SPIN_LABEL.findall(line)]
        if labels == [1, 2] and not blocks:
            two_channels = True
        elif (
            len(labels) == 1
            and not two_channels
            and labels[0] == len(blocks) + 1 <= 2
            and (not blocks or blocks[-1])
        ):
            blocks.append([])
        elif labels or any(blocks):
            raise DescriptorError(f"{path.name}:{line_number} has an unsupported spin header")
    if not any(blocks):
        raise DescriptorError(f"no LOBSTER bond records found in {path}")
    widths = {9} if two_channels else {5, 8}
    for line_number, fields in (row for block in blocks for row in block):
        if len(fields) not in widths:
            raise DescriptorError(
                f"{path.name}:{line_number} has {len(fields)} columns; supported LOBSTER bond "
                "rows have 5 simplified columns, 8 columns including the three atom-2 "
                "lattice-translation integers, or 9 columns with both spin channels"
            )
    listed = sum(len(block) for block in blocks)
    blocks = [_atom_pair_rows(path, block, 2 if two_channels else 1) for block in blocks]
    orbital_rows = listed - sum(len(block) for block in blocks)
    if two_channels:
        return [(number, fields[:7], fields[7:]) for number, fields in blocks[0]], orbital_rows
    if len(blocks) == 1:
        return [(number, fields[:-1], fields[-1:]) for number, fields in blocks[0]], orbital_rows
    up, down = blocks
    if len(up) != len(down):
        raise DescriptorError(
            f"{path.name} lists {len(up)} spin-1 bonds but {len(down)} spin-2 bonds"
        )
    for (number, first), (other, second) in zip(up, down, strict=True):
        if first[:-1] != second[:-1]:
            raise DescriptorError(f"{path.name}:{other} does not match spin-1 line {number}")
    return [
        (number, first[:-1], [first[-1], second[-1]])
        for (number, first), (_, second) in zip(up, down, strict=True)
    ], orbital_rows


def _parse_lobster_list(path: Path, frame: Frame | None) -> DescriptorDocument:
    if frame is None:
        raise DescriptorError("LOBSTER list files require a Frame for periodic bond midpoints")
    lower_name = path.name.lower()
    family = "icobi" if "icobi" in lower_name else "icohp"
    descriptor = "icohp_ev" if family == "icohp" else family
    features: list[Feature] = []
    spin_resolved = False
    rows, orbital_rows = _lobster_rows(path)
    # LOBSTER lists a bond from an atom to its own periodic image from both ends, as
    # translations T and -T with the same distance and values. The first row is kept.
    self_image: dict[tuple[int, tuple[int, int, int]], tuple[str, list[float]]] = {}
    collapsed_rows = 0
    for line_number, fields, raw_values in rows:
        context = f"{path.name}:{line_number}"
        first = _atom_index(fields[1], f"{context} atom1")
        second = _atom_index(fields[2], f"{context} atom2")
        distance = _finite(fields[3].replace("D", "E"), f"{context} distance")
        lattice_translation: tuple[int, int, int] | None = None
        if len(fields) == 7:
            raw_translation = fields[4:7]
            if any(_INTEGER_FIELD.fullmatch(component) is None for component in raw_translation):
                raise DescriptorError(
                    f"{context} lattice translation must contain exactly three integers"
                )
            lattice_translation = (
                int(raw_translation[0]),
                int(raw_translation[1]),
                int(raw_translation[2]),
            )
        if first == second and not any(lattice_translation or ()):
            raise DescriptorError(
                f"{context} bonds {fields[1]} to itself without a nonzero lattice translation"
            )
        channels = [
            _finite(raw.replace("D", "E"), f"{context} {family.upper()}") for raw in raw_values
        ]
        if first == second and lattice_translation is not None:
            negated = (-lattice_translation[0], -lattice_translation[1], -lattice_translation[2])
            key = (first, max(lattice_translation, negated))
            if key in self_image:
                earlier, values = self_image[key]
                if not all(
                    math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-5)
                    for left, right in zip(values, (distance, *channels), strict=True)
                ):
                    raise DescriptorError(
                        f"{context} repeats the self-image bond of {earlier} with a "
                        "different distance or value"
                    )
                collapsed_rows += 1
                continue
            self_image[key] = (context, [distance, *channels])
        # The total of a spin-polarized list is the sum of both channels, which is
        # what a non-spin-polarized LOBSTER run reports for the same bond.
        value = channels[0] if len(channels) == 1 else channels[0] + channels[1]
        attributes: dict[str, Any] = {
            descriptor: value,
            "bond_length_angstrom": distance,
            "distance": distance,
            "source_line": line_number,
            "midpoint_convention": (
                "minimum_image"
                if lattice_translation is None
                else "explicit_atom2_lattice_translation"
            ),
        }
        if lattice_translation is not None:
            attributes["lattice_translation"] = list(lattice_translation)
            # neighbor_atom_indices is sorted, so record which atom the translation
            # moves: LOBSTER's second label, which may be the lower index (Cl5 Na1).
            attributes["lattice_translation_atom"] = second
        if descriptor == "icohp_ev":
            attributes["icohp"] = value
        attributes["descriptor_family"] = family
        if len(channels) == 2:
            spin_resolved = True
            attributes[f"{family}_up"] = channels[0]
            attributes[f"{family}_down"] = channels[1]
        features.append(
            Feature(
                id=f"lobster:{descriptor}:{len(features)}",
                kind="bond_descriptor",
                fractional_position=_periodic_midpoint(
                    frame,
                    first,
                    second,
                    lattice_translation=lattice_translation,
                ),
                # A bond to the atom's own periodic image lists that atom once;
                # lattice_translation names the image.
                neighbor_atom_indices=(first,) if first == second else (first, second),
                attributes=attributes,
            )
        )
    metadata: dict[str, Any] = {"native_format": path.name}
    if orbital_rows:
        metadata["skipped_orbital_rows"] = orbital_rows
    if collapsed_rows:
        metadata["collapsed_self_image_rows"] = collapsed_rows
    if spin_resolved:
        metadata["spin_convention"] = (
            f"{family} is the sum of LOBSTER spin 1 and spin 2; {family}_up and "
            f"{family}_down are spin 1 and spin 2."
        )
    return DescriptorDocument(producer="lobster", features=tuple(features), metadata=metadata)


def _acf_records(lines: Sequence[str]) -> list[tuple[float, float]]:
    """Return (CHARGE, ATOMIC VOL) for each atom row of a Bader ``ACF.dat``."""

    records: list[tuple[float, float]] = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 7 and fields[0].isdigit():
            try:
                records.append((float(fields[4]), float(fields[6])))
            except ValueError:
                continue
    return records


def _parse_atomic_charge_text(path: Path, frame: Frame | None, producer: str) -> DescriptorDocument:
    if frame is None:
        raise DescriptorError(f"{path.name} requires a Frame to map charges onto atoms")
    lines = _read_descriptor_text(path).splitlines()
    records: list[tuple[float, float | None]] = []
    if path.name.upper() == "ACF.DAT":
        records.extend(_acf_records(lines))
    else:
        start = 2 if lines and lines[0].strip().isdigit() else 0
        declared = int(lines[0]) if start else None
        for line in lines[start:]:
            fields = line.split()
            if len(fields) < 5:
                continue
            try:
                records.append((float(fields[4].replace("D", "E")), None))
            except ValueError:
                continue
        if declared is not None and len(records) < declared:
            raise DescriptorError(
                f"{path.name} declares {declared} atoms but only {len(records)} charge rows were parsed"
            )
        if declared is not None:
            records = records[:declared]
    if not records:
        raise DescriptorError(f"no atomic charge records found in {path}")
    if len(records) != len(frame.atoms):
        raise DescriptorError(
            f"{path.name} has {len(records)} charges but frame has {len(frame.atoms)} atoms"
        )
    features = tuple(
        Feature(
            id=f"{producer}:atom:{index}",
            kind="atomic_charge",
            fractional_position=_frame_position(frame, index, f"{producer} atomic charges"),
            charge=charge,
            volume=volume,
            neighbor_atom_indices=(index,),
            attributes={
                "charge_definition": (
                    "ddec_net_atomic_charge"
                    if producer == "chargemol"
                    else "bader_electron_population"
                ),
                "charge_unit": "e",
                "volume_unit": "angstrom^3" if volume is not None else None,
                "source_line_format": path.name,
            },
        )
        for index, (charge, volume) in enumerate(records)
    )
    convention = (
        "DDEC net atomic charge in elementary-charge units; positive values indicate electron deficiency."
        if producer == "chargemol"
        else "Bader ACF charge is a positive integrated electron population in elementary-charge units."
    )
    return DescriptorDocument(
        producer=producer,
        features=features,
        metadata={"native_format": path.name, "charge_convention": convention},
    )


def load_descriptor(
    path: str | Path,
    *,
    producer: str | None = None,
    frame: Frame | None = None,
) -> DescriptorDocument:
    """Load a canonical or supported native plugin descriptor.

    JSON carrying ``schema_version=electrace.features.v1`` is normalized as the
    canonical format. Official BaderKit ``bader.json``/``badelf.json`` and
    critic2 CPREPORT JSON are also accepted. LOBSTER lists, ACF.dat, and
    Chargemol charge XYZ files are recognized by filename.
    """

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise DescriptorError(f"descriptor file does not exist: {source}")
    lower_name = source.name.lower()
    if any(name in lower_name for name in ("icohplist", "icobilist")):
        return _parse_lobster_list(source, frame)
    if source.name.upper() == "ACF.DAT":
        return _parse_atomic_charge_text(source, frame, "bader")
    if "ddec" in lower_name and source.suffix.lower() in {".xyz", ".txt"}:
        return _parse_atomic_charge_text(source, frame, "chargemol")
    if source.suffix.lower() != ".json":
        raise DescriptorError(f"unsupported descriptor format: {source.name}")
    try:
        payload = json.loads(_read_descriptor_text(source))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise DescriptorError(f"cannot parse descriptor JSON {source}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise DescriptorError("descriptor JSON root must be an object")
    if "schema_version" in payload:
        return descriptor_from_dict(payload)
    selected = (producer or "").lower()
    if not selected:
        if lower_name in {"bader.json", "badelf.json"}:
            selected = "baderkit"
        elif "critic" in lower_name or isinstance(payload.get("critical_points"), Mapping):
            selected = "critic2"
    if selected == "baderkit":
        return _parse_baderkit(payload, frame, native_format=source.name)
    if selected == "critic2":
        return _parse_critic2(payload)
    raise DescriptorError(
        f"unsupported native JSON descriptor {source.name}; expected bader.json, "
        "badelf.json, or critic2 CPREPORT JSON"
    )


def dump_descriptor(
    document: DescriptorDocument,
    path: str | Path,
    *,
    indent: int = 2,
    overwrite: bool = False,
) -> Path:
    """Write canonical descriptor JSON."""

    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to replace existing descriptor: {destination}")
    payload = json.dumps(document.to_dict(), indent=indent, allow_nan=False) + "\n"
    atomic_write_text(destination, payload)
    return destination


def _normalized_semantic_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().casefold().split())
    return normalized or None


def attach_descriptors(frame: Frame, documents: Iterable[DescriptorDocument]) -> Frame:
    """Return a frame with validated features and document provenance appended."""

    features = list(frame.features)
    metadata = dict(frame.metadata)
    raw_sources = metadata.get("descriptor_producers", [])
    sources = list(raw_sources) if isinstance(raw_sources, list) else []
    raw_records = metadata.get("descriptor_documents", [])
    document_records = list(raw_records) if isinstance(raw_records, list) else []
    identifiers = {feature.id for feature in features}
    known_atom_indices = {atom.index for atom in frame.atoms}
    for document in documents:
        if document.frame_index is not None and document.frame_index != frame.index:
            raise DescriptorError(
                f"descriptor frame index {document.frame_index} does not match loaded frame {frame.index}"
            )
        if document.frame_label is not None and document.frame_label != frame.label:
            raise DescriptorError(
                f"descriptor frame label {document.frame_label!r} does not match loaded frame {frame.label!r}"
            )
        for feature in document.features:
            if feature.id in identifiers:
                raise DescriptorError(
                    f"duplicate feature id {feature.id!r} while attaching descriptors"
                )
            invalid = [
                index for index in feature.neighbor_atom_indices if index not in known_atom_indices
            ]
            if invalid:
                raise DescriptorError(
                    f"feature {feature.id!r} refers to atom indices {invalid}, but frame has {len(frame.atoms)} atoms"
                )
            declared_producer = feature.attributes.get("descriptor_producer")
            if declared_producer is not None and declared_producer != document.producer:
                raise DescriptorError(
                    f"feature {feature.id!r} declares descriptor_producer "
                    f"{declared_producer!r}, conflicting with document producer "
                    f"{document.producer!r}"
                )
            attributes = {**feature.attributes, "descriptor_producer": document.producer}
            convention = document.metadata.get("charge_convention")
            if convention is not None:
                declared_convention = feature.attributes.get("charge_convention")
                if declared_convention is not None and _normalized_semantic_text(
                    declared_convention
                ) != _normalized_semantic_text(convention):
                    raise DescriptorError(
                        f"feature {feature.id!r} declares charge_convention "
                        f"{declared_convention!r}, conflicting with document convention "
                        f"{convention!r}"
                    )
                attributes["charge_convention"] = convention
            tagged = replace(
                feature,
                attributes=attributes,
            )
            identifiers.add(tagged.id)
            features.append(tagged)
        sources.append(document.producer)
        producer: dict[str, Any] = {"name": document.producer}
        if document.producer_version is not None:
            producer["version"] = document.producer_version
        document_records.append(
            {
                "schema_version": document.schema_version,
                "producer": producer,
                "metadata": dict(document.metadata),
            }
        )
    if sources:
        metadata["descriptor_producers"] = sources
    if document_records:
        metadata["descriptor_documents"] = document_records
    return replace(frame, features=tuple(features), metadata=metadata)


SPIN_DESCRIPTOR = Path("spin", "ACF.dat")


def attach_spin_populations(frame: Frame, path: str | Path) -> Frame:
    """Attach each atom's Bader-basin magnetization from ``spin/ACF.dat`` as ``spin``.

    ``spin/ACF.dat`` is Bader's output for ``bader CHGCAR_mag -ref CHGCAR_tot``: its
    CHARGE column integrates the magnetization (spin up minus spin down) within the
    total-density basins. Values keep VASP's sign and attach only to Bader
    atom-charge features, so no other partition receives them.
    """

    source = Path(path).expanduser().resolve()
    spins = [spin for spin, _ in _acf_records(_read_descriptor_text(source).splitlines())]
    if len(spins) != len(frame.atoms):
        raise DescriptorError(
            f"{source} has {len(spins)} spin populations but frame {frame.label} has "
            f"{len(frame.atoms)} atoms"
        )
    by_atom = {atom.index: spin for atom, spin in zip(frame.atoms, spins, strict=True)}
    features = list(frame.features)
    attached = 0
    for position, feature in enumerate(features):
        definition = feature.attributes.get("charge_definition")
        if (
            feature.kind == "atomic_charge"
            and len(feature.neighbor_atom_indices) == 1
            and isinstance(definition, str)
            and definition.startswith("bader_")
        ):
            spin = by_atom[feature.neighbor_atom_indices[0]]
            features[position] = replace(feature, attributes={**feature.attributes, "spin": spin})
            attached += 1
    if not attached:
        raise DescriptorError(
            f"{source} needs Bader atom charges (ACF.dat or bader.json) in frame "
            f"{frame.label} to attach spin populations to"
        )
    metadata = dict(frame.metadata)
    metadata["descriptor_producers"] = [*metadata.get("descriptor_producers", []), "bader"]
    metadata["descriptor_sources"] = [*metadata.get("descriptor_sources", []), str(source)]
    metadata["descriptor_documents"] = [
        *metadata.get("descriptor_documents", []),
        {
            "schema_version": SCHEMA_VERSION,
            "producer": {"name": "bader"},
            "metadata": {
                "native_format": SPIN_DESCRIPTOR.as_posix(),
                "spin_convention": (
                    "spin is the Bader CHARGE of CHGCAR_mag partitioned by CHGCAR_tot: spin-up "
                    "minus spin-down electrons in each total-density basin (e, numerically "
                    "mu_B), with the sign VASP wrote."
                ),
                "atomic_spin_sum": math.fsum(spins),
            },
        },
    ]
    return replace(frame, features=tuple(features), metadata=metadata)


NET_CHARGE_SOURCES = ("POTCAR", "OUTCAR")


def _is_population(feature: Feature) -> bool:
    """Return whether *feature* is one atom's Bader or BaderKit electron population."""

    return (
        feature.kind == "atomic_charge"
        and len(feature.neighbor_atom_indices) == 1
        and feature.attributes.get("charge_definition") == "bader_electron_population"
    )


def _species_blocks(frame: Frame) -> tuple[list[str], dict[int, int]]:
    """Return the POSCAR species blocks as element symbols, and each atom's block."""

    blocks: list[str] = []
    block_of: dict[int, int] = {}
    for atom in frame.atoms:
        symbol = element_symbol(atom.element)
        if not blocks or blocks[-1] != symbol:
            blocks.append(symbol)
        block_of[atom.index] = len(blocks) - 1
    return blocks, block_of


def _frame_zval(directory: Path, blocks: list[str]) -> tuple[Path, tuple[tuple[str, float], ...]]:
    """Return the frame's ZVAL file and entries, which must match its species blocks."""

    source = next(
        (directory / name for name in NET_CHARGE_SOURCES if (directory / name).is_file()), None
    )
    if source is None:
        raise DescriptorError("no POTCAR or OUTCAR in the frame directory")
    zval = read_vasp_zval(source)
    species = [element for element, _ in zval]
    if species != blocks:
        raise DescriptorError(
            f"{source.name} species {' '.join(species)} do not match the POSCAR species "
            f"{' '.join(blocks)}, which VASP pairs by position"
        )
    return source, zval


def attach_net_charges(frame: Frame, directory: str | Path) -> Frame:
    """Attach ``net_charge`` = ZVAL - population to Bader and BaderKit atom populations.

    ZVAL comes from the first of ``POTCAR`` and ``OUTCAR`` in the frame *directory*.
    VASP pairs their entries with the POSCAR species blocks by position, so ZVAL is
    taken per block, and the two orders must agree. Otherwise nothing is attached and
    ``net_charge_unavailable`` in the frame metadata says why; ZVAL is never assumed.
    Populations stay in ``charge``. DDEC charges are already net charges, and BaderKit
    basins and oxidation states are not atom populations, so they are left alone.
    """

    if not any(_is_population(feature) for feature in frame.features):
        return frame
    blocks, block_of = _species_blocks(frame)
    try:
        source, zval = _frame_zval(Path(directory), blocks)
    except (DescriptorError, VaspParseError) as exc:
        return replace(frame, metadata={**frame.metadata, "net_charge_unavailable": str(exc)})
    features = list(frame.features)
    sums: dict[str, list[float]] = {}
    for position, feature in enumerate(features):
        if not _is_population(feature) or feature.charge is None:
            continue
        net_charge = zval[block_of[feature.neighbor_atom_indices[0]]][1] - feature.charge
        features[position] = replace(
            feature, attributes={**feature.attributes, "net_charge": net_charge}
        )
        producer = str(feature.attributes.get("descriptor_producer"))
        sums.setdefault(producer, []).append(net_charge)
    metadata = dict(frame.metadata)
    metadata["descriptor_sources"] = [*metadata.get("descriptor_sources", []), str(source)]
    metadata["descriptor_documents"] = [
        *metadata.get("descriptor_documents", []),
        {
            "schema_version": SCHEMA_VERSION,
            "producer": {"name": "vasp"},
            "metadata": {
                "native_format": source.name,
                "potcar_species": [element for element, _ in zval],
                "zval": [value for _, value in zval],
                "net_charge_convention": (
                    "net_charge is ZVAL minus the Bader electron population, in e; positive "
                    "values mean electron deficiency."
                ),
                "net_charge_sum": {
                    producer: math.fsum(values) for producer, values in sorted(sums.items())
                },
            },
        },
    ]
    return replace(frame, features=tuple(features), metadata=metadata)
