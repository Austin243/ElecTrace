import pytest
import yaml

POSCAR = """H\n1\n1 0 0\n0 1 0\n0 0 1\nH\n1\nDirect\n0 0 0\n"""


@pytest.fixture
def poscar_text():
    return POSCAR


@pytest.fixture
def workflow_manifest(tmp_path):
    def write(
        *,
        base=None,
        scheduler="local",
        executables=None,
        steps=None,
        step_options=None,
        workspace="workspace",
        templates=None,
    ):
        base = base or tmp_path
        for label in ("00", "01"):
            frame = base / "images" / label
            frame.mkdir(parents=True)
            (frame / "POSCAR").write_text(POSCAR, encoding="utf-8")
        for directory, files in (templates or {}).items():
            target = base / directory
            target.mkdir(parents=True)
            for name, content in files.items():
                (target / name).write_text(content, encoding="utf-8")

        programs = executables or {"vasp": "/bin/true"}
        options = {"required_inputs": ["POSCAR"], "expected_outputs": ["CHGCAR"]}
        options.update(step_options or {})
        payload = {
            "schema_version": "electrace.workflow.v1",
            "project": {
                "root": "images",
                "series_kind": "neb",
                "workspace": str(workspace),
                "structure": "CONTCAR",
                "fallback": "POSCAR",
            },
            "frames": "all",
            "executables": {
                name: {"path": value} if isinstance(value, str) else dict(value)
                for name, value in programs.items()
            },
            "scheduler": {
                "kind": scheduler,
                "directives": (
                    {"partition": "cpu", "exclude": "node[1-8]"} if scheduler == "slurm" else {}
                ),
            },
            "steps": list(
                steps
                or [
                    {
                        "id": "density",
                        "kind": "vasp",
                        "executable": "vasp",
                        "options": options,
                    }
                ]
            ),
        }
        path = base / "workflow.yaml"
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return path

    return write
