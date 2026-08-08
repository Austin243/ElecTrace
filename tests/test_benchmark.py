from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from electrace.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    EventLabel,
    _event_scores,
    benchmark_passed,
    run_synthetic_benchmark,
    write_synthetic_benchmark,
)
from electrace.cli import app
from electrace.models import Event


def _plain(output: str) -> str:
    return Text.from_ansi(output).plain


def test_synthetic_benchmark_is_deterministic_and_passes() -> None:
    first = run_synthetic_benchmark()
    second = run_synthetic_benchmark()

    assert json.dumps(first, allow_nan=False, sort_keys=True) == json.dumps(
        second, allow_nan=False, sort_keys=True
    )
    assert first["schema_version"] == BENCHMARK_SCHEMA_VERSION
    assert first["suite"]["synthetic"] is True
    assert first["suite"]["scientific_validation"] is False
    assert first["summary"] == {
        "cases": 15,
        "passed": 15,
        "failed": 0,
        "event_true_positives": 10,
        "event_false_positives": 0,
        "event_false_negatives": 0,
        "event_precision": 1.0,
        "event_recall": 1.0,
        "event_f1": 1.0,
        "mapping_assertions_correct": 30,
        "mapping_assertions_total": 30,
        "mapping_accuracy": 1.0,
        "ambiguity_assertions_correct": 30,
        "ambiguity_assertions_total": 30,
        "ambiguity_accuracy": 1.0,
    }
    assert benchmark_passed(first)


def test_negative_controls_and_ambiguity_are_explicitly_auditable() -> None:
    report = run_synthetic_benchmark()
    cases = {case["id"]: case for case in report["cases"]}

    for identifier, reason in (
        ("proximity-without-conservation", "missing_complete_conservation_channel"),
        ("failed-conservation", "conservation_tolerance_exceeded"),
    ):
        case = cases[identifier]
        assert case["passed"] is True
        assert all(event["type"] != "split" for event in case["observed"]["events"])
        assert case["observed"]["hyperedge_rejections"][0]["reason"] == reason

    for identifier in ("exact-assignment-tie", "near-assignment-tie"):
        case = cases[identifier]
        assert case["observed"]["feature_ambiguous"] is True
        assert case["observed"]["feature_ambiguity_count"] >= 1
        assert case["observed"]["events"] == []

    resolved = cases["hyperedge-resolves-backbone-tie"]
    assert resolved["observed"]["feature_ambiguous"] is False
    assert [event["type"] for event in resolved["observed"]["events"]] == ["split"]


def test_event_scoring_counts_false_positive_and_false_negative() -> None:
    expected = (EventLabel("birth", target_ids=("q",)),)
    observed = (
        Event(
            "death",
            0,
            1,
            source_ids=("p",),
            confidence=0.75,
        ),
    )

    assert _event_scores(expected, observed) == (0, 1, 1)


def test_benchmark_writer_is_atomic_and_requires_explicit_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "benchmark.json"
    target = tmp_path / "outside.json"
    target.write_text("preserve me", encoding="utf-8")
    output.symlink_to(target)

    with pytest.raises(FileExistsError, match="use --force"):
        write_synthetic_benchmark(output)

    written = write_synthetic_benchmark(output, overwrite=True)

    assert written == output
    assert not output.is_symlink()
    assert target.read_text(encoding="utf-8") == "preserve me"
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["failed"] == 0


def test_benchmark_cli_writes_report_and_reports_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "benchmark.json"
    runner = CliRunner()

    first = runner.invoke(app, ["benchmark", "--output", str(output)])
    assert first.exit_code == 0, first.output
    assert "Synthetic benchmark passed: 15/15 cases" in _plain(first.stdout)
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["event_recall"] == 1.0

    refused = runner.invoke(app, ["benchmark", "--output", str(output)])
    assert refused.exit_code == 2
    assert "use --force" in _plain(refused.output)

    replaced = runner.invoke(app, ["benchmark", "--output", str(output), "--force"])
    assert replaced.exit_code == 0, replaced.output
