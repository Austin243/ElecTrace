"""Built-in command planners for supported external science programs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Capability, CommandPlan, DoctorReport, resolve_executable
from .critic2_input import ALL_ELECTRON_FILES, PERIODIC_INPUT_NAME, seed_settings


def _directory(path: str | Path) -> Path:
    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"frame directory does not exist: {directory}")
    return directory


def _filename(value: str, option: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{option} must be a string")
    name = value.strip()
    if (
        not name
        or name in {".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or any(character.isspace() for character in name)
        or "/" in name
        or "\\" in name
        or Path(name).is_absolute()
    ):
        raise ValueError(f"{option} must be a whitespace-free basename, not a path: {value!r}")
    return name


def _fixed_filename(value: str, expected: str, option: str, plugin: str) -> str:
    """Reject aliases when an executable itself reads a conventional filename."""

    name = _filename(value, option)
    if name != expected:
        raise ValueError(
            f"{plugin} reads the fixed filename {expected!r}; "
            f"custom {option}={name!r} is not supported without an explicit staging step"
        )
    return name


def _capability(
    requested: Capability | str | None,
    supported: tuple[Capability, ...],
    default: Capability,
) -> Capability:
    if requested is None:
        return default
    try:
        value = requested if isinstance(requested, Capability) else Capability(str(requested))
    except ValueError as exc:
        raise ValueError(f"unknown plugin capability {requested!r}") from exc
    if value not in supported:
        choices = ", ".join(item.value for item in supported)
        raise ValueError(f"capability {value.value!r} is not supported; choose one of: {choices}")
    return value


class _BuiltinPlugin:
    name = ""
    default_executable = ""
    capabilities: tuple[Capability, ...] = ()

    def plan(
        self,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        **options: Any,
    ) -> CommandPlan:
        raise NotImplementedError

    def doctor(
        self,
        frame_directory: str | Path | None = None,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        **options: Any,
    ) -> DoctorReport:
        command = executable or self.default_executable
        path = resolve_executable(command)
        missing: tuple[Path, ...] = ()
        messages: list[str] = []
        if path is None:
            messages.append(
                f"executable {command!r} was not found; configure [plugins.{self.name}].executable"
            )
        if frame_directory is not None:
            directory = _directory(frame_directory)
            plan = self.plan(
                directory,
                capability=capability,
                executable=command,
                **options,
            )
            missing = plan.missing_inputs
            if missing:
                messages.append("required frame inputs are missing")
        return DoctorReport(
            plugin=self.name,
            executable=command,
            executable_path=path,
            capabilities=self.capabilities,
            missing_inputs=missing,
            messages=tuple(messages),
        )


class BaderKitPlugin(_BuiltinPlugin):
    """Planner for BaderKit Bader/BadELF analyses."""

    name = "baderkit"
    default_executable = "baderkit"
    capabilities: tuple[Capability, ...] = (
        Capability.BADER_CHARGES,
        Capability.ELF_BASINS,
        Capability.NON_NUCLEAR_ATTRACTORS,
    )

    def plan(
        self,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        charge_file: str = "CHGCAR",
        reference_file: str | None = None,
        elf_file: str = "ELFCAR",
        **options: Any,
    ) -> CommandPlan:
        if options:
            raise TypeError(f"unknown BaderKit plan options: {', '.join(sorted(options))}")
        directory = _directory(frame_directory)
        selected = _capability(capability, self.capabilities, Capability.BADER_CHARGES)
        command = executable or self.default_executable
        charge = _filename(charge_file, "charge_file")
        elf = _filename(elf_file, "elf_file")
        if selected is Capability.BADER_CHARGES:
            argv = [command, "bader", charge]
            required = [directory / charge]
            if reference_file:
                reference = _filename(reference_file, "reference_file")
                argv.extend(["-tot", reference])
                required.append(directory / reference)
            output = "bader.json"
        else:
            argv = [command, "badelf", charge, elf]
            required = [directory / elf, directory / charge]
            if reference_file:
                reference = _filename(reference_file, "reference_file")
                argv.extend(["-tot", reference])
                required.append(directory / reference)
            output = "badelf.json"
        return CommandPlan(
            plugin=self.name,
            capability=selected,
            argv=tuple(argv),
            cwd=directory,
            required_inputs=tuple(required),
            expected_outputs=(directory / output,),
            notes=(f"BaderKit writes {output} in the working directory.",),
        )


class BaderPlugin(_BuiltinPlugin):
    """Planner for the Henkelman-group ``bader`` executable."""

    name = "bader"
    default_executable = "bader"
    capabilities: tuple[Capability, ...] = (
        Capability.BADER_CHARGES,
        Capability.SPIN_POPULATIONS,
    )

    def plan(
        self,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        charge_file: str | None = None,
        reference_file: str | None = None,
        **options: Any,
    ) -> CommandPlan:
        if options:
            raise TypeError(f"unknown Bader plan options: {', '.join(sorted(options))}")
        directory = _directory(frame_directory)
        selected = _capability(capability, self.capabilities, Capability.BADER_CHARGES)
        spin = selected is Capability.SPIN_POPULATIONS
        if spin and reference_file is None:
            raise ValueError(
                "spin_populations needs reference_file, the total density whose basins "
                "the magnetization is integrated in, such as CHGCAR_sum or CHGCAR_tot"
            )
        default_charge = "CHGCAR_mag" if spin else "CHGCAR"
        charge = _filename(
            charge_file if charge_file is not None else default_charge, "charge_file"
        )
        argv = [executable or self.default_executable, charge]
        required = [directory / charge]
        if reference_file is not None:
            reference = _filename(reference_file, "reference_file")
            argv.extend(("-ref", reference))
            required.append(directory / reference)
        note = (
            "ACF.dat holds each atom's magnetization (spin up minus spin down) integrated "
            "in the reference density's Bader basins; analysis reads it as spin/ACF.dat."
            if spin
            else "ACF.dat contains partition-dependent Bader electron populations, not formal oxidation states."
        )
        return CommandPlan(
            plugin=self.name,
            capability=selected,
            argv=tuple(argv),
            cwd=directory,
            required_inputs=tuple(required),
            expected_outputs=(directory / "ACF.dat",),
            notes=(note,),
        )


class Critic2Plugin(_BuiltinPlugin):
    """Planner for critic2 QTAIM and ELF topology calculations."""

    name = "critic2"
    default_executable = "critic2"
    capabilities: tuple[Capability, ...] = (
        Capability.QTAIM_CRITICAL_POINTS,
        Capability.NON_NUCLEAR_ATTRACTORS,
    )

    def plan(
        self,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        structure_file: str = "CONTCAR",
        field_file: str | None = None,
        input_file: str | None = None,
        report_file: str = "critic2.features.json",
        graph: bool = True,
        seeds: str = "auto",
        seed_distance: float | None = None,
        seed_points: int | None = None,
        seed_triplets: bool | None = None,
        **options: Any,
    ) -> CommandPlan:
        if options:
            raise TypeError(f"unknown critic2 plan options: {', '.join(sorted(options))}")
        directory = _directory(frame_directory)
        selected = _capability(capability, self.capabilities, Capability.QTAIM_CRITICAL_POINTS)
        command = executable or self.default_executable
        structure = _filename(structure_file, "structure_file")
        if seeds == "periodic":
            return self._periodic_plan(
                directory,
                selected,
                command,
                structure,
                _json_report(report_file),
                field_file is not None or input_file is not None,
                seed_settings(seed_distance, seed_points, seed_triplets),
            )
        if seeds != "auto":
            raise ValueError(f"critic2 seeds must be 'auto' or 'periodic', not {seeds!r}")
        if (seed_distance, seed_points, seed_triplets) != (None, None, None):
            raise ValueError(
                "seed_distance, seed_points, and seed_triplets apply only with seeds = 'periodic'"
            )
        default_field = "ELFCAR" if selected is Capability.NON_NUCLEAR_ATTRACTORS else "CHGCAR"
        field = _filename(field_file or default_field, "field_file")
        report = _json_report(report_file)
        required = [directory / structure, directory / field]
        argv: tuple[str, ...]
        if input_file:
            input_name = _filename(input_file, "input_file")
            input_path = directory / input_name
            required.append(input_path)
            argv = (command, "-q", input_name, "critic2.cro")
            stdin = None
            stdout = None
        else:
            cpreport = f"CPREPORT {report}" + (" GRAPH" if graph else "")
            stdin = f"CRYSTAL {structure}\nLOAD {field}\nAUTO\n{cpreport}\nEND\n"
            argv = (command, "-q")
            stdout = directory / "critic2.cro"
        return CommandPlan(
            plugin=self.name,
            capability=selected,
            argv=argv,
            cwd=directory,
            required_inputs=tuple(required),
            expected_outputs=(directory / "critic2.cro", directory / report),
            stdin=stdin,
            stdout_path=stdout,
            notes=(
                "The generated critic2 input is a conservative starting point; review field and integration settings for publication runs.",
                "The plan uses critic2's native CPREPORT JSON handoff; ElecTrace does not parse critic2.cro.",
                *(
                    (
                        f"The custom critic2 input must contain 'CPREPORT {report}' to create the declared JSON output.",
                    )
                    if input_file
                    else ()
                ),
            ),
        )

    def _periodic_plan(
        self,
        directory: Path,
        selected: Capability,
        command: str,
        structure: str,
        report: str,
        custom_field: bool,
        settings: tuple[float, int, bool],
    ) -> CommandPlan:
        """Run critic2 on the input that workflow staging writes for periodic seeds."""

        if selected is not Capability.QTAIM_CRITICAL_POINTS:
            raise ValueError(
                "seeds = 'periodic' searches the all-electron density, so it applies to "
                "qtaim_critical_points only"
            )
        if custom_field:
            raise ValueError(
                f"seeds = 'periodic' writes its own {PERIODIC_INPUT_NAME} from "
                "AECCAR0 + AECCAR2; field_file and input_file do not apply"
            )
        distance, points, triplets = settings
        triplet_text = ", and atom-triplet barycenters" if triplets else ""
        return CommandPlan(
            plugin=self.name,
            capability=selected,
            argv=(command, "-q", PERIODIC_INPUT_NAME, "critic2.cro"),
            cwd=directory,
            required_inputs=tuple(
                directory / name for name in (structure, *ALL_ELECTRON_FILES, PERIODIC_INPUT_NAME)
            ),
            expected_outputs=(directory / "critic2.cro", directory / report),
            notes=(
                f"{PERIODIC_INPUT_NAME} searches AECCAR0 + AECCAR2 with SMOOTHRHO from "
                f"{points}-point lines between atom pairs within {distance:g} bohr, counting "
                f"periodic images{triplet_text}.",
                f"electrace workflow stage writes {PERIODIC_INPUT_NAME} from {structure}; the "
                "density must come from a calculation at that structure (NSW = 0).",
                "The plan uses critic2's native CPREPORT JSON handoff; ElecTrace does not parse critic2.cro.",
            ),
        )


def _json_report(value: str) -> str:
    report = _filename(value, "report_file")
    if Path(report).suffix.lower() != ".json":
        raise ValueError("critic2 report_file must have a .json suffix for CPREPORT JSON")
    return report


class LobsterPlugin(_BuiltinPlugin):
    """Planner for an existing LOBSTER input in a VASP calculation directory."""

    name = "lobster"
    default_executable = "lobster"
    capabilities: tuple[Capability, ...] = (Capability.COHP, Capability.COBI)

    def plan(
        self,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        input_file: str = "lobsterin",
        wavecar: str = "WAVECAR",
        structure_file: str = "POSCAR",
        potcar: str = "POTCAR",
        **options: Any,
    ) -> CommandPlan:
        if options:
            raise TypeError(f"unknown LOBSTER plan options: {', '.join(sorted(options))}")
        directory = _directory(frame_directory)
        selected = _capability(capability, self.capabilities, Capability.COHP)
        output = "ICOHPLIST.lobster" if selected is Capability.COHP else "ICOBILIST.lobster"
        input_name = _fixed_filename(input_file, "lobsterin", "input_file", "LOBSTER")
        wavecar_name = _fixed_filename(wavecar, "WAVECAR", "wavecar", "LOBSTER")
        structure_name = _fixed_filename(structure_file, "POSCAR", "structure_file", "LOBSTER")
        potcar_name = _fixed_filename(potcar, "POTCAR", "potcar", "LOBSTER")
        return CommandPlan(
            plugin=self.name,
            capability=selected,
            argv=(executable or self.default_executable,),
            cwd=directory,
            required_inputs=tuple(
                directory / name for name in (input_name, wavecar_name, structure_name, potcar_name)
            ),
            expected_outputs=(directory / output,),
            notes=("LOBSTER reads its filenames and requested bond analyses from lobsterin.",),
        )


class ChargemolPlugin(_BuiltinPlugin):
    """Planner for Chargemol's job-control driven DDEC analysis."""

    name = "chargemol"
    default_executable = "chargemol"
    capabilities: tuple[Capability, ...] = (Capability.DDEC_CHARGES,)

    def plan(
        self,
        frame_directory: str | Path,
        *,
        capability: Capability | str | None = None,
        executable: str | None = None,
        job_control: str = "job_control.txt",
        **options: Any,
    ) -> CommandPlan:
        if options:
            raise TypeError(f"unknown Chargemol plan options: {', '.join(sorted(options))}")
        directory = _directory(frame_directory)
        selected = _capability(capability, self.capabilities, Capability.DDEC_CHARGES)
        job_control_name = _fixed_filename(
            job_control, "job_control.txt", "job_control", "Chargemol"
        )
        return CommandPlan(
            plugin=self.name,
            capability=selected,
            argv=(executable or self.default_executable,),
            cwd=directory,
            required_inputs=(directory / job_control_name,),
            expected_outputs=(directory / "DDEC6_even_tempered_net_atomic_charges.xyz",),
            notes=("Chargemol reads density paths and settings from job_control.txt.",),
        )


BUILTIN_PLUGINS = (
    BaderPlugin(),
    BaderKitPlugin(),
    Critic2Plugin(),
    LobsterPlugin(),
    ChargemolPlugin(),
)
