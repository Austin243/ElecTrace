import shutil

import pytest
import yaml

from electrace.workflow import validate_workflow
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
            id="closing-brace-is-not-part-of-a-value",
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
