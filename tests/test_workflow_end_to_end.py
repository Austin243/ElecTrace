import shutil
from pathlib import Path

import yaml

from electrace.workflow.collection import analyze_collected_workflow, collect_workflow_outputs
from electrace.workflow.operations import run_staged_workflow
from electrace.workflow.staging import stage_workflow


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def test_stage_run_collect_analyze_end_to_end(tmp_path, workflow_manifest):
    vasp = _executable(
        tmp_path / "vasp.py",
        "from pathlib import Path\nimport shutil\n"
        "Path('CHGCAR').write_text('density')\nshutil.copyfile('POSCAR', 'CONTCAR')\n",
    )
    bader = _executable(
        tmp_path / "bader.py",
        "from pathlib import Path\n"
        "i=int(Path.cwd().parent.name.split('-', 1)[0])\n"
        "Path('ACF.dat').write_text(f'# X Y Z CHARGE MIN DIST ATOMIC VOL\\n'"
        "+f'1 0 0 0 {1.0+i} 0 10\\n')\n",
    )
    manifest = workflow_manifest(
        executables={"vasp": str(vasp), "bader": str(bader)},
        steps=[
            {
                "id": "density",
                "kind": "vasp",
                "executable": "vasp",
                "options": {
                    "required_inputs": ["POSCAR"],
                    "expected_outputs": ["CHGCAR", "CONTCAR"],
                },
            },
            {
                "id": "charges",
                "kind": "bader",
                "executable": "bader",
                "depends_on": ["density"],
            },
        ],
    )

    staged = stage_workflow(manifest)
    results = run_staged_workflow(manifest, confirmed=True, max_parallel=2)
    collected = collect_workflow_outputs(manifest)
    artifacts = analyze_collected_workflow(manifest)

    assert len(staged.frames) == 2
    assert len(results) == 4 and all(result.succeeded for result in results)
    assert len(list(collected.descriptor_root.rglob("ACF.dat"))) == 2
    assert len(artifacts.analysis["frames"]) == 2
    assert (artifacts.output_directory / "analysis.json").is_file()


def test_collected_analysis_keeps_the_staged_order_despite_outcar_stress(
    tmp_path, workflow_manifest
):
    # An OUTCAR is collected as the ZVAL source when a frame has no POTCAR, and its stress
    # must not sort the collected frames.
    bader = _executable(
        tmp_path / "bader.py",
        "from pathlib import Path\n"
        "Path('ACF.dat').write_text('# X Y Z CHARGE MIN DIST ATOMIC VOL\\n1 0 0 0 1.0 0 10\\n')\n",
    )
    manifest = workflow_manifest(
        executables={"bader": str(bader)},
        steps=[
            {
                "id": "charges",
                "kind": "bader",
                "executable": "bader",
                "options": {
                    "input_links": {"CHGCAR": "source:structure"},
                },
            }
        ],
    )
    for label, stress in (("00", 50.0), ("01", -50.0)):
        (tmp_path / "images" / label / "OUTCAR").write_text(
            f"  external pressure = {stress} kB  Pullay stress = 0.00 kB\n", encoding="utf-8"
        )

    stage_workflow(manifest)
    assert all(result.succeeded for result in run_staged_workflow(manifest, confirmed=True))
    analysis = analyze_collected_workflow(manifest).analysis

    assert [frame["label"] for frame in analysis["frames"]] == ["000000-00", "000001-01"]
    assert analysis["project"]["series_kind"] == "static"


def test_workflow_named_by_pressure_stays_a_pressure_series(tmp_path, workflow_manifest):
    """The counterexample: "ordered" in the collected config does not lose a pressure series."""

    bader = _executable(
        tmp_path / "bader.py",
        "from pathlib import Path\n"
        "Path('ACF.dat').write_text('# X Y Z CHARGE MIN DIST ATOMIC VOL\\n1 0 0 0 1.0 0 10\\n')\n",
    )
    manifest = workflow_manifest(
        executables={"bader": str(bader)},
        steps=[
            {
                "id": "charges",
                "kind": "bader",
                "executable": "bader",
                "options": {"input_links": {"CHGCAR": "source:structure"}},
            }
        ],
    )
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["project"]["series_kind"] = "pressure"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    shutil.move(tmp_path / "images/00", tmp_path / "images/5GPa")
    shutil.move(tmp_path / "images/01", tmp_path / "images/10GPa")

    stage_workflow(manifest)
    assert all(result.succeeded for result in run_staged_workflow(manifest, confirmed=True))
    analysis = analyze_collected_workflow(manifest).analysis

    assert [frame["label"] for frame in analysis["frames"]] == ["000000-5GPa", "000001-10GPa"]
    assert analysis["project"]["series_kind"] == "pressure"
    assert analysis["barrier"]["frames"] == []
