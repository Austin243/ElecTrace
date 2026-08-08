from __future__ import annotations

from pathlib import Path

import pytest

from electrace.report import render_html_report


def test_report_is_self_contained_and_escapes_script_content(tmp_path: Path) -> None:
    output = render_html_report(
        {
            "schema_version": "0.1",
            "project": {"name": "Demo <system>"},
            "frames": [{"label": "00", "energy": -1.0, "atom_count": 2, "feature_count": 1}],
            "events": [
                {
                    "type": "birth",
                    "start_frame": 0,
                    "confidence": 0.9,
                    "confidence_label": "high",
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
    assert 'e.confidence_label || ""' in document
    assert 'e.confidence_label||"unclassified"' in document
    assert "Number(e.confidence || 0) >= .8" not in document


@pytest.mark.parametrize("bad_value", [float("nan"), object()])
def test_report_rejects_non_finite_and_non_json_analysis_values(
    tmp_path: Path, bad_value: object
) -> None:
    output = tmp_path / "report.html"

    with pytest.raises(
        ValueError,
        match="HTML report analysis JSON contains a non-finite or non-JSON value",
    ):
        render_html_report({"nested": {"bad": bad_value}}, output)

    assert not output.exists()


def test_energy_chart_preserves_sparse_frame_positions(tmp_path: Path) -> None:
    output = render_html_report(
        {
            "frames": [
                {"label": str(index), "energy": energy}
                for index, energy in enumerate((-1.0, None, None, None, 0.0))
            ],
            "events": [],
            "tracks": [],
        },
        tmp_path / "report.html",
    )

    document = output.read_text(encoding="utf-8")
    assert '"energy":null' in document
    assert "maxX=Math.max(frames.length-1,1)" in document
    assert "maxX=Math.max(points.length-1,1)" not in document
