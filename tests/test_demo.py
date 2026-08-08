from __future__ import annotations

from pathlib import Path

import pytest

from electrace.config import load_config
from electrace.demo import generate_demo
from electrace.io import load_sequence


def test_demo_generates_a_complete_loadable_sequence(tmp_path: Path) -> None:
    config_path = generate_demo(tmp_path / "demo")
    config = load_config(config_path)
    sequence = load_sequence(config_path.parent / config.inputs.root)

    assert config.project_name == "Synthetic C–N electronic rearrangement"
    assert len(sequence.frames) == 6
    assert [len(frame.atoms) for frame in sequence.frames] == [2] * 6
    assert [len(frame.features) for frame in sequence.frames] == [2, 2, 3, 2, 3, 3]
    assert sequence.frames[3].energy == pytest.approx(0.71)
    assert sequence.frames[4].features[-1].kind == "non_nuclear_attractor"


def test_demo_refuses_to_replace_nonempty_directory(tmp_path: Path) -> None:
    destination = tmp_path / "demo"
    destination.mkdir()
    (destination / "valuable.txt").write_text("keep")

    with pytest.raises(FileExistsError):
        generate_demo(destination)
