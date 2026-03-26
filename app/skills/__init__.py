"""
Skill loader — automatically discovers and loads skills from the skills directory.

Each skill is a sub-folder containing a SKILL.md file with the skill's rules/content.
Usage:
    from app.skills import load_skill, list_skills

    rules = load_skill("pricing_rules")   # returns SKILL.md content as str
    names = list_skills()                  # ["pricing_rules", "fetch_competitor_info", ...]
"""

from functools import lru_cache
from pathlib import Path
from typing import List

SKILLS_DIR = Path(__file__).parent


@lru_cache(maxsize=32)
def load_skill(skill_name: str) -> str:
    """Load a skill's content from its SKILL.md file.

    Raises FileNotFoundError when the skill directory or SKILL.md is missing.
    """
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(
            f"Skill '{skill_name}' not found — expected {skill_path}"
        )
    return skill_path.read_text(encoding="utf-8")


def list_skills() -> List[str]:
    """Return the names of all discovered skills (directories that contain SKILL.md)."""
    return sorted(
        d.name
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )
