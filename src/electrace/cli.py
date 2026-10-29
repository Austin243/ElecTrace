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
from electrace.cli_output import failure, serialize, success
from electrace.config import load_config, write_default_config
from electrace.demo import generate_demo
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
from electrace.volume import read_vasp_grid, split_spin_density, sum_density_grids
from electrace.workflow import dump_starter_manifest, validate_workflow
from electrace.workflow.collection import (
    analyze_collected_workflow,
    collect_workflow_outputs,
)
from electrace.workflow.operations import (
    SlurmSubmissionError,
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


def _emit_json(
    command: str,
    data: object,
    *,
    warnings: Sequence[str] = (),
) -> None:
    sys.stdout.write(serialize(success(command, data, warnings=warnings)))


def _finish(command: str, data: object, as_json: bool, *messages: str) -> None:
    if as_json:
        _emit_json(command, data)
    else:
        for message in messages:
            console.print(message)


def _exception_code(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        message = str(exc).casefold()
        return (
            "authorization_required"
            if "confirmed=true" in message or "--yes" in message
            else "permission_denied"
        )
    if isinstance(exc, KeyError):
        return "unknown_resource"
    if isinstance(exc, RuntimeError):
        return "execution_error"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    if isinstance(exc, FileExistsError):
        return "already_exists"
    if isinstance(exc, (IsADirectoryError, NotADirectoryError)):
        return "invalid_path"
    if isinstance(exc, OSError):
        return "io_error"
    return "invalid_input"


def _fail(
    message: str,
    *,
    code: int = 2,
    as_json: bool = False,
    command: str = "electrace",
    error_code: str = "invalid_input",
    details: object | None = None,
) -> Never:
    if as_json:
        sys.stdout.write(serialize(failure(command, error_code, message, details=details)))
        raise typer.Exit(code=code)
    error_console.print(f"[bold red]Error:[/bold red] {escape(message)}")
    raise typer.Exit(code=code)


def _fail_exception(
    exc: BaseException,
    *,
    command: str,
    as_json: bool,
    code: int = 2,
) -> Never:
    message = str(exc).strip() or type(exc).__name__
    _fail(
        message,
        code=code,
        as_json=as_json,
        command=command,
        error_code=_exception_code(exc),
    )


def _analysis_summary(analysis: dict[str, Any], source: Path) -> dict[str, Any]:
    return {
        "source": str(source.expanduser().resolve()),
        "project": analysis.get("project", {}),
        "counts": {
            "frames": len(analysis.get("frames", [])),
            "feature_observations": len(analysis.get("tracks", [])),
            "events": len(analysis.get("events", [])),
            "warnings": len(analysis.get("warnings", [])),
        },
    }


def _analysis_warnings(analysis: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in analysis.get("warnings", []))


def _doctor_row(report: DoctorReport) -> tuple[str, str]:
    status = (
        "[green]ready[/green]"
        if report.ready
        else "[yellow]installed; inputs missing[/yellow]"
        if report.installed
        else "[dim]optional; not found[/dim]"
    )
    return status, escape("; ".join(report.messages) or str(report.executable_path))


@app.command()
def init(
    config: Path = typer.Argument(Path("electrace.toml")),
    images: Path = typer.Option(..., "--images", "-i"),
    force: bool = typer.Option(False, "--force"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create a reviewed starter configuration for an existing VASP path."""

    try:
        output = write_default_config(config, input_root=images, overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="init", as_json=as_json)
    _finish(
        "init",
        {"configuration": str(output.resolve()), "images": str(images.resolve())},
        as_json,
        f"[green]Created[/green] {output}",
        "Review matching thresholds and plugin settings before analysis.",
    )


@app.command()
def doctor(
    config: Path | None = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Check the Python runtime, plugins, and configured frame inputs."""

    try:
        loaded = load_config(config) if config is not None else None
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="doctor", as_json=as_json)
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
        warnings = (discovery_error,) if discovery_error is not None else ()
        _emit_json("doctor", payload, warnings=warnings)
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
        table.add_row(report.plugin, *_doctor_row(report))
    for frame_report in frame_reports:
        for report in frame_report.plugins:
            table.add_row(f"{report.plugin} [{frame_report.label}]", *_doctor_row(report))
    console.print(table)
    if not reports and not any(report.plugins for report in frame_reports):
        console.print("No plugins are enabled in the selected configuration.")


@app.command()
def demo(
    output: Path = typer.Option(Path(".electrace-demo"), "--output", "-o"),
    force: bool = typer.Option(False, "--force"),
    analyze: bool = typer.Option(True, "--analyze/--no-analyze"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Generate a deterministic synthetic path and optionally analyze it."""

    try:
        config_path = generate_demo(output, overwrite=force)
        artifacts = run_project(config_path, overwrite=force) if analyze else None
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="demo", as_json=as_json)
    if as_json:
        data: dict[str, Any] = {
            "output_directory": str(output.resolve()),
            "configuration": str(config_path.resolve()),
            "analyzed": artifacts is not None,
        }
        warnings: Sequence[str] = ()
        if artifacts is not None:
            data["analysis"] = _analysis_summary(
                artifacts.analysis,
                artifacts.output_directory,
            )
            warnings = tuple(str(item) for item in artifacts.analysis.get("warnings", []))
        _emit_json("demo", data, warnings=warnings)
        return
    console.print(f"[green]Generated demo[/green] at {output.resolve()}")
    if artifacts is not None:
        console.print(
            f"Detected [bold]{len(artifacts.analysis['events'])}[/bold] events; "
            f"report: {artifacts.output_directory / 'report.html'}"
        )


@app.command()
def analyze(
    config: Path = typer.Argument(...),
    output: Path | None = typer.Option(None, "--output", "-o"),
    strict: bool = typer.Option(True, "--strict/--permissive"),
    force: bool = typer.Option(False, "--force"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run periodic correspondence, event detection, and report generation."""

    try:
        artifacts = run_project(
            config,
            output_directory=output,
            strict=strict,
            overwrite=force,
        )
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="analyze", as_json=as_json)
    analysis = artifacts.analysis
    if as_json:
        warnings = tuple(str(item) for item in analysis.get("warnings", []))
        _emit_json(
            "analyze",
            _analysis_summary(analysis, artifacts.output_directory),
            warnings=warnings,
        )
        return
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
    except (OSError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {candidate}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{candidate} does not contain an analysis object")
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
    target: Path = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Summarize a configuration, source series, or analysis bundle read-only."""

    kind = "analysis"
    try:
        analysis = _read_analysis(target)
        if analysis is None and target.is_file() and target.suffix.lower() == ".toml":
            analysis, _ = load_and_analyze(target)
            kind = "configuration"
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="inspect", as_json=as_json)
    if analysis is not None:
        try:
            summary = _analysis_summary(analysis, target)
            warnings = _analysis_warnings(analysis)
        except (OSError, TypeError, ValueError) as exc:
            _fail_exception(exc, command="inspect", as_json=as_json)
        if as_json:
            _emit_json("inspect", {"kind": kind, **summary}, warnings=warnings)
        else:
            _print_analysis_summary(analysis)
        return
    if not target.is_dir():
        _fail(
            "Expected electrace.toml, analysis.json, or an input directory",
            as_json=as_json,
            command="inspect",
        )
    try:
        sequence = load_sequence(target)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="inspect", as_json=as_json)
    if as_json:
        _emit_json(
            "inspect",
            {
                "kind": "ordered_series",
                "target": str(target),
                "frames": [
                    {
                        "index": frame.index,
                        "label": frame.label,
                        "atom_count": len(frame.atoms),
                        "feature_count": len(frame.features),
                        "energy_ev": frame.energy,
                    }
                    for frame in sequence.frames
                ],
            },
        )
        return
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
    frame: Path = typer.Argument(...),
    capability: str | None = typer.Option(None, "--capability", "-c"),
    config: Path | None = typer.Option(None, "--config"),
    all_frames: bool = typer.Option(False, "--all-frames"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print an external-tool command plan without executing it."""

    try:
        loaded = load_config(config) if config is not None else None
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="plan", as_json=as_json)
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
            payload = {
                "plugin": plugin_name,
                "path": str(frame),
                "plans": [command.to_dict() for command in commands],
            }
        else:
            if loaded is not None and plugin_name == "critic2":
                plugin_config = dict(plugin_config or {})
                plugin_config.setdefault("structure_file", loaded.inputs.structure_name)
            payload = plan_plugin(
                plugin_name,
                frame,
                capability=capability,
                configuration=plugin_config,
            ).to_dict()
    except (KeyError, OSError, ValueError, TypeError) as exc:
        _fail_exception(exc, command="plan", as_json=as_json)
    if as_json:
        _emit_json("plan", payload)
    else:
        console.print_json(data=payload)


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
    path: Path = typer.Argument(...),
    dataset: int = typer.Option(0, "--dataset", min=0),
    sample: str | None = typer.Option(None, "--sample"),
    split_spin: bool = typer.Option(
        False,
        "--split-spin",
        help="Write CHGCAR_tot and CHGCAR_mag from a collinear ISPIN = 2 CHGCAR.",
    ),
    add: Path | None = typer.Option(
        None,
        "--add",
        help="Write CHGCAR_sum = PATH + ADD, such as AECCAR0 + AECCAR2 for bader -ref.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Directory for --split-spin or --add files; default spin/ or the directory of PATH.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Replace existing --split-spin or --add files."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Read a VASP volumetric grid directly; summarize, sample, sum, or split its spin grids."""

    if add is not None:
        if split_spin or sample is not None or dataset:
            _fail(
                "--add cannot be combined with --split-spin, --dataset, or --sample",
                as_json=as_json,
                command="volume",
            )
        try:
            payload = sum_density_grids(path, add, output_dir, overwrite=force)
        except (OSError, TypeError, ValueError) as exc:
            _fail_exception(exc, command="volume", as_json=as_json)
        _finish(
            "volume",
            payload,
            as_json,
            f"[green]Wrote[/green] {payload['written']}",
            f"Integrated sum: {payload['integrated_sum']:.4f}",
        )
        return
    if split_spin:
        if sample is not None or dataset:
            _fail(
                "--split-spin cannot be combined with --dataset or --sample",
                as_json=as_json,
                command="volume",
            )
        try:
            payload = split_spin_density(path, output_dir, overwrite=force)
        except (OSError, TypeError, ValueError) as exc:
            _fail_exception(exc, command="volume", as_json=as_json)
        written = cast(list[str], payload["written"])
        _finish(
            "volume",
            payload,
            as_json,
            f"[green]Wrote[/green] {written[0]}",
            f"[green]Wrote[/green] {written[1]}",
            f"Integrated magnetization: {payload['integrated_magnetization']:.4f}",
        )
        return
    if output_dir is not None or force:
        _fail(
            "--output-dir and --force apply only to --split-spin and --add",
            as_json=as_json,
            command="volume",
        )
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
        _fail_exception(exc, command="volume", as_json=as_json)
    if as_json:
        _emit_json("volume", payload)
        return
    if sample is not None:
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
    manifest: Path = typer.Argument(Path("electrace-workflow.yaml")),
    images: Path = typer.Option(..., "--images", "-i"),
    force: bool = typer.Option(False, "--force"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create a reviewed YAML workflow with explicit executable settings."""

    try:
        output = dump_starter_manifest(manifest, root=str(images), overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.init", as_json=as_json)
    _finish(
        "workflow.init",
        {"manifest": str(output.resolve()), "images": str(images)},
        as_json,
        f"[green]Created workflow[/green] {output.resolve()}",
        "Set executable paths and provide reviewed VASP/template inputs before staging.",
    )


@workflow_app.command("validate")
def workflow_validate(
    manifest: Path = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Resolve and validate the complete workflow plan without writing anything."""

    try:
        validation = validate_workflow(manifest)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        _fail(
            str(exc),
            as_json=as_json,
            command="workflow.validate",
            error_code="validation_failed",
        )
    payload = validation.to_dict()
    if as_json:
        _emit_json(
            "workflow.validate",
            payload,
            warnings=validation.warnings,
        )
        return
    console.print(
        f"[green]Workflow valid[/green]: {len(validation.resolved_jobs)} jobs across "
        f"{len(validation.selected_frames)} selected frames"
    )
    console.print(f"Workspace: {validation.workspace}")
    console.print(f"Scheduler: {validation.scheduler.kind}")
    for warning in validation.warnings:
        console.print(f"[yellow]Warning:[/yellow] {escape(warning)}")


@workflow_app.command("stage")
def workflow_stage(
    manifest: Path = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Select frames and stage auditable jobs without executing anything."""

    try:
        staged = stage_workflow(manifest, overwrite=force)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        _fail_exception(exc, command="workflow.stage", as_json=as_json)
    _finish(
        "workflow.stage",
        staged.to_dict(),
        as_json,
        f"[green]Staged[/green] {len(staged.jobs)} jobs in {staged.workspace}",
        f"Selected frames: {', '.join(frame.label for frame in staged.frames)}",
    )


@workflow_app.command("run")
def workflow_run(
    manifest: Path = typer.Argument(...),
    confirmed: bool = typer.Option(False, "--yes"),
    max_parallel: int = typer.Option(1, "--max-parallel", min=1),
    keep_going: bool = typer.Option(False, "--keep-going"),
    as_json: bool = typer.Option(False, "--json"),
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
        _fail_exception(exc, command="workflow.run", as_json=as_json)
    failures = [result for result in results if not result.succeeded]
    payload = {
        "jobs": [result.to_dict() for result in results],
        "counts": {
            "total": len(results),
            "succeeded": len(results) - len(failures),
            "failed": len(failures),
        },
    }
    if as_json:
        if failures:
            _fail(
                f"{len(failures)} workflow job(s) failed",
                code=1,
                as_json=True,
                command="workflow.run",
                error_code="workflow_jobs_failed",
                details=payload,
            )
        _emit_json("workflow.run", payload)
        return
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
    manifest: Path = typer.Argument(...),
    job_name: str = typer.Option("electrace", "--job-name"),
    confirmed: bool = typer.Option(False, "--yes"),
    submit_command: str = typer.Option("sbatch", "--submit-command"),
    force: bool = typer.Option(False, "--force"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Prepare Slurm scripts and optionally submit one job per selected frame."""

    try:
        if not confirmed:
            scripts = prepare_slurm_scripts(manifest, job_name=job_name, overwrite=force)
            if as_json:
                _emit_json(
                    "workflow.submit",
                    {
                        "submitted": False,
                        "job_name": job_name,
                        "scripts": [str(path) for path in scripts],
                    },
                )
                return
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
    except SlurmSubmissionError as exc:
        details = {
            "phase": exc.phase,
            "failed_script": str(exc.failed_script) if exc.failed_script is not None else None,
            "partial_submissions": dict(exc.partial_submissions),
            "submission_complete": exc.submission_complete,
            "record_path": str(exc.record_path),
        }
        _fail(
            str(exc),
            code=1,
            as_json=as_json,
            command="workflow.submit",
            error_code="slurm_submission_failed",
            details=details,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.submit", as_json=as_json)
    if as_json:
        record_path = Path(next(iter(submissions))).parent / "submissions.json"
        _emit_json(
            "workflow.submit",
            {
                "submitted": True,
                "job_name": job_name,
                "submissions": submissions,
                "submission_record": str(record_path),
            },
        )
        return
    console.print(f"[green]Submitted[/green] {len(submissions)} Slurm jobs")
    for submitted_script, job_id in submissions.items():
        console.print(f"  {job_id}: [dim]{submitted_script}[/dim]")


@workflow_app.command("status")
def workflow_status_command(
    manifest: Path = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Report which staged inputs and expected outputs currently exist."""

    try:
        payload = workflow_status(manifest)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.status", as_json=as_json)
    if as_json:
        _emit_json("workflow.status", payload)
        return
    console.print_json(data=payload)


@workflow_app.command("collect")
def workflow_collect(
    manifest: Path = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Validate and collect every selected descriptor output for analysis."""

    try:
        collected = collect_workflow_outputs(manifest, overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.collect", as_json=as_json)
    _finish(
        "workflow.collect",
        {
            "configuration": str(collected.config_path),
            "input_root": str(collected.input_root),
            "descriptor_root": str(collected.descriptor_root),
            "files": [str(path) for path in collected.files],
        },
        as_json,
        f"[green]Collected[/green] workflow evidence at {collected.config_path.parent}",
        f"Analysis configuration: {collected.config_path}",
    )


@workflow_app.command("analyze")
def workflow_analyze(
    manifest: Path = typer.Argument(...),
    strict: bool = typer.Option(True, "--strict/--permissive"),
    force: bool = typer.Option(False, "--force"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Collect completed outputs, track changes, and generate the result bundle."""

    try:
        artifacts = analyze_collected_workflow(
            manifest,
            strict=strict,
            overwrite=force,
        )
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.analyze", as_json=as_json)
    analysis = artifacts.analysis
    if as_json:
        warnings = tuple(str(item) for item in analysis.get("warnings", []))
        _emit_json(
            "workflow.analyze",
            _analysis_summary(analysis, artifacts.output_directory),
            warnings=warnings,
        )
        return
    console.print(f"[green]Workflow analysis complete[/green]: {artifacts.output_directory}")
    console.print(
        f"{len(analysis['frames'])} frames · {len(analysis['tracks'])} feature observations · "
        f"{len(analysis['events'])} events · {len(analysis['warnings'])} warnings"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
