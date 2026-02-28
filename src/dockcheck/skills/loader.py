"""Skill loader — discover and parse SKILL.md files from .dockcheck/skills/."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel


class SkillMetadata(BaseModel):
    """Lightweight representation: name + purpose only, no full instructions loaded."""

    name: str
    purpose: str
    path: Path

    model_config = {"arbitrary_types_allowed": True}


class Skill(BaseModel):
    """Full skill representation including parsed instructions."""

    name: str
    purpose: str
    instructions: str
    path: Path
    inputs: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


class SkillLoader:
    """Discovers and loads skills from a SKILL.md directory tree."""

    def __init__(self, skills_dir: str = ".dockcheck/skills") -> None:
        self.skills_dir = Path(skills_dir)

    def discover(self) -> list[SkillMetadata]:
        """Find all SKILL.md files and return lightweight metadata (name + purpose only)."""
        if not self.skills_dir.exists():
            return []

        metadata: list[SkillMetadata] = []
        for skill_md in sorted(self.skills_dir.rglob("SKILL.md")):
            skill = self.parse_skill_md(skill_md)
            metadata.append(
                SkillMetadata(
                    name=skill.name,
                    purpose=skill.purpose,
                    path=skill_md,
                )
            )
        return metadata

    def load(self, skill_name: str) -> Skill:
        """Load full skill instructions for a specific skill by name."""
        skill_dir = self.skills_dir / skill_name
        skill_md = skill_dir / "SKILL.md"

        if not skill_md.exists():
            raise FileNotFoundError(
                f"Skill '{skill_name}' not found at {skill_md}"
            )

        return self.parse_skill_md(skill_md)

    def load_all(self) -> list[Skill]:
        """Load all skills from the skills directory."""
        if not self.skills_dir.exists():
            return []

        skills: list[Skill] = []
        for skill_md in sorted(self.skills_dir.rglob("SKILL.md")):
            skills.append(self.parse_skill_md(skill_md))
        return skills

    @staticmethod
    def parse_skill_md(path: Path) -> Skill:
        """Parse a SKILL.md file into a Skill model.

        Extracts:
        - name: from the top-level H1 heading
        - purpose: from the ## Purpose section body
        - inputs: from the ## Inputs section (list items)
        - instructions: full raw markdown content
        """
        content = path.read_text(encoding="utf-8")

        # Derive skill name from the directory containing the SKILL.md
        skill_name = path.parent.name

        # Extract H1 heading (may differ from directory name for display purposes)
        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        display_name = h1_match.group(1).strip() if h1_match else skill_name

        # Extract ## Purpose section
        purpose = _extract_section(content, "Purpose")

        # Extract ## Inputs section as a list of strings (strip leading "- ")
        inputs_text = _extract_section(content, "Inputs")
        inputs: list[str] = []
        for line in inputs_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                inputs.append(stripped[2:].strip())
            elif stripped.startswith("* "):
                inputs.append(stripped[2:].strip())

        return Skill(
            name=display_name,
            purpose=purpose,
            instructions=content,
            path=path,
            inputs=inputs,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_section(content: str, section_title: str) -> str:
    """Extract the body text of a markdown ## Section, stopping at the next ##.

    Returns the extracted text stripped of leading/trailing whitespace.
    Returns an empty string if the section is not found.
    """
    # Match "## <Title>" (case-insensitive, any number of leading spaces)
    pattern = re.compile(
        r"^##\s+" + re.escape(section_title) + r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    match = pattern.search(content)
    if not match:
        return ""

    section_start = match.end()

    # Find next ## heading after the section start
    next_heading = re.search(r"^##\s+", content[section_start:], re.MULTILINE)
    if next_heading:
        section_body = content[section_start : section_start + next_heading.start()]
    else:
        section_body = content[section_start:]

    return section_body.strip()
