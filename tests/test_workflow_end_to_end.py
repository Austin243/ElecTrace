from __future__ import annotations

import json
from pathlib import Path

import yaml
from rich.text import Text
from typer.testing import CliRunner

from electrace.cli import app
from electrace.workflow.collection import (
    analyze_collected_workflow,
    collect_workflow_outputs,
)
from electrace.workflow.operations import run_staged_workflow
from electrace.workflow.staging import stage_workflow

runner = CliRunner()

POSCAR = """\
H2
1.0
2 0 0
0 2 0
0 0 2
H
2
Direct
0.0 0.0 0.0
0.5 0.0 0.0
"""


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o700)


def _end_to_end_manifest(tmp_path: Path) -> Path:
    images = tmp_path / "images"
    for label in ("00", "01"):
        frame = images / label
        frame.mkdir(parents=True)
        (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")

    vasp_template = tmp_path / "templates" / "vasp"
    vasp_template.mkdir(parents=True)
    (vasp_template / "INCAR").write_text("ENCUT = 300\n", encoding="utf-8")
    (vasp_template / "KPOINTS").write_text("Gamma\n0\nGamma\n1 1 1\n0 0 0\n", encoding="utf-8")
    (vasp_template / "POTCAR").write_text("licensed-test-placeholder\n", encoding="utf-8")
    lobster_template = tmp_path / "templates" / "lobster"
    lobster_template.mkdir(parents=True)
    (lobster_template / "lobsterin").write_text("cohpGenerator from 0.1 to 2.0\n", encoding="utf-8")

    _write_executable(
        tmp_path / "fake_vasp.py",
        "from pathlib import Path\n"
        "import shutil\n"
        "shutil.copyfile('POSCAR', 'CONTCAR')\n"
        "for name in ('CHGCAR', 'ELFCAR', 'WAVECAR'):\n"
        "    Path(name).write_text(name + ' fixture')\n",
    )
    _write_executable(
        tmp_path / "fake_baderkit.py",
        "from pathlib import Path\n"
        "import json\n"
        "index = int(Path.cwd().parent.name.split('-', 1)[0])\n"
        "oxidation = float(index)\n"
        "payload = {'method_kwargs': {'version': 'test'}, "
        "'atom_results': {'atom_charges': [1.0, 1.0], "
        "'oxidation_states': [oxidation, -oxidation], 'atom_volumes': [4.0, 4.0]}, "
        "'maxima_results': {}}\n"
        "Path('badelf.json').write_text(json.dumps(payload))\n",
    )
    _write_executable(
        tmp_path / "fake_lobster.py",
        "from pathlib import Path\n"
        "index = int(Path.cwd().parent.name.split('-', 1)[0])\n"
        "value = -1.0 - index\n"
        "Path('ICOHPLIST.lobster').write_text("
        "f'No. atom1 atom2 distance translation ICOHP\\n'"
        "+ f'1 H1 H2 1.00 0 0 0 {value}\\n')\n",
    )

    payload = {
        "schema_version": "electrace.workflow.v1",
        "project": {
            "root": "images",
            "series_kind": "neb",
            "workspace": "workspace",
            "structure": "CONTCAR",
            "fallback": "POSCAR",
        },
        "frames": "all",
        "executables": {
            "vasp": {"path": "./fake_vasp.py"},
            "baderkit": {"path": "./fake_baderkit.py"},
            "lobster": {"path": "./fake_lobster.py"},
        },
        "scheduler": {"kind": "local", "directives": {}},
        "steps": [
            {
                "id": "vasp_elf",
                "kind": "vasp",
                "executable": "vasp",
                "template_dir": "templates/vasp",
                "options": {
                    "expected_outputs": ["CHGCAR", "ELFCAR", "CONTCAR"],
                    "incar_overrides": {"LELF": True, "LCHARG": True},
                },
            },
            {
                "id": "badelf",
                "kind": "baderkit",
                "executable": "baderkit",
                "capability": "elf_basins",
                "depends_on": ["vasp_elf"],
            },
            {
                "id": "vasp_lobster",
                "kind": "vasp",
                "executable": "vasp",
                "template_dir": "templates/vasp",
                "options": {
                    "expected_outputs": ["WAVECAR", "CONTCAR"],
                    "incar_overrides": {"NSW": 0, "ISYM": -1, "LWAVE": True},
                },
            },
            {
                "id": "icohp",
                "kind": "lobster",
                "executable": "lobster",
                "capability": "cohp",
                "template_dir": "templates/lobster",
                "depends_on": ["vasp_lobster"],
            },
        ],
    }
    manifest = tmp_path / "workflow.yaml"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return manifest


def test_full_fake_external_workflow_collects_and_analyzes_changes(tmp_path: Path) -> None:
    manifest = _end_to_end_manifest(tmp_path)
    staged = stage_workflow(manifest)

    results = run_staged_workflow(manifest, confirmed=True, max_parallel=2)

    assert len(results) == 8
    assert all(result.succeeded for result in results)
    collected = collect_workflow_outputs(manifest)
    assert sorted(path.name for path in collected.descriptor_root.rglob("*.*")) == [
        "ICOHPLIST.lobster",
        "ICOHPLIST.lobster",
        "badelf.json",
        "badelf.json",
    ]

    artifacts = analyze_collected_workflow(manifest)

    analysis = artifacts.analysis
    assert len(analysis["frames"]) == 2
    assert {frame["label"] for frame in analysis["frames"]} == {
        staged.frames[0].analysis_label,
        staged.frames[1].analysis_label,
    }
    serialized_events = json.dumps(analysis["events"], sort_keys=True)
    assert "oxidation_state" in serialized_events
    assert "icohp" in serialized_events
    assert (artifacts.output_directory / "analysis.json").is_file()


def test_workflow_cli_runs_collects_and_analyzes_end_to_end(tmp_path: Path) -> None:
    manifest = _end_to_end_manifest(tmp_path)

    staged = runner.invoke(app, ["workflow", "stage", str(manifest)])
    executed = runner.invoke(
        app,
        ["workflow", "run", str(manifest), "--yes", "--max-parallel", "2"],
    )
    collected = runner.invoke(app, ["workflow", "collect", str(manifest)])
    analyzed = runner.invoke(app, ["workflow", "analyze", str(manifest)])

    assert staged.exit_code == 0, staged.output
    assert executed.exit_code == 0, executed.output
    assert collected.exit_code == 0, collected.output
    assert analyzed.exit_code == 0, analyzed.output
    assert "2 frames" in Text.from_ansi(analyzed.stdout).plain


def test_volume_cli_reads_and_samples_plain_elfcar(tmp_path: Path) -> None:
    elfcar = tmp_path / "ELFCAR"
    elfcar.write_text(
        "H ELF\n1\n1 0 0\n0 1 0\n0 0 1\nH\n1\nDirect\n0 0 0\n\n2 1 1\n0.0 1.0\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["volume", str(elfcar), "--sample", "0.25,0,0", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(Text.from_ansi(result.stdout).plain)
    assert payload["shape"] == [2, 1, 1]
    assert payload["sample"]["value"] == 0.5
