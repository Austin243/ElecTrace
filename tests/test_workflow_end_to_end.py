from pathlib import Path

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
