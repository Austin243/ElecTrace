from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "electrace"


def test_electrace_skill_is_complete_and_installable() -> None:
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    _, frontmatter, body = skill_text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata.keys() == {"name", "description"}
    assert metadata["name"] == "electrace"
    assert all(
        trigger in metadata["description"].lower()
        for trigger in ("bader", "qtaim", "lobster", "vasp neb")
    )
    assert "[TODO" not in skill_text
    assert "references/workflows.md" in body
    assert (SKILL / "references" / "workflows.md").is_file()

    agent_metadata = yaml.safe_load((SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    interface = agent_metadata["interface"]
    assert interface["display_name"] == "ElecTrace"
    assert 25 <= len(interface["short_description"]) <= 64
    assert "$electrace" in interface["default_prompt"]
