"""Path figures drawn from an ElecTrace analysis.

One figure stacks, on a shared frame axis, the energy profile; the density at the
critical points of chosen bonds over their interatomic distances; and the net charges
over the spins of chosen atoms. A dashed line marks the highest-energy frame, and a
filled dot between two frames marks a step on which ElecTrace reports an event for that
bond or atom and quantity. Reporting only: values are read from ``analysis.json`` as
written, and a value missing from a frame is a gap, never zero. Drawing needs the
optional matplotlib dependency (``electrace[plot]``).
"""

from __future__ import annotations

import importlib.util
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from electrace import __version__
from electrace._files import atomic_output_path
from electrace.bonds import _unwrapping_shifts
from electrace.events import _as_finite_float
from electrace.geometry import minimum_image_displacement
from electrace.models import Frame, Sequence

if TYPE_CHECKING:
    from matplotlib.axes import Axes

FORMATS = ("pdf", "svg", "png")
PNG_DPI = 600
# Each panel's quantity and axis label; --ylim names these quantities.
AXIS_LABELS = {
    "energy": r"$\Delta E$ (eV)",
    "rho": r"$\rho_\mathrm{BCP}$ (a.u.)",
    "distance": "Distance (Å)",
    "charge": "Net charge (e)",
    "spin": r"Spin ($\mu_\mathrm{B}$)",
}
# Panel groups, top to bottom: the energy, the chosen bonds, and the chosen atoms.
GROUPS = (("energy",), ("rho", "distance"), ("charge", "spin"))
OWNER = {"rho": "bond", "distance": "bond", "charge": "atom", "spin": "atom"}
HEIGHTS = {"energy": 1.05, "rho": 0.82, "distance": 0.82, "charge": 0.82, "spin": 0.82}
WIDTH = 3.42  # inches: one PNAS column, 8.7 cm
# Open markers and colors after Sun et al., PNAS 120, e2218405120 (2023).
COLORS = ("#e8251f", "#2e8b57", "#f28e1c", "#2747c8", "#8e44ad", "#00a3b4", "#8c564b", "#7f7f7f")
MARKERS = ("o", "s", "^", "D", "v", "p", "h", "<")
STYLE = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
    "mathtext.fontset": "custom",
    "mathtext.rm": "sans",
    "mathtext.it": "sans:italic",
    "mathtext.cal": "sans",
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.linewidth": 0.6,
    "axes.formatter.useoffset": False,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.major.size": 2.8,
    "ytick.major.size": 2.8,
    "xtick.minor.size": 1.6,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "xtick.minor.width": 0.5,
    "legend.fontsize": 7.5,
    "lines.linewidth": 1.0,
    "lines.markersize": 4.0,
    "lines.markeredgewidth": 0.9,
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
    "svg.hashsalt": "electrace",
}

_ATOM = re.compile(r"([A-Z][a-z]?)?(\d+)")
_BOND_ID = re.compile(r"B\d+")


@dataclass(slots=True)
class _Series:
    """One bond image or atom: a value per frame for each quantity, NaN where missing."""

    label: str
    record: dict[str, Any]
    values: dict[str, list[float]]
    # Every value of a frame that holds several; the line breaks at that frame.
    extras: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    features: set[tuple[int, str]] = field(default_factory=set)
    events: list[tuple[Mapping[str, Any], list[str]]] = field(default_factory=list)


def _common_elements(frames: list[Frame]) -> dict[int, str]:
    """Return each atom index that every frame lists, with one element throughout."""

    common = {atom.index: atom.element for atom in frames[0].atoms}
    for frame in frames[1:]:
        current = {atom.index: atom.element for atom in frame.atoms}
        common = {index: name for index, name in common.items() if current.get(index) == name}
    return common


def _atom(token: str, elements: Mapping[int, str]) -> int:
    match = _ATOM.fullmatch(token.strip())
    if match is None:
        raise ValueError(f"cannot read atom {token!r}: give a 0-based index such as 41 or C41")
    symbol, index = match.group(1), int(match.group(2))
    if index not in elements:
        raise ValueError(f"atom {index} is not an atom of every frame with one element")
    if symbol is not None and symbol != elements[index]:
        raise ValueError(f"atom {index} is {elements[index]}, not {symbol}")
    return index


def _bond(token: str, elements: Mapping[int, str]) -> tuple[int, int] | str:
    text = token.strip()
    if _BOND_ID.fullmatch(text):
        return text
    parts = re.split(r"[-–—]", text)
    if len(parts) != 2:
        raise ValueError(
            f"cannot read bond {token!r}: give two atoms such as C41-H227, or a bond_id "
            "such as B00334"
        )
    first, second = sorted(_atom(part, elements) for part in parts)
    return first, second


def parse_limits(items: Iterable[str]) -> dict[str, tuple[float, float]]:
    """Read ``QUANTITY=LOW:HIGH`` axis limits, such as ``rho=0:0.4``."""

    limits: dict[str, tuple[float, float]] = {}
    for item in items:
        name, _, span = item.partition("=")
        low, separator, high = span.partition(":")
        try:
            values = (float(low), float(high))
        except ValueError:
            values = (math.nan, math.nan)
        name = name.strip().lower()
        if name not in AXIS_LABELS or not separator or not values[0] < values[1]:
            raise ValueError(
                f"cannot read --ylim {item!r}: give QUANTITY=LOW:HIGH with LOW < HIGH, "
                f"where QUANTITY is one of {', '.join(AXIS_LABELS)}"
            )
        limits[name] = values
    return limits


def _per_frame(
    points: Iterable[tuple[int | None, float | None]], count: int
) -> tuple[list[float], list[tuple[int, float]]]:
    """Return one value per frame, NaN where missing, and every value of crowded frames.

    A frame with several values keeps all of them as separate points and none on the
    line, so the line breaks there instead of choosing one.
    """

    found: dict[int, list[float]] = {}
    for position, value in points:
        if position is not None and value is not None:
            found.setdefault(position, []).append(value)
    line = [math.nan] * count
    extras: list[tuple[int, float]] = []
    for position, values in sorted(found.items()):
        if len(values) == 1:
            line[position] = values[0]
        else:
            extras.extend((position, value) for value in values)
    return line, extras


def _labels_at(labels: list[str], positions: Iterable[int]) -> str:
    return ", ".join(labels[position] for position in sorted(set(positions)))


def _bond_series(
    choices: list[tuple[int, int] | str],
    analysis: Mapping[str, Any],
    frames: list[Frame],
    elements: Mapping[int, str],
    focus: set[int],
    warnings: list[str],
) -> list[_Series]:
    """Return one series per chosen bond and periodic image (``bond_id``)."""

    rows: list[Mapping[str, Any]] = list(analysis.get("bonds") or [])
    if "bonds" not in analysis:
        warnings.append(
            "The analysis has no bond view (`bonds`), so bonds have distances but no "
            "critical-point densities; analyze again with this ElecTrace version to add it."
        )
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(str(row["bond_id"]), []).append(row)
    ids_of: dict[tuple[int, int], list[str]] = {}
    for key, members in by_id.items():
        ids_of.setdefault((int(members[0]["atom_a"]), int(members[0]["atom_b"])), []).append(key)
    wanted: list[tuple[tuple[int, int], str | None]] = []
    for choice in choices:
        if isinstance(choice, str):
            if choice not in by_id:
                raise ValueError(f"no bond rows have bond_id {choice}")
            row = by_id[choice][0]
            wanted.append(((int(row["atom_a"]), int(row["atom_b"])), choice))
            continue
        name = f"{elements[choice[0]]}{choice[0]}–{elements[choice[1]]}{choice[1]}"
        ids = ids_of.get(choice, [])
        if not ids and focus and not focus.intersection(choice):
            warnings.append(
                f"{name} is not drawn: neither atom is a focus atom, so the analysis leaves "
                "its bond rows out. Analyze without focus_atoms, or with one of its atoms in "
                "the focus, to draw it."
            )
            continue
        if not ids and choice[0] == choice[1]:
            raise ValueError(f"{name} has no bond rows to place the atom's periodic image")
        if not ids and rows:
            warnings.append(
                f"No feature ties {name} in any frame, so it has a distance but no "
                "critical-point density."
            )
        if len(ids) > 1:
            warnings.append(
                f"{name} has bond rows for {len(ids)} periodic images ({', '.join(ids)}); "
                "each is drawn separately and labelled with its bond_id."
            )
        wanted.extend([(choice, item) for item in ids] or [(choice, None)])

    labels = [frame.label for frame in frames]
    position = {frame.index: number for number, frame in enumerate(frames)}
    shifts = _unwrapping_shifts(Sequence(tuple(frames)))
    series: list[_Series] = []
    for (first, second), bond_id in dict.fromkeys(wanted):
        members = by_id[bond_id] if bond_id is not None else []
        name = f"{elements[first]}{first}–{elements[second]}{second}"
        if bond_id is not None and len(ids_of[(first, second)]) > 1:
            name = f"{name} ({bond_id})"
        # The distance of the rows' periodic image in every frame, following atoms that
        # wrap across the cell as the bond view does; without rows, the minimum image.
        image = None
        if members:
            at = position[int(members[0]["frame"])]
            image = np.asarray(members[0]["lattice_translation"], dtype=float) + np.subtract(
                shifts[at][first], shifts[at][second]
            )
        distances: list[float] = []
        for number, frame in enumerate(frames):
            cell = np.asarray(frame.cell, dtype=float)
            left = np.asarray(frame.atom_by_index(first).fractional_position, dtype=float)
            right = np.asarray(frame.atom_by_index(second).fractional_position, dtype=float)
            if image is None:
                vector = minimum_image_displacement(left, right, cell)
            else:
                local = image - np.subtract(shifts[number][first], shifts[number][second])
                vector = (right - left + local) @ cell
            distances.append(float(np.linalg.norm(vector)))
        for row in members:
            given = _as_finite_float(row.get("distance"))
            computed = distances[position[int(row["frame"])]]
            if given is None or abs(given - computed) > 1e-6:
                warnings.append(
                    f"{name}: the bond row of frame {row['frame_label']} gives distance "
                    f"{given} Å, but the frame geometry gives {computed:.6f} Å."
                )
        rho, extras = _per_frame(
            (
                (position.get(int(row["frame"])), _as_finite_float(row.get("field")))
                for row in members
            ),
            len(frames),
        )
        if extras:
            warnings.append(
                f"{name} has several bond rows with a density in frame(s) "
                f"{_labels_at(labels, (at for at, _ in extras))}; each is drawn as a point "
                "and the line breaks there."
            )
        elif members and all(math.isnan(value) for value in rho):
            warnings.append(
                f"The bond rows of {name} carry no critical-point density (`field`), so "
                "only its distance is drawn."
            )
        with_rows = sorted({position[int(row["frame"])] for row in members})
        series.append(
            _Series(
                label=name,
                record={
                    "bond_id": bond_id,
                    "atoms": [first, second],
                    "frames_with_rows": [labels[at] for at in with_rows],
                },
                values={"rho": rho, "distance": distances},
                extras={"rho": extras},
                features={
                    (int(row["frame"]), str(feature))
                    for row in members
                    for feature in row.get("feature_ids", [])
                },
            )
        )
    return series


def _net_charge(observation: Mapping[str, Any]) -> float | None:
    attributes = observation.get("attributes", {})
    value = _as_finite_float(attributes.get("net_charge"))
    if value is None and attributes.get("charge_definition") == "ddec_net_atomic_charge":
        # DDEC charges are already net charges.
        return _as_finite_float(observation.get("charge"))
    return value


def _spin(observation: Mapping[str, Any]) -> float | None:
    return _as_finite_float(observation.get("attributes", {}).get("spin"))


def _atom_series(
    choices: list[int],
    analysis: Mapping[str, Any],
    frames: list[Frame],
    elements: Mapping[int, str],
    focus: set[int],
    warnings: list[str],
) -> list[_Series]:
    """Return one series per chosen atom and charge producer, from the atom tracks."""

    by_atom: dict[int, dict[str, list[Mapping[str, Any]]]] = {}
    for item in analysis.get("tracks", []):
        atoms = item.get("neighbor_atom_indices", [])
        if item.get("kind") != "atomic_charge" or len(atoms) != 1:
            continue
        producer = item.get("attributes", {}).get("descriptor_producer")
        producer = str(producer or str(item["feature_id"]).split(":", 1)[0])
        by_atom.setdefault(int(atoms[0]), {}).setdefault(producer, []).append(item)
    labels = [frame.label for frame in frames]
    position = {frame.index: number for number, frame in enumerate(frames)}
    series: list[_Series] = []
    for atom in dict.fromkeys(choices):
        name = f"{elements[atom]}{atom}"
        producers = by_atom.get(atom, {})
        if not producers:
            warnings.append(
                f"{name} is not drawn: it is not a focus atom, so the analysis leaves its "
                "charges out. Analyze without focus_atoms, or with the atom in the focus, to "
                "draw it."
                if focus and atom not in focus
                else f"{name} is not drawn: the analysis has no atom charge for it."
            )
            continue
        for producer, observations in producers.items():
            label = f"{name} ({producer})" if len(producers) > 1 else name
            item = _Series(
                label=label,
                record={"atom": atom, "producer": producer},
                values={},
                features={(int(obs["frame"]), str(obs["feature_id"])) for obs in observations},
            )
            for quantity, read in (("charge", _net_charge), ("spin", _spin)):
                item.values[quantity], item.extras[quantity] = _per_frame(
                    ((position.get(int(obs["frame"])), read(obs)) for obs in observations),
                    len(frames),
                )
                if item.extras[quantity]:
                    crowded = _labels_at(labels, (at for at, _ in item.extras[quantity]))
                    warnings.append(
                        f"{label} has several {quantity} values in frame(s) {crowded}; each "
                        "is drawn as a point and the line breaks there."
                    )
            series.append(item)
    reasons = sorted(
        {
            str(frame.metadata["net_charge_unavailable"])
            for frame in frames
            if "net_charge_unavailable" in frame.metadata
        }
    )
    for quantity, what in (("charge", "net charge"), ("spin", "spin")):
        gaps = {
            number
            for item in series
            for number, value in enumerate(item.values[quantity])
            if math.isnan(value) and all(at != number for at, _ in item.extras[quantity])
        }
        if not gaps:
            continue
        if all(all(math.isnan(value) for value in item.values[quantity]) for item in series):
            note = f"No chosen atom has a {what}, so the figure has no {what} panel."
        else:
            note = (
                f"Chosen atoms lack a {what} in frame(s) {_labels_at(labels, gaps)}; their "
                "lines have gaps there."
            )
        if quantity == "charge" and reasons:
            note += f" The frames give: {'; '.join(reasons)}."
        warnings.append(note)
    kept = [
        any(
            item.extras[name] or not all(map(math.isnan, item.values[name])) for name in item.values
        )
        for item in series
    ]
    if any(kept):
        warnings.extend(
            f"{item.label} is not drawn: it has no net charge or spin in any frame."
            for item, keep in zip(series, kept, strict=True)
            if not keep
        )
    return [item for item, keep in zip(series, kept, strict=True) if keep]


def _marked_on(event: Mapping[str, Any], kind: str) -> list[str]:
    """Return the panels on which an event of a chosen bond or atom is marked.

    A bond critical point that appears, vanishes, changes neighbors, or changes density
    is marked on the bond's density; an atom's charge or spin change, on that quantity.
    Other changes, such as a Laplacian, ICOHP, or volume alone, are listed, not drawn.
    """

    evidence = event.get("evidence") or {}
    changed = set(evidence.get("descriptor_changes") or {})
    if kind == "bond":
        return ["rho"] if event.get("type") != "property_change" or "field" in changed else []
    if event.get("type") != "property_change":
        return []
    marked = ["charge"] if "charge_delta" in evidence or "net_charge" in changed else []
    return marked + (["spin"] if "spin" in changed else [])


def _attach_events(
    series: Mapping[str, list[_Series]], events: Iterable[Mapping[str, Any]]
) -> None:
    """Give each series the events whose source or target feature is one of its own."""

    owners: dict[tuple[int, str], list[tuple[str, _Series]]] = {}
    for kind, items in series.items():
        for item in items:
            for key in item.features:
                owners.setdefault(key, []).append((kind, item))
    for event in events:
        keys = [
            *((int(event["frame_before"]), str(item)) for item in event.get("source_ids", [])),
            *((int(event["frame_after"]), str(item)) for item in event.get("target_ids", [])),
        ]
        touched = {id(item): (kind, item) for key in keys for kind, item in owners.get(key, [])}
        for kind, item in touched.values():
            item.events.append((event, _marked_on(event, kind)))


def _gid(*parts: str) -> str:
    """Return an SVG group id, so each series can be found when editing the figure."""

    return re.sub(r"[^A-Za-z0-9_.:-]", "_", ":".join(parts).replace("–", "-"))


def _output_paths(output: str | Path) -> list[Path]:
    stem = Path(output).expanduser()
    if stem.suffix.lower().lstrip(".") in FORMATS:
        stem = stem.with_suffix("")
    return [stem.parent / f"{stem.name}.{suffix}" for suffix in FORMATS]


def _json_values(values: list[float]) -> list[float | None]:
    return [None if math.isnan(value) else value for value in values]


def write_path_figure(
    analysis: Mapping[str, Any],
    output: str | Path,
    *,
    bonds: Iterable[str] = (),
    atoms: Iterable[str] = (),
    limits: Mapping[str, tuple[float, float]] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Draw *analysis* as a path figure and write it as PDF, SVG, and a 600 dpi PNG.

    *bonds* name atom pairs (``C41-H227`` or ``41-227``) or bond IDs (``B00334``) and
    *atoms* name atoms (``H227`` or ``227``), by 0-based POSCAR index. *output* is the
    file path without its suffix. Returns the files, the plotted values (null where
    missing), each series' events with the panels that mark them, and warnings.
    """

    if importlib.util.find_spec("matplotlib") is None:
        raise ImportError('Path figures need matplotlib: python -m pip install "electrace[plot]"')
    paths = _output_paths(output)
    existing = [str(path) for path in paths if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"{', '.join(existing)} already exist; use --force to replace them")
    if not analysis.get("frames"):
        raise ValueError("the analysis has no frames")
    # Only atoms and cells are needed; feature lists can be large or absent.
    frames = [Frame.from_dict({**item, "features": []}) for item in analysis["frames"]]
    elements = _common_elements(frames)
    bond_choices = list(dict.fromkeys(_bond(token, elements) for token in bonds))
    atom_choices = [_atom(token, elements) for token in atoms]
    if not bond_choices and not atom_choices:
        raise ValueError("choose at least one bond with --bond or atom with --atom")
    focus = {int(item["index"]) for item in (analysis.get("focus") or {}).get("atoms", [])}
    labels = [frame.label for frame in frames]
    position = {frame.index: number for number, frame in enumerate(frames)}
    warnings: list[str] = []

    known = [number for number, frame in enumerate(frames) if frame.energy is not None]
    energy: list[float] | None = None
    if not known:
        warnings.append("No frame has an energy, so the figure has no energy panel.")
    else:
        reference = cast(float, frames[known[0]].energy)
        energy = [math.nan if item.energy is None else item.energy - reference for item in frames]
        if known[0]:
            warnings.append(
                f"Frame {labels[0]} has no energy, so energies are relative to frame "
                f"{labels[known[0]]}."
            )
        if len(known) < len(frames):
            missing = _labels_at(labels, set(range(len(frames))) - set(known))
            warnings.append(f"No energy for frame(s) {missing}; the profile has gaps there.")
    barrier = analysis.get("barrier")
    peaks: list[int] = []
    if not isinstance(barrier, Mapping):
        barrier = {}
        warnings.append("The analysis has no barrier record, so no peak is marked.")
    else:
        peaks = sorted(position[int(index)] for index in barrier.get("frames", []))
        if not peaks and barrier.get("reason"):
            warnings.append(f"No peak is marked. {barrier['reason']}")

    series = {
        "bond": _bond_series(bond_choices, analysis, frames, elements, focus, warnings),
        "atom": _atom_series(atom_choices, analysis, frames, elements, focus, warnings),
    }
    _attach_events(series, analysis.get("events", []))
    present = {"energy": energy is not None} | {
        name: any(
            item.extras.get(name) or not all(math.isnan(value) for value in item.values[name])
            for item in series[kind]
        )
        for name, kind in OWNER.items()
    }
    if series["bond"] and not present["rho"]:
        warnings.append(
            "No chosen bond has a critical-point density, so the figure has no density panel."
        )
    groups = [[name for name in group if present[name]] for group in GROUPS]
    groups = [group for group in groups if group]
    if not any(name != "energy" for group in groups for name in group):
        raise ValueError(
            "none of the chosen bonds or atoms has values in this analysis. " + " ".join(warnings)
        )

    _render(
        paths,
        groups,
        labels,
        str((analysis.get("project") or {}).get("series_kind", "")),
        energy,
        peaks,
        barrier,
        series,
        position,
        dict(limits or {}),
    )

    def record(item: _Series, quantities: Mapping[str, str]) -> dict[str, Any]:
        return {
            "label": item.label,
            **item.record,
            **{key: _json_values(item.values[name]) for key, name in quantities.items()},
            "events": [
                {
                    "event_id": event.get("event_id"),
                    "type": event.get("type"),
                    "frame_before": labels[position[int(event["frame_before"])]],
                    "frame_after": labels[position[int(event["frame_after"])]],
                    "barrier_position": event.get("barrier_position"),
                    "marked_on": [name for name in marked if present[name]],
                }
                for event, marked in item.events
            ],
        }

    return {
        "files": [str(path) for path in paths],
        "frames": labels,
        "panels": [name for group in groups for name in group],
        "energy": None
        if energy is None
        else {"reference_frame": labels[known[0]], "relative_ev": _json_values(energy)},
        "barrier": {
            "frames": [labels[number] for number in peaks],
            "rise_from_first": barrier.get("rise_from_first") if peaks else None,
        },
        "bonds": [record(item, {"rho": "rho", "distance": "distance"}) for item in series["bond"]],
        "atoms": [
            record(item, {"net_charge": "charge", "spin": "spin"}) for item in series["atom"]
        ],
        "warnings": warnings,
    }


def _render(
    paths: list[Path],
    groups: list[list[str]],
    labels: list[str],
    series_kind: str,
    energy: list[float] | None,
    peaks: list[int],
    barrier: Mapping[str, Any],
    series: Mapping[str, list[_Series]],
    position: Mapping[int, int],
    limits: Mapping[str, tuple[float, float]],
) -> None:
    """Lay out and draw the panels, then write each of *paths* in its format."""

    import matplotlib as mpl
    from matplotlib.figure import Figure
    from matplotlib.layout_engine import ConstrainedLayoutEngine
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FixedLocator, FuncFormatter, MaxNLocator

    names = [name for group in groups for name in group]
    count = len(labels)
    shown = [str(int(label)) if label.isdigit() else label for label in labels]
    # Labelled frames in steps of 1, 2, 5, 10, 20, ...: at most six intervals, and
    # fewer for long labels, at about 0.06 inch per character on a 2.2 inch axis.
    most = max(1, min(6, int(2.2 / (0.06 * max(map(len, shown)) + 0.12)) - 1))
    step = 1
    while (count - 1) / step > most:
        step = step * 5 // 2 if str(step)[0] == "2" else step * 2

    def tick(value: float, _: object) -> str:
        number = round(value)
        return shown[number] if abs(value - number) < 1e-9 and 0 <= number < count else ""

    # Bonds and atoms never share a color; each group starts its own marker cycle.
    styles: dict[str, list[dict[str, Any]]] = {
        kind: [
            {
                "color": COLORS[(start + number) % len(COLORS)],
                "marker": MARKERS[number % len(MARKERS)],
                "markerfacecolor": "white",
            }
            for number in range(len(series[kind]))
        ]
        for kind, start in (("bond", 0), ("atom", len(series["bond"])))
    }
    x = np.arange(count, dtype=float)
    with mpl.rc_context(cast(Any, STYLE)):
        figure = Figure(
            figsize=(WIDTH, sum(HEIGHTS[name] for name in names) + 0.45 + 0.2 * len(groups)),
            layout=ConstrainedLayoutEngine(h_pad=0.02, w_pad=0.03, hspace=0.0, wspace=0.0),
        )
        # One column holds every panel, so all share one plot width; a group's letter
        # above its first panel widens the gap between groups.
        grid = figure.add_gridspec(len(names), 1, height_ratios=[HEIGHTS[n] for n in names])
        axes: dict[str, Axes] = {}
        for row, name in enumerate(names):
            axes[name] = figure.add_subplot(grid[row], sharex=axes[names[0]] if row else None)
        for name, ax in axes.items():
            if name == "energy" and energy is not None:
                ax.plot(
                    x,
                    energy,
                    color="black",
                    marker="o",
                    markersize=3.6,
                    markerfacecolor="black",
                    gid="energy",
                )
            kind = OWNER.get(name, "")
            for item, style in zip(series.get(kind, []), styles.get(kind, []), strict=True):
                values = item.values[name]
                ax.plot(x, values, gid=_gid(name, item.label), **style)
                extra = item.extras.get(name)
                if extra:
                    ax.plot(
                        [at for at, _ in extra],
                        [value for _, value in extra],
                        linestyle="none",
                        gid=_gid(name, item.label, "several"),
                        **style,
                    )
                # A filled dot between two frames marks an event on this quantity.
                dots: set[tuple[float, float]] = set()
                for event, marked in item.events:
                    before = position.get(int(event["frame_before"]))
                    after = position.get(int(event["frame_after"]))
                    if name not in marked or before is None or after is None:
                        continue
                    ends = [values[at] for at in (before, after) if not math.isnan(values[at])]
                    if ends:
                        dots.add(((before + after) / 2, sum(ends) / len(ends)))
                if dots:
                    ax.plot(
                        *zip(*sorted(dots), strict=True),
                        linestyle="none",
                        marker="o",
                        markersize=3.4,
                        markeredgewidth=0.0,
                        color=style["color"],
                        zorder=3,
                        gid=_gid(name, item.label, "events"),
                    )
            if name == "spin":
                ax.axhline(0.0, color="0.6", linewidth=0.5, linestyle=(0, (1, 1.5)), zorder=0.5)
            for peak in peaks:
                ax.axvline(
                    peak,
                    color="0.45",
                    linewidth=0.7,
                    linestyle=(0, (4, 2.5)),
                    zorder=0.5,
                    gid="peak",
                )
            ax.set_ylabel(AXIS_LABELS[name])
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4, steps=[1, 2, 2.5, 5, 10]))
            ax.xaxis.set_major_locator(FixedLocator(list(range(0, count, step))))
            ax.xaxis.set_minor_locator(FixedLocator(list(range(count))))
            ax.xaxis.set_major_formatter(FuncFormatter(tick))
            if name != names[-1]:
                ax.tick_params(labelbottom=False)
            if name == "rho":
                ax.set_ylim(bottom=0.0)
            if name == "spin":
                # At least +-0.1 so that a closed-shell path reads as zero, not as noise.
                low, high = ax.get_ylim()
                ax.set_ylim(min(low, -0.1), max(high, 0.1))
            if name in limits:
                ax.set_ylim(*limits[name])
        axes[names[-1]].set_xlim(-0.5, count - 0.5)
        axes[names[-1]].set_xlabel("Image" if series_kind == "neb" else "Frame")
        figure.align_ylabels(list(axes.values()))

        for letter, group in zip("ABC", groups, strict=False):
            top = axes[group[0]]
            top.set_title(letter, loc="left", x=-0.02, pad=6.0, fontsize=10, ha="right")
            rise = _as_finite_float(barrier.get("rise_from_first"))
            if group[0] == "energy" and peaks and rise is not None:
                top.annotate(
                    f"{rise:+.2f} eV",
                    xy=(sum(peaks) / len(peaks), 1.0),
                    xycoords=("data", "axes fraction"),
                    xytext=(0, 2.5),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=7.5,
                )
            if group[0] == "energy":
                continue
            kind = OWNER[group[0]]
            rows = max(1, int(sum(HEIGHTS[name] for name in group) * 72 / (7.5 * 1.45)))
            top.legend(
                [Line2D([], [], **style) for style in styles[kind]],
                [item.label for item in series[kind]],
                loc="upper left",
                bbox_to_anchor=(1.02, 1.0),
                frameon=False,
                borderaxespad=0.0,
                handlelength=1.7,
                handletextpad=0.4,
                labelspacing=0.35,
                ncol=math.ceil(len(series[kind]) / rows),
            )
        # Saving reads the font and SVG settings, so it stays inside the style.
        metadata = {
            "pdf": {"Creator": f"ElecTrace {__version__}", "CreationDate": None},
            "svg": {"Creator": f"ElecTrace {__version__}", "Date": None},
            "png": {"Software": f"ElecTrace {__version__}"},
        }
        for path, suffix in zip(paths, FORMATS, strict=True):
            with atomic_output_path(path) as temporary:
                figure.savefig(temporary, format=suffix, dpi=PNG_DPI, metadata=metadata[suffix])
