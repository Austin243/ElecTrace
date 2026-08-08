"""Command-line interface for the ElecTrace first release."""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Never, cast

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from electrace import __version__
from electrace.benchmark import benchmark_passed, run_synthetic_benchmark
from electrace.config import ElecTraceConfig, load_config, write_default_config
from electrace.demo import generate_demo
from electrace.export import ExportDependencyError, write_json
from electrace.io import load_sequence
from electrace.pipeline import load_and_analyze, run_project
from electrace.plugins import (
    DEFAULT_REGISTRY,
    DoctorReport,
    FrameDoctorReport,
    doctor_plugins,
    doctor_plugins_path,
    plan_plugin,
    plan_plugin_path,
)
from electrace.volume import read_vasp_grid
from electrace.workflow import dump_starter_manifest
from electrace.workflow.collection import (
    analyze_collected_workflow,
    collect_workflow_outputs,
)
from electrace.workflow.operations import (
    prepare_slurm_scripts,
    run_staged_workflow,
    submit_staged_workflow,
    workflow_status,
)
from electrace.workflow.staging import stage_workflow

app = typer.Typer(
    name="electrace",
    help="Track electronic features across periodic VASP paths.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
    pretty_exceptions_show_locals=False,
)
workflow_app = typer.Typer(
    name="workflow",
    help="Stage, run, submit, collect, and analyze external calculations.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
app.add_typer(workflow_app, name="workflow")
console = Console()
error_console = Console(stderr=True)
_MINIMUM_PYTHON = (3, 11)
_MAXIMUM_TESTED_PYTHON = (3, 13)


def _python_supported(
    version: Sequence[int] = cast(Sequence[int], sys.version_info),
) -> bool:
    """Return whether *version* is within ElecTrace's tested runtime range."""

    major_minor = tuple(version[:2])
    return _MINIMUM_PYTHON <= major_minor <= _MAXIMUM_TESTED_PYTHON


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"ElecTrace {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed version and exit.",
    ),
) -> None:
    """ElecTrace tracks electronic features across ordered periodic paths."""


def _fail(message: str, *, code: int = 2) -> Never:
    error_console.print(f"[bold red]Error:[/bold red] {escape(message)}")
    raise typer.Exit(code=code)


def _load_optional_config(path: Path | None) -> ElecTraceConfig | None:
    if path is None:
        return None
    try:
        return load_config(path)
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))


@app.command()
def init(
    config: Path = typer.Argument(
        Path("electrace.toml"),
        help="Configuration file to create.",
    ),
    images: Path = typer.Option(
        ...,
        "--images",
        "-i",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Ordered VASP image or static-series root.",
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing configuration."),
) -> None:
    """Create a reviewed starter configuration for an existing VASP path."""

    try:
        output = write_default_config(config, input_root=images, overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Created[/green] {output}")
    console.print("Review matching thresholds and plugin settings before analysis.")


@app.command()
def doctor(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Optional ElecTrace TOML configuration.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Check the Python runtime, plugins, and configured frame inputs."""

    loaded = _load_optional_config(config)
    plugin_config = loaded.plugins if loaded is not None else None
    frame_reports: tuple[FrameDoctorReport, ...] = ()
    reports: tuple[DoctorReport, ...] = ()
    configured_root: Path | None = None
    discovery_error: str | None = None
    if loaded is not None and config is not None:
        configured_root = Path(loaded.inputs.root).expanduser()
        if not configured_root.is_absolute():
            configured_root = config.parent / configured_root
        try:
            frame_reports = doctor_plugins_path(
                configured_root,
                configuration=plugin_config,
                mode=loaded.series_kind,
                prefer_contcar=loaded.inputs.prefer_contcar,
                structure_name=loaded.inputs.structure_name,
                fallback_structure_name=loaded.inputs.fallback_structure_name,
            )
        except (OSError, TypeError, ValueError) as exc:
            discovery_error = str(exc)
    else:
        reports = doctor_plugins(configuration=plugin_config)
    payload = {
        "electrace_version": __version__,
        "python": platform.python_version(),
        "python_supported": _python_supported(),
        "configuration": str(config) if config is not None else None,
        "discovery_error": discovery_error,
        "plugins": [report.to_dict() for report in reports],
        "frames": [report.to_dict() for report in frame_reports],
    }
    if as_json:
        console.print_json(data=payload)
        return
    table = Table(title="ElecTrace environment")
    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Details")
    table.add_row(
        "Python",
        "[green]ready[/green]" if payload["python_supported"] else "[red]unsupported[/red]",
        str(payload["python"]),
    )
    if discovery_error:
        table.add_row("Configured path", "[red]error[/red]", discovery_error)
    elif configured_root:
        table.add_row("Configured path", "[green]readable[/green]", str(configured_root))
    for report in reports:
        status = (
            "[green]ready[/green]"
            if report.ready
            else (
                "[yellow]installed; inputs missing[/yellow]"
                if report.installed
                else "[dim]optional; not found[/dim]"
            )
        )
        details = "; ".join(report.messages) or str(report.executable_path)
        table.add_row(report.plugin, status, escape(details))
    for frame_report in frame_reports:
        for report in frame_report.plugins:
            status = (
                "[green]ready[/green]"
                if report.ready
                else (
                    "[yellow]installed; inputs missing[/yellow]"
                    if report.installed
                    else "[dim]optional; not found[/dim]"
                )
            )
            details = "; ".join(report.messages) or str(report.executable_path)
            table.add_row(
                f"{report.plugin} [{frame_report.label}]",
                status,
                escape(details),
            )
    console.print(table)
    if not reports and not any(report.plugins for report in frame_reports):
        console.print("No plugins are enabled in the selected configuration.")


@app.command()
def demo(
    output: Path = typer.Option(
        Path(".electrace-demo"),
        "--output",
        "-o",
        help="Directory for the synthetic demonstration.",
    ),
    force: bool = typer.Option(False, "--force", help="Replace known demo artifacts."),
    analyze: bool = typer.Option(
        True,
        "--analyze/--no-analyze",
        help="Run the generated demonstration through the full pipeline.",
    ),
) -> None:
    """Generate a deterministic synthetic path and optionally analyze it."""

    try:
        config_path = generate_demo(output, overwrite=force)
        artifacts = run_project(config_path, overwrite=force) if analyze else None
    except (OSError, TypeError, ValueError, ExportDependencyError) as exc:
        _fail(str(exc))
    console.print(f"[green]Generated demo[/green] at {output.resolve()}")
    if artifacts is not None:
        console.print(
            f"Detected [bold]{len(artifacts.analysis['events'])}[/bold] events; "
            f"report: {artifacts.output_directory / 'report.html'}"
        )


@app.command()
def benchmark(
    output: Path = typer.Option(
        Path("electrace-benchmark.json"),
        "--output",
        "-o",
        help="Path for the deterministic JSON benchmark report.",
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing report."),
) -> None:
    """Run the offline synthetic matching and event regression benchmark."""

    try:
        if (output.exists() or output.is_symlink()) and not force:
            raise FileExistsError(
                f"benchmark output already exists: {output}; use --force to replace it"
            )
        report = run_synthetic_benchmark()
        write_json(report, output)
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    summary = cast(dict[str, Any], report["summary"])
    passed = benchmark_passed(report)
    status = "passed" if passed else "failed"
    color = "green" if passed else "red"
    console.print(
        f"[{color}]Synthetic benchmark {status}[/{color}]: "
        f"{summary['passed']}/{summary['cases']} cases · "
        f"event precision {summary['event_precision']:.3f} · "
        f"event recall {summary['event_recall']:.3f}"
    )
    console.print(f"Report: {output.resolve()}")
    if not passed:
        raise typer.Exit(code=1)


@app.command()
def analyze(
    config: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="ElecTrace TOML configuration.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Override the configured output directory.",
    ),
    strict: bool = typer.Option(
        True,
        "--strict/--permissive",
        help="Fail on incomplete discovery or ambiguous atom/feature correspondence.",
    ),
    force: bool = typer.Option(False, "--force", help="Replace known result artifacts."),
) -> None:
    """Run periodic correspondence, event detection, and report generation."""

    try:
        artifacts = run_project(
            config,
            output_directory=output,
            strict=strict,
            overwrite=force,
        )
    except (OSError, TypeError, ValueError, ExportDependencyError) as exc:
        _fail(str(exc))
    analysis = artifacts.analysis
    console.print(f"[green]Analysis complete[/green]: {artifacts.output_directory}")
    console.print(
        f"{len(analysis['frames'])} frames · {len(analysis['tracks'])} feature observations · "
        f"{len(analysis['events'])} events · {len(analysis['warnings'])} warnings"
    )
    for item in artifacts.files:
        console.print(f"  [dim]{item.name}[/dim]")


def _read_analysis(target: Path) -> dict[str, Any] | None:
    candidate = target / "analysis.json" if target.is_dir() else target
    if candidate.name != "analysis.json" or not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"Cannot read {candidate}: {exc}")
    if not isinstance(payload, dict):
        _fail(f"{candidate} does not contain an analysis object")
    return cast(dict[str, Any], payload)


def _print_analysis_summary(analysis: dict[str, Any]) -> None:
    project = analysis.get("project", {})
    console.print(f"[bold]{project.get('name', 'ElecTrace analysis')}[/bold]")
    console.print(
        f"{len(analysis.get('frames', []))} frames · {len(analysis.get('tracks', []))} feature observations · "
        f"{len(analysis.get('events', []))} events"
    )
    warnings = analysis.get("warnings", [])
    if warnings:
        console.print(f"[yellow]{len(warnings)} warning(s)[/yellow]")
        for warning in warnings:
            console.print(f"  - {warning}")


@app.command()
def inspect(
    target: Path = typer.Argument(
        ..., exists=True, resolve_path=True, help="Config, input path, or result bundle."
    ),
) -> None:
    """Summarize a configuration, source series, or analysis bundle read-only."""

    analysis = _read_analysis(target)
    if analysis is not None:
        _print_analysis_summary(analysis)
        return
    if target.is_file() and target.suffix.lower() == ".toml":
        try:
            configured_analysis, _ = load_and_analyze(target)
        except (OSError, TypeError, ValueError) as exc:
            _fail(str(exc))
        _print_analysis_summary(configured_analysis)
        return
    if not target.is_dir():
        _fail("Expected electrace.toml, analysis.json, or an input directory")
    try:
        sequence = load_sequence(target)
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    table = Table(title=f"Ordered series: {target.name}")
    table.add_column("Index", justify="right")
    table.add_column("Label")
    table.add_column("Atoms", justify="right")
    table.add_column("Features", justify="right")
    table.add_column("Energy (eV)", justify="right")
    for frame in sequence.frames:
        table.add_row(
            str(frame.index),
            frame.label,
            str(len(frame.atoms)),
            str(len(frame.features)),
            "—" if frame.energy is None else f"{frame.energy:.6f}",
        )
    console.print(table)


@app.command()
def plan(
    plugin: str = typer.Argument(..., help=f"Plugin name: {', '.join(DEFAULT_REGISTRY.names())}."),
    frame: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        resolve_path=True,
        help="One VASP frame directory, or a path root with --all-frames.",
    ),
    capability: str | None = typer.Option(None, "--capability", "-c"),
    config: Path | None = typer.Option(
        None,
        "--config",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    all_frames: bool = typer.Option(
        False,
        "--all-frames",
        help="Plan deterministically for every frame discovered below FRAME.",
    ),
) -> None:
    """Print an external-tool command plan without executing it."""

    loaded = _load_optional_config(config)
    plugin_name = plugin.strip().lower()
    plugin_config = loaded.plugins.get(plugin_name, {}) if loaded is not None else None
    try:
        if all_frames:
            commands = plan_plugin_path(
                plugin_name,
                frame,
                capability=capability,
                configuration=plugin_config,
                mode=loaded.series_kind if loaded is not None else "auto",
                prefer_contcar=(loaded.inputs.prefer_contcar if loaded is not None else True),
                structure_name=(loaded.inputs.structure_name if loaded is not None else "CONTCAR"),
                fallback_structure_name=(
                    loaded.inputs.fallback_structure_name if loaded is not None else "POSCAR"
                ),
            )
            console.print_json(
                data={
                    "plugin": plugin_name,
                    "path": str(frame),
                    "plans": [command.to_dict() for command in commands],
                }
            )
            return
        if loaded is not None and plugin_name == "critic2":
            plugin_config = dict(plugin_config or {})
            plugin_config.setdefault("structure_file", loaded.inputs.structure_name)
        command = plan_plugin(
            plugin_name,
            frame,
            capability=capability,
            configuration=plugin_config,
        )
    except (KeyError, OSError, ValueError, TypeError) as exc:
        _fail(str(exc))
    console.print_json(data=command.to_dict())


def _fractional_coordinate(value: str) -> tuple[float, float, float]:
    try:
        fields = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ValueError("--sample must be three comma-separated numbers") from exc
    if len(fields) != 3:
        raise ValueError("--sample must be three comma-separated numbers")
    return fields


@app.command()
def volume(
    path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Plain VASP ELFCAR/CHGCAR-like volumetric file.",
    ),
    dataset: int = typer.Option(0, "--dataset", min=0, help="Zero-based scalar dataset."),
    sample: str | None = typer.Option(
        None,
        "--sample",
        help="Periodically sample at fractional x,y,z.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Read a VASP volumetric grid directly, summarize it, and optionally sample it."""

    try:
        grid = read_vasp_grid(path, dataset=dataset)
        payload = grid.summary()
        if sample is not None:
            coordinate = _fractional_coordinate(sample)
            payload["sample"] = {
                "fractional_position": list(coordinate),
                "value": grid.sample(coordinate),
            }
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    if as_json or sample is not None:
        console.print_json(data=payload)
        return
    table = Table(title=f"VASP grid: {path.name}")
    table.add_column("Property")
    table.add_column("Value", justify="right")
    for key in (
        "dataset_index",
        "dataset_count",
        "shape",
        "point_count",
        "cell_volume",
        "minimum",
        "maximum",
        "mean",
    ):
        table.add_row(key.replace("_", " "), str(payload[key]))
    console.print(table)


@workflow_app.command("init")
def workflow_init(
    manifest: Path = typer.Argument(
        Path("electrace-workflow.yaml"),
        help="Workflow YAML file to create.",
    ),
    images: Path = typer.Option(
        ...,
        "--images",
        "-i",
        exists=True,
        file_okay=False,
        resolve_path=True,
        help="Ordered VASP NEB or static-series root.",
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing manifest."),
) -> None:
    """Create a reviewed YAML workflow with explicit executable settings."""

    try:
        output = dump_starter_manifest(manifest, root=str(images), overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Created workflow[/green] {output.resolve()}")
    console.print("Set executable paths and provide reviewed VASP/template inputs before staging.")


@workflow_app.command("stage")
def workflow_stage(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="ElecTrace workflow YAML.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace only an existing marker-protected ElecTrace workspace.",
    ),
) -> None:
    """Select frames and stage auditable jobs without executing anything."""

    try:
        staged = stage_workflow(manifest, overwrite=force)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        _fail(str(exc))
    console.print(f"[green]Staged[/green] {len(staged.jobs)} jobs in {staged.workspace}")
    console.print(f"Selected frames: {', '.join(frame.label for frame in staged.frames)}")


@workflow_app.command("run")
def workflow_run(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Staged local workflow YAML.",
    ),
    confirmed: bool = typer.Option(
        False,
        "--yes",
        help="Explicitly authorize execution of all staged external commands.",
    ),
    max_parallel: int = typer.Option(
        1,
        "--max-parallel",
        min=1,
        help="Maximum independent local jobs to run concurrently.",
    ),
    keep_going: bool = typer.Option(
        False,
        "--keep-going",
        help="Continue starting independent jobs after a failure.",
    ),
) -> None:
    """Execute a staged local DAG; external commands require ``--yes``."""

    try:
        results = run_staged_workflow(
            manifest,
            confirmed=confirmed,
            max_parallel=max_parallel,
            stop_on_error=not keep_going,
        )
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    failures = [result for result in results if not result.succeeded]
    console.print(
        f"[{'red' if failures else 'green'}]Workflow finished[/]: "
        f"{len(results) - len(failures)}/{len(results)} jobs succeeded"
    )
    if failures:
        for result in failures:
            error_console.print(f"  {result.id}: {result.status} — {result.error}")
        raise typer.Exit(code=1)


@workflow_app.command("submit")
def workflow_submit(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Staged Slurm workflow YAML.",
    ),
    job_name: str = typer.Option(
        "electrace",
        "--job-name",
        help="Exact Slurm job name used for every selected frame.",
    ),
    confirmed: bool = typer.Option(
        False,
        "--yes",
        help="Actually invoke sbatch; without this flag only scripts are prepared.",
    ),
    submit_command: str = typer.Option(
        "sbatch",
        "--submit-command",
        help="sbatch-compatible executable or wrapper path.",
    ),
    force: bool = typer.Option(False, "--force", help="Replace generated Slurm scripts."),
) -> None:
    """Prepare Slurm scripts and optionally submit one job per selected frame."""

    try:
        if not confirmed:
            scripts = prepare_slurm_scripts(manifest, job_name=job_name, overwrite=force)
            console.print(f"[green]Prepared[/green] {len(scripts)} Slurm scripts")
            for script_path in scripts:
                console.print(f"  [dim]{script_path}[/dim]")
            console.print("Review the scripts, then repeat with --yes to submit them.")
            return
        submissions = submit_staged_workflow(
            manifest,
            job_name=job_name,
            submit_command=submit_command,
            confirmed=True,
            overwrite_scripts=force,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Submitted[/green] {len(submissions)} Slurm jobs")
    for submitted_script, job_id in submissions.items():
        console.print(f"  {job_id}: [dim]{submitted_script}[/dim]")


@workflow_app.command("status")
def workflow_status_command(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Staged workflow YAML.",
    ),
) -> None:
    """Report which staged inputs and expected outputs currently exist."""

    try:
        payload = workflow_status(manifest)
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    console.print_json(data=payload)


@workflow_app.command("collect")
def workflow_collect(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Completed staged workflow YAML.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace only the marker-protected collected analysis tree.",
    ),
) -> None:
    """Validate and collect every selected descriptor output for analysis."""

    try:
        collected = collect_workflow_outputs(manifest, overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Collected[/green] workflow evidence at {collected.config_path.parent}")
    console.print(f"Analysis configuration: {collected.config_path}")


@workflow_app.command("analyze")
def workflow_analyze(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="Completed staged workflow YAML.",
    ),
    strict: bool = typer.Option(
        True,
        "--strict/--permissive",
        help="Fail on ambiguous atom or feature correspondence.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace marker-protected collected evidence and known result artifacts.",
    ),
) -> None:
    """Collect completed outputs, track changes, and generate the result bundle."""

    try:
        artifacts = analyze_collected_workflow(
            manifest,
            strict=strict,
            overwrite=force,
        )
    except (OSError, TypeError, ValueError, ExportDependencyError) as exc:
        _fail(str(exc))
    analysis = artifacts.analysis
    console.print(f"[green]Workflow analysis complete[/green]: {artifacts.output_directory}")
    console.print(
        f"{len(analysis['frames'])} frames · {len(analysis['tracks'])} feature observations · "
        f"{len(analysis['events'])} events · {len(analysis['warnings'])} warnings"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
