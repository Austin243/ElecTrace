"""Strict configuration loading for ElecTrace analyses.

TOML is deliberately used for the first release because Python 3.11 can read
it without another runtime dependency.  Fixed sections reject unknown keys so
scientific settings cannot be silently misspelled; ``[metadata]`` values and
plugin-specific options are the explicit extension points.
"""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from electrace._files import atomic_write_text

_DESCRIPTOR_SOURCES = frozenset({"auto", "local", "external", "merge"})
_SERIES_KINDS = frozenset({"auto", "neb", "static", "ordered", "pressure"})
_ATOM_IDENTITIES = frozenset({"geometric", "index"})
_ROOT_KEYS = frozenset(
    {"project", "inputs", "matching", "events", "outputs", "plugins", "metadata"}
)
_PROJECT_KEYS = frozenset({"name", "series_kind"})


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")
    return value


def _string(value: Any, name: str, *, normalize: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    stripped = value.strip()
    if not stripped or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must be a non-empty string without control characters")
    return stripped.casefold() if normalize else stripped


def _number(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and (result < minimum if minimum_inclusive else result <= minimum):
        relation = "at least" if minimum_inclusive else "greater than"
        raise ValueError(f"{name} must be {relation} {minimum:g}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be at most {maximum:g}")
    return result


def _integer(value: Any, name: str, *, minimum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    result = value
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def _validate_json(value: Any) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("configuration must contain finite JSON-compatible values") from exc


def _input_filename(value: str, field_name: str) -> str:
    """Validate a configured input filename without allowing path traversal."""

    if not isinstance(value, str):
        raise TypeError(f"inputs.{field_name} must be a string")
    name = value.strip()
    if (
        not name
        or name in {".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or "/" in name
        or "\\" in name
        or Path(name).is_absolute()
    ):
        raise ValueError(f"inputs.{field_name} must be a basename, not a path: {value!r}")
    return name


@dataclass(slots=True)
class InputConfig:
    """Input discovery settings."""

    root: str = "."
    descriptor_dir: str | None = None
    structure_name: str = "CONTCAR"
    fallback_structure_name: str = "POSCAR"
    descriptor_name: str = "features.json"
    descriptor_source: str = "auto"
    prefer_contcar: bool = True

    def __post_init__(self) -> None:
        self.root = _string(self.root, "inputs.root")
        if self.descriptor_dir is not None:
            self.descriptor_dir = _string(self.descriptor_dir, "inputs.descriptor_dir")
        self.structure_name = _input_filename(self.structure_name, "structure_name")
        self.fallback_structure_name = _input_filename(
            self.fallback_structure_name, "fallback_structure_name"
        )
        self.descriptor_name = _input_filename(self.descriptor_name, "descriptor_name")
        if not isinstance(self.descriptor_source, str):
            raise TypeError("inputs.descriptor_source must be a string")
        self.descriptor_source = self.descriptor_source.strip().lower()
        if self.descriptor_source not in _DESCRIPTOR_SOURCES:
            choices = ", ".join(sorted(_DESCRIPTOR_SOURCES))
            raise ValueError(
                f"inputs.descriptor_source must be one of {choices}; got {self.descriptor_source!r}"
            )
        if self.descriptor_source in {"external", "merge"} and self.descriptor_dir is None:
            raise ValueError(
                f"inputs.descriptor_dir is required when descriptor_source={self.descriptor_source!r}"
            )
        self.prefer_contcar = _boolean(self.prefer_contcar, "inputs.prefer_contcar")


@dataclass(slots=True)
class MatchingSettings:
    """User-facing matching weights and acceptance thresholds."""

    atom_identity: str = "geometric"
    max_atom_displacement: float = 1.5
    max_feature_displacement: float = 2.0
    position_weight: float = 1.0
    charge_weight: float = 0.35
    volume_weight: float = 0.25
    neighbor_weight: float = 0.75
    overlap_weight: float = 1.0
    descriptor_weight: float = 0.35
    descriptor_scales: dict[str, float] = field(default_factory=dict)
    match_threshold: float = 3.0
    conservation_tolerance: float = 0.35
    split_merge_threshold: float = 0.55

    def __post_init__(self) -> None:
        self.atom_identity = _string(self.atom_identity, "matching.atom_identity", normalize=True)
        if self.atom_identity not in _ATOM_IDENTITIES:
            choices = ", ".join(sorted(_ATOM_IDENTITIES))
            raise ValueError(
                f"matching.atom_identity must be one of {choices}; got {self.atom_identity!r}"
            )
        for name in (
            "max_atom_displacement",
            "max_feature_displacement",
            "match_threshold",
        ):
            setattr(
                self,
                name,
                _number(
                    getattr(self, name),
                    f"matching.{name}",
                    minimum=0.0,
                    minimum_inclusive=False,
                ),
            )
        for name in (
            "position_weight",
            "charge_weight",
            "volume_weight",
            "neighbor_weight",
            "overlap_weight",
            "descriptor_weight",
        ):
            setattr(
                self,
                name,
                _number(getattr(self, name), f"matching.{name}", minimum=0.0),
            )
        if not isinstance(self.descriptor_scales, dict):
            raise TypeError("matching.descriptor_scales must be a table")
        self.descriptor_scales = {
            _string(key, "matching.descriptor_scales key"): _number(
                value,
                f"matching.descriptor_scales.{key}",
                minimum=0.0,
                minimum_inclusive=False,
            )
            for key, value in self.descriptor_scales.items()
        }
        self.split_merge_threshold = _number(
            self.split_merge_threshold,
            "matching.split_merge_threshold",
            minimum=0.0,
            maximum=1.0,
        )
        self.conservation_tolerance = _number(
            self.conservation_tolerance,
            "matching.conservation_tolerance",
            minimum=0.0,
            maximum=1.0,
        )


@dataclass(slots=True)
class EventSettings:
    """Temporal filters used to suppress one-frame numerical noise."""

    minimum_persistence: int = 2
    minimum_event_confidence: float = 0.10
    charge_change_threshold: float = 0.10
    volume_change_fraction: float = 0.20
    descriptor_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "icohp": 0.5,
            "icobi": 0.1,
            "bond_order": 0.1,
            "elf": 0.1,
            "oxidation_state": 0.5,
        }
    )
    high_confidence_threshold: float = 0.80
    medium_confidence_threshold: float = 0.55

    def __post_init__(self) -> None:
        self.minimum_persistence = _integer(
            self.minimum_persistence, "events.minimum_persistence", minimum=1
        )
        self.minimum_event_confidence = _number(
            self.minimum_event_confidence,
            "events.minimum_event_confidence",
            minimum=0.0,
            maximum=1.0,
        )
        self.charge_change_threshold = _number(
            self.charge_change_threshold,
            "events.charge_change_threshold",
            minimum=0.0,
            minimum_inclusive=False,
        )
        self.volume_change_fraction = _number(
            self.volume_change_fraction,
            "events.volume_change_fraction",
            minimum=0.0,
            minimum_inclusive=False,
        )
        if not isinstance(self.descriptor_thresholds, dict):
            raise TypeError("events.descriptor_thresholds must be a table")
        self.descriptor_thresholds = {
            _string(key, "events.descriptor_thresholds key"): _number(
                value,
                f"events.descriptor_thresholds.{key}",
                minimum=0.0,
                minimum_inclusive=False,
            )
            for key, value in self.descriptor_thresholds.items()
        }
        self.high_confidence_threshold = _number(
            self.high_confidence_threshold,
            "events.high_confidence_threshold",
            minimum=0.0,
            maximum=1.0,
        )
        self.medium_confidence_threshold = _number(
            self.medium_confidence_threshold,
            "events.medium_confidence_threshold",
            minimum=0.0,
            maximum=1.0,
        )
        if self.medium_confidence_threshold > self.high_confidence_threshold:
            raise ValueError(
                "confidence thresholds must satisfy "
                "0 <= medium_confidence_threshold <= high_confidence_threshold <= 1"
            )


@dataclass(slots=True)
class OutputConfig:
    """Output selection for an analysis run."""

    directory: str = "electrace-results"
    html_report: bool = True
    json: bool = True
    csv: bool = True
    # Zero-based POSCAR indices; an empty list reports every feature.
    focus_atoms: list[int] = field(default_factory=list)
    # False leaves each frame's feature list and the match edges out of
    # analysis.json and the report; tracks, events, and summaries are unchanged.
    match_details: bool = True

    def __post_init__(self) -> None:
        self.directory = _string(self.directory, "outputs.directory")
        for name in ("html_report", "json", "csv", "match_details"):
            setattr(self, name, _boolean(getattr(self, name), f"outputs.{name}"))
        if not isinstance(self.focus_atoms, (list, tuple)):
            raise TypeError("outputs.focus_atoms must be an array of atom indices")
        for index in self.focus_atoms:
            _integer(index, "each outputs.focus_atoms entry", minimum=0)
        if len(set(self.focus_atoms)) != len(self.focus_atoms):
            raise ValueError("outputs.focus_atoms must not repeat an atom index")
        self.focus_atoms = sorted(self.focus_atoms)


@dataclass(slots=True)
class ElecTraceConfig:
    """Complete ElecTrace configuration."""

    project_name: str = "ElecTrace analysis"
    series_kind: str = "auto"
    inputs: InputConfig = field(default_factory=InputConfig)
    matching: MatchingSettings = field(default_factory=MatchingSettings)
    events: EventSettings = field(default_factory=EventSettings)
    outputs: OutputConfig = field(default_factory=OutputConfig)
    plugins: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.project_name = _string(self.project_name, "project.name")
        self.series_kind = _string(self.series_kind, "project.series_kind", normalize=True)
        if self.series_kind not in _SERIES_KINDS:
            choices = ", ".join(sorted(_SERIES_KINDS))
            raise ValueError(f"project.series_kind must be one of {choices}")
        for name, value, expected in (
            ("inputs", self.inputs, InputConfig),
            ("matching", self.matching, MatchingSettings),
            ("events", self.events, EventSettings),
            ("outputs", self.outputs, OutputConfig),
        ):
            if not isinstance(value, expected):
                raise TypeError(f"configuration.{name} must be a {expected.__name__}")
        plugins: dict[str, dict[str, Any]] = {}
        for name, options in self.plugins.items():
            normalized_name = _string(name, "plugin name", normalize=True)
            if not isinstance(options, dict):
                raise TypeError(f"[plugins.{normalized_name}] must be a TOML table")
            if "enabled" in options:
                _boolean(options["enabled"], f"plugins.{normalized_name}.enabled")
            for key in ("executable", "capability"):
                if key in options:
                    _string(options[key], f"plugins.{normalized_name}.{key}")
            plugins[normalized_name] = dict(options)
        self.plugins = plugins
        if not isinstance(self.metadata, dict):
            raise TypeError("[metadata] must be a TOML table")
        self.metadata = dict(self.metadata)
        _validate_json(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable nested mapping."""

        result = asdict(self)
        # At their defaults, record the configuration exactly as before these options existed.
        if not self.outputs.focus_atoms:
            del result["outputs"]["focus_atoms"]
        if self.outputs.match_details:
            del result["outputs"]["match_details"]
        _validate_json(result)
        return result


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def _reject_unknown_keys(data: dict[str, Any], allowed: frozenset[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{context} contains unknown keys: {', '.join(unknown)}")


def load_config(path: str | Path) -> ElecTraceConfig:
    """Load and validate an ElecTrace TOML configuration file."""

    config_path = Path(path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    _reject_unknown_keys(data, _ROOT_KEYS, "configuration root")
    project = _section(data, "project")
    _reject_unknown_keys(project, _PROJECT_KEYS, "[project]")
    return ElecTraceConfig(
        project_name=project.get("name", "ElecTrace analysis"),
        series_kind=project.get("series_kind", "auto"),
        inputs=InputConfig(**_section(data, "inputs")),
        matching=MatchingSettings(**_section(data, "matching")),
        events=EventSettings(**_section(data, "events")),
        outputs=OutputConfig(**_section(data, "outputs")),
        plugins={str(k): dict(v) for k, v in _section(data, "plugins").items()},
        metadata=dict(_section(data, "metadata")),
    )


DEFAULT_CONFIG = """# ElecTrace 0.1 configuration
[project]
name = "ElecTrace analysis"
series_kind = "auto"  # auto, neb, pressure, or ordered

[inputs]
root = "."
structure_name = "CONTCAR"
fallback_structure_name = "POSCAR"
descriptor_name = "features.json"
descriptor_source = "auto" # local without descriptor_dir; external with it
prefer_contcar = true

[matching]
atom_identity = "geometric" # or "index" for one fixed atom order, as in NEB images
max_atom_displacement = 1.5 # geometric identity only
max_feature_displacement = 2.0
position_weight = 1.0
charge_weight = 0.35
volume_weight = 0.25
neighbor_weight = 0.75
overlap_weight = 1.0
descriptor_weight = 0.35
match_threshold = 3.0
conservation_tolerance = 0.35
split_merge_threshold = 0.55

# Optional scalar descriptors participate in correspondence only when their
# characteristic scales are declared, for example:
# [matching.descriptor_scales]
# icohp = 0.5
# icobi = 0.1

[events]
minimum_persistence = 2
minimum_event_confidence = 0.10
charge_change_threshold = 0.10
volume_change_fraction = 0.20
high_confidence_threshold = 0.80
medium_confidence_threshold = 0.55

[events.descriptor_thresholds]
icohp = 0.5
icobi = 0.1
bond_order = 0.1
elf = 0.1
oxidation_state = 0.5
# spin = 0.1 # per-atom spin population from spin/ACF.dat (ISPIN = 2 runs)

[outputs]
directory = "electrace-results"
html_report = true
json = true
csv = true
# Report only features tied to these 0-based POSCAR atom indices; matching
# still uses every atom and feature.
# focus_atoms = [0, 2]
# Leave each frame's feature list and the match edges out of analysis.json and
# report.html; tracks, events, and summaries are unchanged.
# match_details = false

[plugins.critic2]
enabled = true
executable = "critic2"

[plugins.baderkit]
enabled = true
executable = "baderkit"

[plugins.bader]
enabled = false
executable = "bader"

[plugins.lobster]
enabled = false
executable = "lobster"

[plugins.chargemol]
enabled = false
executable = "chargemol"

[metadata]
system = ""
method = ""
notes = ""
"""


def write_default_config(
    path: str | Path,
    *,
    input_root: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Atomically write a starter configuration without following a leaf symlink."""

    config_path = Path(path)
    if config_path.is_symlink():
        raise OSError(f"refusing to write configuration through a symbolic link: {config_path}")
    if config_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to replace existing configuration: {config_path}")
    document = DEFAULT_CONFIG
    if input_root is not None:
        encoded_root = json.dumps(str(input_root))
        document = document.replace('root = "."', f"root = {encoded_root}", 1)
    atomic_write_text(config_path, document)
    return config_path
