"""Tests for skill loader — discovery, parsing, and validation of SKILL.md files."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from dockcheck.skills.loader import Skill, SkillLoader, SkillMetadata

# Path to the bundled skills shipped with the project
BUILT_IN_SKILLS_DIR = Path(__file__).parent.parent.parent / ".dockcheck" / "skills"

# Expected built-in skill names (directory names)
EXPECTED_SKILL_DIRS = {"analyze", "test", "test-writer", "deploy", "verify", "notify"}


class TestSkillDiscovery:
    """Tests for SkillLoader.discover() — lightweight metadata without full parse."""

    def test_discover_returns_list(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        result = loader.discover()
        assert isinstance(result, list)

    def test_discover_finds_all_built_in_skills(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        # Each SKILL.md H1 should map to one of the expected display names;
        # use directory-level check as the canonical identifier
        loader.discover()  # verify no exceptions
        dirs_found = {m.path.parent.name for m in loader.discover()}
        assert EXPECTED_SKILL_DIRS == dirs_found

    def test_discover_returns_skill_metadata_objects(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        for item in loader.discover():
            assert isinstance(item, SkillMetadata)

    def test_discover_metadata_has_name_and_purpose(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        for item in loader.discover():
            assert item.name  # non-empty
            assert item.purpose  # non-empty

    def test_discover_metadata_path_points_to_skill_md(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        for item in loader.discover():
            assert item.path.name == "SKILL.md"
            assert item.path.exists()

    def test_discover_empty_dir_returns_empty_list(self, tmp_path):
        loader = SkillLoader(str(tmp_path))
        assert loader.discover() == []

    def test_discover_nonexistent_dir_returns_empty_list(self, tmp_path):
        loader = SkillLoader(str(tmp_path / "does_not_exist"))
        assert loader.discover() == []

    def test_discover_sorted_by_name(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        names = [m.path.parent.name for m in loader.discover()]
        assert names == sorted(names)


class TestSkillLoading:
    """Tests for SkillLoader.load() — full skill with instructions."""

    def test_load_analyze_skill(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load("analyze")
        assert isinstance(skill, Skill)
        assert "Analyze" in skill.name
        assert skill.purpose
        assert skill.instructions

    def test_load_test_skill(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load("test")
        assert "Test" in skill.name

    def test_load_test_writer_skill(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load("test-writer")
        assert skill.name

    def test_load_deploy_skill(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load("deploy")
        assert skill.purpose

    def test_load_verify_skill(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load("verify")
        assert skill.instructions

    def test_load_notify_skill(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load("notify")
        assert skill.inputs

    def test_load_missing_skill_raises_file_not_found(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            loader.load("nonexistent")

    def test_load_all_returns_all_skills(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skills = loader.load_all()
        assert len(skills) == len(EXPECTED_SKILL_DIRS)
        for skill in skills:
            assert isinstance(skill, Skill)

    def test_load_all_empty_dir(self, tmp_path):
        loader = SkillLoader(str(tmp_path))
        assert loader.load_all() == []

    def test_load_all_skills_have_instructions(self):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        for skill in loader.load_all():
            assert len(skill.instructions) > 50, (
                f"Skill '{skill.name}' has very short instructions"
            )


class TestSkillParsing:
    """Tests for SkillLoader.parse_skill_md() — SKILL.md content parsing."""

    def _write_skill_md(self, tmp_path: Path, content: str) -> Path:
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(textwrap.dedent(content))
        return skill_md

    def test_parse_extracts_name_from_h1(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # My Awesome Skill

            ## Purpose
            Does something great.

            ## Instructions
            1. Do step one
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert skill.name == "My Awesome Skill"

    def test_parse_extracts_purpose(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Purpose
            Detect blast radius in the diff.

            ## Instructions
            1. Read the diff
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert "blast radius" in skill.purpose

    def test_parse_extracts_inputs_as_list(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Purpose
            Something.

            ## Inputs
            - Git diff (unified format)
            - List of changed files
            - Repository context

            ## Instructions
            1. Do it
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert len(skill.inputs) == 3
        assert "Git diff (unified format)" in skill.inputs

    def test_parse_instructions_contains_full_content(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Purpose
            Something.

            ## Instructions
            1. First step
            2. Second step
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert "First step" in skill.instructions
        assert "Second step" in skill.instructions

    def test_parse_path_is_set(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Purpose
            Something.

            ## Instructions
            1. Do it
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert skill.path == skill_md

    def test_parse_missing_purpose_section_returns_empty_string(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Instructions
            1. Do it
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert skill.purpose == ""

    def test_parse_missing_inputs_section_returns_empty_list(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Purpose
            Something.

            ## Instructions
            1. Do it
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert skill.inputs == []

    def test_parse_no_h1_falls_back_to_directory_name(self, tmp_path):
        skill_dir = tmp_path / "fallback-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("## Purpose\nSomething.\n\n## Instructions\n1. Do it\n")
        skill = SkillLoader.parse_skill_md(skill_md)
        assert skill.name == "fallback-skill"

    def test_parse_strips_whitespace_from_purpose(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Purpose

            This purpose has leading blank lines.

            ## Instructions
            1. Do it
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        assert not skill.purpose.startswith("\n")
        assert not skill.purpose.endswith("\n")

    def test_parse_inputs_ignores_non_list_lines(self, tmp_path):
        skill_md = self._write_skill_md(
            tmp_path,
            """\
            # Skill

            ## Purpose
            Something.

            ## Inputs
            Here is what you need:
            - Item one
            - Item two

            ## Instructions
            1. Do it
            """,
        )
        skill = SkillLoader.parse_skill_md(skill_md)
        # Only actual list items should be in inputs
        assert "Item one" in skill.inputs
        assert "Item two" in skill.inputs
        assert "Here is what you need:" not in skill.inputs


class TestBuiltInSkillContent:
    """Validate that each built-in SKILL.md has the required sections."""

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_DIRS))
    def test_skill_has_nonempty_purpose(self, skill_name):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load(skill_name)
        assert skill.purpose, f"Skill '{skill_name}' has empty purpose"

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_DIRS))
    def test_skill_has_nonempty_instructions(self, skill_name):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load(skill_name)
        assert skill.instructions, f"Skill '{skill_name}' has empty instructions"

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_DIRS))
    def test_skill_instructions_contain_numbered_steps(self, skill_name):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load(skill_name)
        assert "1." in skill.instructions, (
            f"Skill '{skill_name}' has no numbered steps in instructions"
        )

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_DIRS))
    def test_skill_has_expected_output_section(self, skill_name):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load(skill_name)
        assert "Expected Output" in skill.instructions, (
            f"Skill '{skill_name}' is missing '## Expected Output' section"
        )

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_DIRS))
    def test_skill_expected_output_contains_confidence_field(self, skill_name):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load(skill_name)
        assert '"confidence"' in skill.instructions, (
            f"Skill '{skill_name}' Expected Output JSON is missing 'confidence' field"
        )

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_DIRS))
    def test_skill_expected_output_contains_completed_field(self, skill_name):
        loader = SkillLoader(str(BUILT_IN_SKILLS_DIR))
        skill = loader.load(skill_name)
        assert '"completed"' in skill.instructions, (
            f"Skill '{skill_name}' Expected Output JSON is missing 'completed' field"
        )
