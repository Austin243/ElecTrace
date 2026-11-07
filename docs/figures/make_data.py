"""Write hcn.json, the data behind the README banner, by running ElecTrace on examples/hcn-hnc.

Run it with ElecTrace installed from this checkout, then redraw the banner:

    python docs/figures/make_data.py
    node docs/figures/overview.mjs
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import electrace
from electrace.pipeline import load_and_analyze

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# The data must describe this checkout, not some other installed copy of ElecTrace.
if not Path(electrace.__file__).resolve().is_relative_to(ROOT / "src"):
    raise SystemExit(f"electrace is imported from {electrace.__file__}, not from {ROOT / 'src'}")


def rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def main() -> None:
    analysis, _ = load_and_analyze(ROOT / "examples" / "hcn-hnc" / "electrace.toml")
    side = analysis["frames"][0]["cell"][0][0]
    tracks: dict[str, dict[str, Any]] = {}
    for item in analysis["tracks"]:
        track = tracks.setdefault(item["track_id"], {"kind": item["kind"], "points": []})
        track["points"].append(
            {
                "frame": item["frame"],
                "neighbors": item["neighbor_atom_indices"],
                "x": rounded(item["fractional_x"] * side),
                "y": rounded(item["fractional_y"] * side),
                "population": rounded(item["charge"]),
                "field": rounded(item["attributes"].get("field")),
            }
        )
    reassignment = next(
        event for event in analysis["events"] if event["type"] == "connectivity_change"
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    data = {
        "electrace_version": project["version"],
        "electrace_commit": commit,
        "frames": [
            {
                "label": frame["label"],
                "atoms": [
                    {
                        "element": atom["element"],
                        "x": rounded(atom["fractional_position"][0] * side),
                        "y": rounded(atom["fractional_position"][1] * side),
                    }
                    for atom in frame["atoms"]
                ],
            }
            for frame in analysis["frames"]
        ],
        "tracks": list(tracks.values()),
        "reassignment_confidence": rounded(reassignment["confidence"], 3),
    }
    (HERE / "hcn.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
