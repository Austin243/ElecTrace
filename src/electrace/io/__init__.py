"""VASP path discovery, lightweight parsing, and plugin descriptor ingestion."""

from .descriptors import DescriptorError, descriptor_from_dict, dump_descriptor, load_descriptor
from .discovery import discover_neb, discover_static_series
from .loader import load_path, load_sequence
from .vasp import parse_poscar, read_poscar, read_vasp_energy

__all__ = [
    "DescriptorError",
    "descriptor_from_dict",
    "discover_neb",
    "discover_static_series",
    "dump_descriptor",
    "load_descriptor",
    "load_path",
    "load_sequence",
    "parse_poscar",
    "read_poscar",
    "read_vasp_energy",
]
