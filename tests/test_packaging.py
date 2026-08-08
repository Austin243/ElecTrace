from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from electrace.cli import _MAXIMUM_TESTED_PYTHON, _MINIMUM_PYTHON

ROOT = Path(__file__).resolve().parents[1]


def _requirement_name(requirement: str) -> str:
    """Return the normalized project name from a simple PEP 508 requirement."""

    token = requirement.split(";", maxsplit=1)[0].strip()
    token = token.split("[", maxsplit=1)[0]
    for separator in ("<", ">", "=", "!", "~", " "):
        token = token.split(separator, maxsplit=1)[0]
    return token.lower().replace("_", "-")


def _names(requirements: list[str]) -> set[str]:
    return {_requirement_name(requirement) for requirement in requirements}


def test_runtime_extras_match_import_boundaries() -> None:
    document: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = document["project"]
    required = _names(project["dependencies"])
    optional = project["optional-dependencies"]

    assert required == {"numpy", "pyyaml", "rich", "typer"}
    assert _names(optional["accelerate"]) == {"scipy"}
    assert _names(optional["hdf5"]) == {"h5py"}
    assert _names(optional["all"]) == {"h5py", "scipy"}
    optional_names = {name for requirements in optional.values() for name in _names(requirements)}
    assert optional_names.isdisjoint({"ase", "mkdocstrings", "types-setuptools"})
    assert "typer>=0.20" in project["dependencies"]


def test_python_metadata_matches_the_doctor_support_window() -> None:
    document: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lower = ".".join(str(component) for component in _MINIMUM_PYTHON)
    upper = f"{_MAXIMUM_TESTED_PYTHON[0]}.{_MAXIMUM_TESTED_PYTHON[1] + 1}"

    assert document["project"]["requires-python"] == f">={lower},<{upper}"


def test_sdist_excludes_repository_only_configuration() -> None:
    document: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text())
    excluded = set(document["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"])

    assert {"/.github", "/.pre-commit-config.yaml", "/AGENTS.md"} <= excluded
