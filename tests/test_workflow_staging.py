import shutil

import pytest
import yaml
from typer.testing import CliRunner

from electrace.cli import app
from electrace.workflow import validate_workflow
from electrace.workflow.operations import run_staged_workflow
from electrace.workflow.staging import (
    _apply_incar_overrides,
    load_staged_workflow,
    stage_workflow,
)


def test_stage_and_reload_preserve_source(tmp_path, poscar_text, workflow_manifest):
    manifest = workflow_manifest()
    staged = stage_workflow(manifest)

    assert [job.id for job in staged.jobs] == ["f000000.density", "f000001.density"]
    assert staged.jobs[0].required_inputs == (staged.jobs[0].cwd / "POSCAR",)
    assert (tmp_path / "images/00/POSCAR").read_text(encoding="utf-8") == poscar_text
    assert load_staged_workflow(manifest).to_dict() == staged.to_dict()


def test_stage_refuses_source_overlap_and_unmarked_overwrite(tmp_path, workflow_manifest):
    with pytest.raises(ValueError, match="must not overlap"):
        stage_workflow(workflow_manifest(base=tmp_path / "overlap", workspace="images/work"))

    manifest = workflow_manifest(base=tmp_path / "unmarked")
    workspace = tmp_path / "unmarked/workspace"
    workspace.mkdir()
    sentinel = workspace / "keep"
    sentinel.write_text("user data", encoding="utf-8")
    with pytest.raises(ValueError, match="unmarked"):
        stage_workflow(manifest, overwrite=True)
    assert sentinel.read_text(encoding="utf-8") == "user data"


def test_validate_and_stage_warn_when_names_do_not_set_the_frame_order(tmp_path, workflow_manifest):
    manifest = workflow_manifest()
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["project"]["series_kind"] = "ordered"
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    shutil.move(tmp_path / "images/00", tmp_path / "images/IS")
    shutil.move(tmp_path / "images/01", tmp_path / "images/FS")

    expected = "natural order of their names (FS, IS)"
    assert any(expected in warning for warning in validate_workflow(manifest).warnings)
    assert any(expected in warning for warning in stage_workflow(manifest).warnings)


def test_numeric_workflow_frames_get_no_order_warning(workflow_manifest):
    manifest = workflow_manifest()

    assert not any("natural order" in item for item in validate_workflow(manifest).warnings)
    assert stage_workflow(manifest).warnings == ()


@pytest.mark.parametrize(
    ("template", "overrides", "expected"),
    [
        pytest.param(
            "ENCUT = 520; ISPIN = 2\nPREC = Accurate\n",
            {"ENCUT": 400},
            "ENCUT = 400; ISPIN = 2\nPREC = Accurate\n",
            id="first-on-a-shared-line-keeps-the-other-tag",
        ),
        pytest.param(
            "ISPIN = 2; ENCUT = 520\n",
            {"ENCUT": 400},
            "ISPIN = 2; ENCUT = 400\n",
            id="second-on-a-shared-line-is-set-where-it-stands",
        ),
        pytest.param(
            "ISMEAR = 0 ; ENCUT = 520 ; ISPIN = 2 ! spin\n",
            {"ENCUT": 400},
            "ISMEAR = 0 ; ENCUT = 400 ; ISPIN = 2 ! spin\n",
            id="shared-line-keeps-spacing-and-comment",
        ),
        pytest.param(
            "ENCUT = 520\nISMEAR = 0\nENCUT = 600\n",
            {"ENCUT": 400},
            "ENCUT = 400\nISMEAR = 0\nENCUT = 400\n",
            id="every-duplicate-is-set",
        ),
        pytest.param(
            "MAGMOM = 1 1 \\\n  2 2\nISPIN = 2\n",
            {"MAGMOM": "4*0"},
            "MAGMOM = 4*0\nISPIN = 2\n",
            id="continuation-lines-go-with-their-statement",
        ),
        pytest.param(
            "MAGMOM = 1 1 \\\n  ENCUT = 2\nENCUT = 520\n",
            {"ENCUT": 400},
            "MAGMOM = 1 1 \\\n  ENCUT = 2\nENCUT = 400\n",
            id="continuation-of-another-tag-stays",
        ),
        pytest.param(
            'SYSTEM = "a; ENCUT = 1"\nENCUT = 520\n',
            {"ENCUT": 400},
            'SYSTEM = "a; ENCUT = 1"\nENCUT = 400\n',
            id="quoted-text-is-not-a-statement",
        ),
        pytest.param(
            'WANNIER90_WIN = "\nENCUT = 1\n"\nENCUT = 520\n',
            {"ENCUT": 400},
            'WANNIER90_WIN = "\nENCUT = 1\n"\nENCUT = 400\n',
            id="multiline-quoted-value-of-another-tag-stays",
        ),
        pytest.param(
            'WANNIER90_WIN = "\nnum_wann = 8\n"\nISPIN = 2\n',
            {"WANNIER90_WIN": '"x"'},
            'WANNIER90_WIN = "x"\nISPIN = 2\n',
            id="multiline-quoted-value-goes-with-its-statement",
        ),
        pytest.param(
            "PLUGINS { STRUCTURE = T ; NEIGHBOR_CUTOFF = 5.0 }\n",
            {"NEIGHBOR_CUTOFF": 6},
            "PLUGINS { STRUCTURE = T ; NEIGHBOR_CUTOFF = 5.0 }\nNEIGHBOR_CUTOFF = 6\n",
            id="tag-inside-a-one-line-block-stays",
        ),
        pytest.param(
            "ISMEAR = 0\nELPH {\n  ISMEAR = -15\n  NBANDS = 200\n}\n",
            {"ISMEAR": 1},
            "ISMEAR = 1\nELPH {\n  ISMEAR = -15\n  NBANDS = 200\n}\n",
            id="nested-tag-of-the-same-name-stays",
        ),
        pytest.param(
            "ENCUT = 500\nELPH {\n  ISMEAR = -15\n}\n",
            {"ISMEAR": 1},
            "ENCUT = 500\nELPH {\n  ISMEAR = -15\n}\nISMEAR = 1\n",
            id="nested-tag-is-not-the-top-level-tag",
        ),
        pytest.param(
            "PLUGINS { STRUCTURE = T ; NEIGHBOR_CUTOFF = 5.0 } ; ENCUT = 520\n",
            {"ENCUT": 400},
            "PLUGINS { STRUCTURE = T ; NEIGHBOR_CUTOFF = 5.0 } ; ENCUT = 400\n",
            id="statement-after-a-closed-block-is-set",
        ),
        pytest.param(
            "OUTER {\n  NOTE = a{b}\n  ENCUT = 1\n}\nENCUT = 520\n",
            {"ENCUT": 400},
            "OUTER {\n  NOTE = a{b}\n  ENCUT = 1\n}\nENCUT = 400\n",
            id="braces-in-a-nested-value-do-not-end-the-block",
        ),
        pytest.param(
            "SYSTEM = Ti{001 slab\nENCUT = 520\n",
            {"ENCUT": 400},
            "SYSTEM = Ti{001 slab\nENCUT = 400\n",
            id="brace-in-a-value-opens-no-block",
        ),
        pytest.param(
            'SYSTEM = "{"\nENCUT = 520\n',
            {"ENCUT": 400},
            'SYSTEM = "{"\nENCUT = 400\n',
            id="quoted-brace-opens-no-block",
        ),
        pytest.param(
            'SYSTEM = my "run\nENCUT = 520\n',
            {"ENCUT": 400},
            'SYSTEM = my "run\nENCUT = 400\n',
            id="unmatched-quote-does-not-hide-later-tags",
        ),
        pytest.param(
            "ISPIN = 2 \\",
            {"NSW": 0},
            "ISPIN = 2 \\\nNSW = 0\n",
            id="backslash-at-the-very-end-keeps-the-line",
        ),
    ],
)
def test_incar_override_sets_only_the_statement_it_names(tmp_path, template, overrides, expected):
    incar = tmp_path / "INCAR"
    incar.write_text(template, encoding="utf-8")

    _apply_incar_overrides(incar, overrides)

    assert incar.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    ("template", "overrides", "expected"),
    [
        (
            "ENCUT = 520 ! cutoff\n  ISMEAR=0\n# ENCUT = 300\nSIGMA = 0.05\n",
            {"encut": 400, "LWAVE": False},
            "ENCUT = 400\n  ISMEAR=0\n# ENCUT = 300\nSIGMA = 0.05\nLWAVE = .FALSE.\n",
        ),
        ("  encut=520  # note\n", {"ENCUT": 400}, "ENCUT = 400\n"),
        ("ENCUT = 520 ;\n", {"ENCUT": 400}, "ENCUT = 400\n"),
        ("ISPIN = 2 ! ENCUT = 520\n", {"ENCUT": 400}, "ISPIN = 2 ! ENCUT = 520\nENCUT = 400\n"),
        ("ISPIN = 2\n\n\n", {"NSW": 0}, "ISPIN = 2\n\n\nNSW = 0\n"),
        ("LCHARG = T\r\nNSW = 5\r\n", {"NSW": 0}, "LCHARG = T\nNSW = 0\n"),
        ("", {"NSW": 0}, "NSW = 0\n"),
        ("MAGMOM = 1 1 \\\n  2 2\n", {"NSW": 0}, "MAGMOM = 1 1 \\\n  2 2\nNSW = 0\n"),
        ("ISPIN = 2\n", {"ISPIN": True}, "ISPIN = .TRUE.\n"),
        ("SYSTEM = Ti{001} slab\nENCUT = 500\n", {"SYSTEM": "new"}, "SYSTEM = new\nENCUT = 500\n"),
    ],
)
def test_incar_override_of_a_plain_template_is_unchanged(tmp_path, template, overrides, expected):
    """The counterexample: lines holding one statement come out exactly as they always did."""

    incar = tmp_path / "INCAR"
    incar.write_bytes(template.encode())

    _apply_incar_overrides(incar, overrides)

    assert incar.read_text(encoding="utf-8") == expected


def test_stage_applies_incar_overrides_and_leaves_the_template_alone(tmp_path, workflow_manifest):
    template = "ENCUT = 520; ISPIN = 2\n"
    manifest = workflow_manifest(
        templates={"vasp-template": {"INCAR": template}},
        steps=[
            {
                "id": "density",
                "kind": "vasp",
                "executable": "vasp",
                "template_dir": "vasp-template",
                "options": {
                    "required_inputs": ["POSCAR", "INCAR"],
                    "expected_outputs": ["CHGCAR"],
                    "incar_overrides": {"ENCUT": 400},
                },
            }
        ],
    )

    staged = stage_workflow(manifest)

    assert (staged.jobs[0].cwd / "INCAR").read_text(encoding="utf-8") == "ENCUT = 400; ISPIN = 2\n"
    assert (tmp_path / "vasp-template/INCAR").read_text(encoding="utf-8") == template


@pytest.mark.parametrize(
    "leftover",
    [
        "frames/000000-00/density/CHGCAR",
        "frames/000000-00/density/vasp.stdout.log",
        "run-results.json",
        "analysis/electrace.toml",
        "slurm/submissions.json",
    ],
)
def test_stage_force_keeps_anything_a_fresh_stage_would_not_write(
    tmp_path, workflow_manifest, leftover
):
    manifest = workflow_manifest()
    stage_workflow(manifest)
    path = tmp_path / "workspace" / leftover
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("days of work", encoding="utf-8")

    with pytest.raises(ValueError, match="--discard-results") as caught:
        stage_workflow(manifest, overwrite=True)

    assert path.read_text(encoding="utf-8") == "days of work"
    assert leftover in str(caught.value)
    assert list(tmp_path.glob(".workspace.stage-*")) == []

    stage_workflow(manifest, overwrite=True, discard_results=True)
    assert not path.exists()


def test_stage_force_says_when_jobs_may_still_be_running(tmp_path, workflow_manifest):
    manifest = workflow_manifest()
    stage_workflow(manifest)
    record = tmp_path / "workspace/slurm/submissions.json"
    record.parent.mkdir()
    record.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="queued or running"):
        stage_workflow(manifest, overwrite=True)


def test_stage_force_still_replaces_a_workspace_without_results(tmp_path, workflow_manifest):
    """The counterexample: nothing ran, so there is nothing to lose."""

    manifest = workflow_manifest()
    staged = stage_workflow(manifest)
    scripts = tmp_path / "workspace/slurm"
    scripts.mkdir()
    (scripts / "frame-000000.slurm").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "scripts.json").write_text("{}", encoding="utf-8")

    replaced = stage_workflow(manifest, overwrite=True)

    assert replaced.to_dict() == staged.to_dict()
    assert not scripts.exists()


def test_stage_force_on_the_command_line_after_a_local_run(tmp_path, workflow_manifest):
    vasp = tmp_path / "vasp.sh"
    vasp.write_text("#!/bin/sh\necho density > CHGCAR\n", encoding="utf-8")
    vasp.chmod(0o700)
    manifest = workflow_manifest(executables={"vasp": str(vasp)})
    stage_workflow(manifest)
    assert all(result.succeeded for result in run_staged_workflow(manifest, confirmed=True))
    density = list((tmp_path / "workspace").rglob("CHGCAR"))
    assert len(density) == 2
    runner = CliRunner()

    refused = runner.invoke(app, ["workflow", "stage", str(manifest), "--force"])
    assert refused.exit_code == 2
    assert all(path.is_file() for path in density)

    replaced = runner.invoke(
        app, ["workflow", "stage", str(manifest), "--force", "--discard-results"]
    )
    assert replaced.exit_code == 0, replaced.output
    assert not any(path.exists() for path in density)

    help_text = " ".join(runner.invoke(app, ["workflow", "stage", "--help"]).output.split())
    assert "--discard-results" in help_text
