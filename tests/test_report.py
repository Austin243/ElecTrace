from __future__ import annotations

import re
from pathlib import Path

from electrace.models import EventType
from electrace.report import _HTML, render_html_report


def test_report_is_self_contained_and_escapes_input(tmp_path: Path) -> None:
    output = render_html_report(
        {
            "schema_version": "0.1",
            "project": {"name": "Demo <system>"},
            "frames": [{"label": "00", "energy": -1.0}],
            "events": [
                {
                    "type": "birth",
                    "start_frame": 0,
                    "confidence": 0.9,
                    "description": "appeared </script><script>alert(1)</script>",
                }
            ],
            "tracks": [],
        },
        tmp_path / "report.html",
    )

    document = output.read_text()
    assert "Demo &lt;system&gt;" in document
    assert "\\u003c/script>" in document
    assert "https://" not in document


def test_every_event_type_has_a_badge_color() -> None:
    style = _HTML[_HTML.index("<style>") : _HTML.index("</style>")]
    rules = re.findall(r"([^{}]+)\{ color: var\(--\w+\); \}", style)
    colored = {name.strip().lstrip(".") for rule in rules for name in rule.split(",")}

    assert {item.value for item in EventType} <= colored


def test_timeline_badge_column_fits_the_longest_badge() -> None:
    # "PROPERTY_CHANGE" at .7rem monospace with its padding needs about 7.5rem.
    match = re.search(r"\.event \{ display: grid; grid-template-columns: ([\d.]+)rem", _HTML)

    assert match is not None
    assert float(match.group(1)) >= 8


def test_timeline_labels_each_event_with_its_step() -> None:
    timeline = _HTML[
        _HTML.index('document.getElementById("timeline")') : _HTML.index("noiseNote(e) {")
    ]

    assert "e.start_frame" not in timeline
    assert "frameLabel(e.frame_before)" in timeline and "frameLabel(e.frame_after)" in timeline


def test_report_says_what_a_missing_descriptor_left_out(tmp_path: Path) -> None:
    document = render_html_report(
        {
            "project": {"name": "Demo"},
            "missing_descriptors": [
                {
                    "producer": "bader",
                    "file": "ACF.dat",
                    "frames": [3],
                    "frame_labels": ["03"],
                    "events_left_out": {"birth": 3, "death": 3},
                }
            ],
        },
        tmp_path / "report.html",
    ).read_text()

    assert "data.missing_descriptors" in document
    assert '"file":"ACF.dat"' in document
