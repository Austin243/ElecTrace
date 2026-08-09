"""VASP path discovery, lightweight parsing, and plugin descriptor ingestion."""

from .descriptors import (
    MAX_DESCRIPTOR_BYTES,
    SCHEMA_VERSION,
    DescriptorDocument,
    DescriptorError,
    attach_descriptors,
    descriptor_from_dict,
    dump_descriptor,
    load_descriptor,
)
from .discovery import (
    DiscoveredPath,
    DiscoveryError,
    FrameSource,
    SeriesKind,
    discover_neb,
    discover_path,
    discover_static_series,
    pressure_from_label,
    pressure_from_outcar,
)
from .loader import load_path, load_sequence
from .vasp import (
    PoscarData,
    VaspParseError,
    parse_poscar,
    read_poscar,
    read_vasp_energy,
)

__all__ = [
    "MAX_DESCRIPTOR_BYTES",
    "SCHEMA_VERSION",
    "DescriptorDocument",
    "DescriptorError",
    "DiscoveredPath",
    "DiscoveryError",
    "FrameSource",
    "PoscarData",
    "SeriesKind",
    "VaspParseError",
    "attach_descriptors",
    "descriptor_from_dict",
    "discover_neb",
    "discover_path",
    "discover_static_series",
    "dump_descriptor",
    "load_descriptor",
    "load_path",
    "load_sequence",
    "parse_poscar",
    "pressure_from_label",
    "pressure_from_outcar",
    "read_poscar",
    "read_vasp_energy",
]
