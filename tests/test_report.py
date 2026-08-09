from __future__ import annotations

from pathlib import Path

from electrace.report import render_html_report


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
