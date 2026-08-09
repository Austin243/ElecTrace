import pytest

from electrace.workflow.collection import analyze_collected_workflow, collect_workflow_outputs
from electrace.workflow.staging import descriptor_outputs, stage_workflow

ACF = """# X Y Z CHARGE MIN DIST ATOMIC VOL
1 0.0 0.0 0.0 1.0 0.0 10.0
"""


def _staged(factory, base):
    manifest = factory(
        base=base,
        executables={"bader": "/opt/bader"},
        steps=[{"id": "charges", "kind": "bader", "executable": "bader"}],
    )
    staged = stage_workflow(manifest)
    for outputs in descriptor_outputs(staged).values():
        outputs[0].write_text(ACF, encoding="utf-8")
    return manifest, base / "images/00/POSCAR"


def test_collect_and_analyze_preserve_sources(tmp_path, poscar_text, workflow_manifest):
    manifest, source = _staged(workflow_manifest, tmp_path)

    collected = collect_workflow_outputs(manifest)
    artifacts = analyze_collected_workflow(manifest)

    assert source.read_text(encoding="utf-8") == poscar_text
    assert (collected.input_root / "000000-00/CONTCAR").is_file()
    assert (collected.descriptor_root / "000000-00/ACF.dat").is_file()
    assert collected.config_path.is_file()
    assert (artifacts.output_directory / "analysis.json").is_file()


def test_collect_refuses_missing_output_and_unmarked_replacement(tmp_path, workflow_manifest):
    manifest, _ = _staged(workflow_manifest, tmp_path / "missing")
    next((tmp_path / "missing/workspace").rglob("ACF.dat")).unlink()
    with pytest.raises((FileNotFoundError, ValueError), match=r"output|supported"):
        collect_workflow_outputs(manifest)

    manifest, _ = _staged(workflow_manifest, tmp_path / "unmarked")
    analysis = tmp_path / "unmarked/workspace/analysis"
    analysis.mkdir()
    sentinel = analysis / "keep"
    sentinel.write_text("user data", encoding="utf-8")
    with pytest.raises(ValueError, match="unmarked"):
        collect_workflow_outputs(manifest, overwrite=True)
    assert sentinel.read_text(encoding="utf-8") == "user data"
