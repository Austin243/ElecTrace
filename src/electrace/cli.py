"""Command-line interface for the ElecTrace first release."""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Never, Protocol, cast

import typer
import typer.core as typer_core
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from typer.core import TyperGroup

from electrace import __version__
from electrace.benchmark import benchmark_passed, run_synthetic_benchmark
from electrace.cli_output import failure, normalize_error_code, serialize, success
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

_CLICK_RUNTIME = cast(Any, typer_core.__dict__.get("_click") or typer_core.__dict__["click"])
_CLICK_EXCEPTION = cast(type[BaseException], _CLICK_RUNTIME.exceptions.ClickException)
_CLICK_EXIT = cast(type[BaseException], _CLICK_RUNTIME.exceptions.Exit)


class _ClickExceptionLike(Protocol):
    exit_code: int

    def format_message(self) -> str: ...


def _command_from_args(args: Sequence[str]) -> str:
    """Return a stable command identifier from a raw CLI argument vector."""

    tokens = [token for token in args if not token.startswith("-")]
    if not tokens:
        return "electrace"
    if tokens[0] == "workflow":
        return f"workflow.{tokens[1]}" if len(tokens) > 1 else "workflow"
    return tokens[0]


def _click_error_code(exc: BaseException) -> str:
    if type(exc).__name__ == "BadParameter" and "does not exist" in str(exc).casefold():
        return "not_found"
    return {
        "NoSuchOption": "invalid_option",
        "MissingParameter": "missing_parameter",
        "BadParameter": "invalid_input",
        "UsageError": "invalid_usage",
    }.get(type(exc).__name__, "invalid_input")


class _JsonAwareGroup(TyperGroup):
    """Render parse-time Click failures as JSON whenever ``--json`` is present."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        arguments = list(sys.argv[1:] if args is None else args)
        json_requested = any(
            argument == "--json" or argument.startswith("--json=") for argument in arguments
        )
        if not json_requested:
            return super().main(
                args=arguments,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        try:
            result = super().main(
                args=arguments,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except _CLICK_EXCEPTION as raw_exc:
            exc = cast(_ClickExceptionLike, raw_exc)
            sys.stdout.write(
                serialize(
                    failure(
                        _command_from_args(arguments),
                        _click_error_code(raw_exc),
                        exc.format_message(),
                    )
                )
            )
            if standalone_mode:
                raise SystemExit(exc.exit_code) from raw_exc
            raise _CLICK_EXIT(exc.exit_code) from raw_exc
        if isinstance(result, int) and result != 0:
            if standalone_mode:
                raise SystemExit(result)
            raise _CLICK_EXIT(result)
        return result


app = typer.Typer(
    name="electrace",
    cls=_JsonAwareGroup,
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
    data: object | None = None,
    *,
    warnings: Sequence[str] = (),
    artifacts: Sequence[object] = (),
) -> None:
    """Write one versioned JSON success envelope without terminal styling."""

    sys.stdout.write(
        serialize(
            success(
                command,
                data,
                warnings=warnings,
                artifacts=artifacts,
            )
        )
    )


def _exception_code(exc: BaseException) -> str:
    """Map handled CLI exceptions to stable machine-readable error codes."""

    if isinstance(exc, PermissionError):
        message = str(exc).casefold()
        return (
            "authorization_required"
            if "confirmed=true" in message or "--yes" in message
            else "permission_denied"
        )
    if isinstance(exc, ExportDependencyError):
        return "dependency_unavailable"
    if isinstance(exc, KeyError):
        return "unknown_resource"
    if isinstance(exc, RuntimeError):
        return "execution_error"
    return normalize_error_code(exc)


def _fail(
    message: str,
    *,
    code: int = 2,
    as_json: bool = False,
    command: str = "electrace",
    error_code: str = "invalid_input",
    details: object | None = None,
    artifacts: Sequence[object] = (),
) -> Never:
    if as_json:
        sys.stdout.write(
            serialize(
                failure(
                    command,
                    error_code,
                    message,
                    details=details,
                    artifacts=artifacts,
                )
            )
        )
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


def _load_optional_config(path: Path | None) -> ElecTraceConfig | None:
    if path is None:
        return None
    return load_config(path)


def _artifact(path: Path, role: str) -> dict[str, str]:
    return {"path": str(path.expanduser().resolve()), "role": role}


def _analysis_summary(analysis: dict[str, Any], source: Path) -> dict[str, Any]:
    project = analysis.get("project", {})
    if not isinstance(project, dict):
        raise ValueError("analysis project must be an object")
    collections: dict[str, list[Any]] = {}
    for name in ("frames", "tracks", "events", "warnings"):
        value = analysis.get(name, [])
        if not isinstance(value, list):
            raise ValueError(f"analysis {name} must be an array")
        collections[name] = value
    return {
        "source": str(source.expanduser().resolve()),
        "project": project,
        "counts": {
            "frames": len(collections["frames"]),
            "feature_observations": len(collections["tracks"]),
            "events": len(collections["events"]),
            "warnings": len(collections["warnings"]),
        },
    }


def _analysis_warnings(analysis: dict[str, Any]) -> tuple[str, ...]:
    value = analysis.get("warnings", [])
    if not isinstance(value, list):
        raise ValueError("analysis warnings must be an array")
    return tuple(str(item) for item in value)


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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
) -> None:
    """Create a reviewed starter configuration for an existing VASP path."""

    try:
        output = write_default_config(config, input_root=images, overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="init", as_json=as_json)
    if as_json:
        _emit_json(
            "init",
            {"configuration": str(output.resolve()), "images": str(images.resolve())},
            artifacts=(_artifact(output, "configuration"),),
        )
        return
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

    try:
        loaded = _load_optional_config(config)
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
) -> None:
    """Generate a deterministic synthetic path and optionally analyze it."""

    try:
        config_path = generate_demo(output, overwrite=force)
        artifacts = run_project(config_path, overwrite=force) if analyze else None
    except (OSError, TypeError, ValueError, ExportDependencyError) as exc:
        _fail_exception(exc, command="demo", as_json=as_json)
    if as_json:
        written = [_artifact(config_path, "configuration")]
        data: dict[str, Any] = {
            "output_directory": str(output.resolve()),
            "configuration": str(config_path.resolve()),
            "analyzed": artifacts is not None,
        }
        warnings: Sequence[str] = ()
        if artifacts is not None:
            written.extend(_artifact(path, "analysis_artifact") for path in artifacts.files)
            data["analysis"] = _analysis_summary(
                artifacts.analysis,
                artifacts.output_directory,
            )
            warnings = tuple(str(item) for item in artifacts.analysis.get("warnings", []))
        _emit_json("demo", data, warnings=warnings, artifacts=written)
        return
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
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
        _fail_exception(exc, command="benchmark", as_json=as_json)
    summary = cast(dict[str, Any], report["summary"])
    passed = benchmark_passed(report)
    status = "passed" if passed else "failed"
    color = "green" if passed else "red"
    if as_json:
        artifact = _artifact(output.resolve(), "benchmark_report")
        if passed:
            _emit_json(
                "benchmark",
                {"passed": True, "summary": summary},
                artifacts=(artifact,),
            )
            return
        _fail(
            "synthetic benchmark failed",
            code=1,
            as_json=True,
            command="benchmark",
            error_code="benchmark_failed",
            details={"passed": False, "summary": summary},
            artifacts=(artifact,),
        )
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
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
        _fail_exception(exc, command="analyze", as_json=as_json)
    analysis = artifacts.analysis
    if as_json:
        warnings = tuple(str(item) for item in analysis.get("warnings", []))
        _emit_json(
            "analyze",
            _analysis_summary(analysis, artifacts.output_directory),
            warnings=warnings,
            artifacts=tuple(_artifact(path, "analysis_artifact") for path in artifacts.files),
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
    target: Path = typer.Argument(
        ..., exists=True, resolve_path=True, help="Config, input path, or result bundle."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
) -> None:
    """Summarize a configuration, source series, or analysis bundle read-only."""

    try:
        analysis = _read_analysis(target)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="inspect", as_json=as_json)
    if analysis is not None:
        try:
            summary = _analysis_summary(analysis, target)
            warnings = _analysis_warnings(analysis)
            if as_json:
                _emit_json(
                    "inspect",
                    {"kind": "analysis", **summary},
                    warnings=warnings,
                )
                return
            _print_analysis_summary(analysis)
        except (OSError, TypeError, ValueError) as exc:
            _fail_exception(exc, command="inspect", as_json=as_json)
        return
    if target.is_file() and target.suffix.lower() == ".toml":
        try:
            configured_analysis, _ = load_and_analyze(target)
        except (OSError, TypeError, ValueError) as exc:
            _fail_exception(exc, command="inspect", as_json=as_json)
        try:
            summary = _analysis_summary(configured_analysis, target)
            warnings = _analysis_warnings(configured_analysis)
            if as_json:
                _emit_json(
                    "inspect",
                    {"kind": "configuration", **summary},
                    warnings=warnings,
                )
                return
            _print_analysis_summary(configured_analysis)
        except (OSError, TypeError, ValueError) as exc:
            _fail_exception(exc, command="inspect", as_json=as_json)
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
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Wrap the command plan in the versioned CLI JSON envelope.",
    ),
) -> None:
    """Print an external-tool command plan without executing it."""

    try:
        loaded = _load_optional_config(config)
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
            if as_json:
                _emit_json("plan", payload)
            else:
                console.print_json(data=payload)
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
        _fail_exception(exc, command="plan", as_json=as_json)
    if as_json:
        _emit_json("plan", command.to_dict())
    else:
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
) -> None:
    """Create a reviewed YAML workflow with explicit executable settings."""

    try:
        output = dump_starter_manifest(manifest, root=str(images), overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.init", as_json=as_json)
    if as_json:
        _emit_json(
            "workflow.init",
            {"manifest": str(output.resolve()), "images": str(images)},
            artifacts=(_artifact(output.resolve(), "workflow_manifest"),),
        )
        return
    console.print(f"[green]Created workflow[/green] {output.resolve()}")
    console.print("Set executable paths and provide reviewed VASP/template inputs before staging.")


@workflow_app.command("validate")
def workflow_validate(
    manifest: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="ElecTrace workflow YAML.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
) -> None:
    """Select frames and stage auditable jobs without executing anything."""

    try:
        staged = stage_workflow(manifest, overwrite=force)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        _fail_exception(exc, command="workflow.stage", as_json=as_json)
    if as_json:
        _emit_json(
            "workflow.stage",
            staged.to_dict(),
            artifacts=(_artifact(staged.record_path, "workspace_record"),),
        )
        return
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
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
                    artifacts=tuple(_artifact(path, "slurm_script") for path in scripts),
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
        partial_artifacts = tuple(
            _artifact(Path(path), "submitted_slurm_script") for path in exc.partial_submissions
        )
        _fail(
            str(exc),
            code=1,
            as_json=as_json,
            command="workflow.submit",
            error_code="slurm_submission_failed",
            details=details,
            artifacts=(
                *partial_artifacts,
                _artifact(exc.record_path, "slurm_submission_record"),
            ),
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
            artifacts=(
                *(_artifact(Path(path), "submitted_slurm_script") for path in submissions),
                _artifact(record_path, "slurm_submission_record"),
            ),
        )
        return
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
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Wrap status in the versioned CLI JSON envelope.",
    ),
) -> None:
    """Report which staged inputs and expected outputs currently exist."""

    try:
        payload = workflow_status(manifest)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.status", as_json=as_json)
    if as_json:
        _emit_json("workflow.status", payload)
    else:
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
) -> None:
    """Validate and collect every selected descriptor output for analysis."""

    try:
        collected = collect_workflow_outputs(manifest, overwrite=force)
    except (OSError, TypeError, ValueError) as exc:
        _fail_exception(exc, command="workflow.collect", as_json=as_json)
    if as_json:
        _emit_json(
            "workflow.collect",
            {
                "configuration": str(collected.config_path),
                "input_root": str(collected.input_root),
                "descriptor_root": str(collected.descriptor_root),
                "files": [str(path) for path in collected.files],
            },
            artifacts=tuple(_artifact(path, "collected_artifact") for path in collected.files),
        )
        return
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
    as_json: bool = typer.Option(False, "--json", help="Emit a versioned JSON envelope."),
) -> None:
    """Collect completed outputs, track changes, and generate the result bundle."""

    try:
        artifacts = analyze_collected_workflow(
            manifest,
            strict=strict,
            overwrite=force,
        )
    except (OSError, TypeError, ValueError, ExportDependencyError) as exc:
        _fail_exception(exc, command="workflow.analyze", as_json=as_json)
    analysis = artifacts.analysis
    if as_json:
        warnings = tuple(str(item) for item in analysis.get("warnings", []))
        _emit_json(
            "workflow.analyze",
            _analysis_summary(analysis, artifacts.output_directory),
            warnings=warnings,
            artifacts=tuple(_artifact(path, "analysis_artifact") for path in artifacts.files),
        )
        return
    console.print(f"[green]Workflow analysis complete[/green]: {artifacts.output_directory}")
    console.print(
        f"{len(analysis['frames'])} frames · {len(analysis['tracks'])} feature observations · "
        f"{len(analysis['events'])} events · {len(analysis['warnings'])} warnings"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
