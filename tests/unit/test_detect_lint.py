"""Tests for lint/format command detection in RepoDetector."""

from __future__ import annotations

import json
from pathlib import Path

from dockcheck.init.detect import RepoDetector


class TestDetectLintCommand:
    def test_npm_lint_script(self, tmp_path: Path):
        pkg = {"scripts": {"lint": "eslint ."}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "npm run lint"

    def test_eslintrc_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        (tmp_path / ".eslintrc.json").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "npx eslint ."

    def test_eslint_flat_config(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        (tmp_path / "eslint.config.js").write_text("export default {};")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "npx eslint ."

    def test_biome_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        (tmp_path / "biome.json").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "npx biome check ."

    def test_npm_lint_takes_priority(self, tmp_path: Path):
        """package.json lint script takes priority over config files."""
        pkg = {"scripts": {"lint": "biome check ."}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / ".eslintrc.json").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "npm run lint"

    def test_ruff_from_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'app'\n\n[tool.ruff]\nline-length = 100"
        )
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "ruff check ."

    def test_ruff_toml_file(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'")
        (tmp_path / "ruff.toml").write_text("line-length = 100")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "ruff check ."

    def test_flake8(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'")
        (tmp_path / ".flake8").write_text("[flake8]\nmax-line-length = 100")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "flake8"

    def test_go_lint(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/app")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "golangci-lint run"

    def test_rust_lint(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command == "cargo clippy"

    def test_no_lint_command(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command is None

    def test_python_no_ruff_config(self, tmp_path: Path):
        """Python project without ruff config returns None."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.lint_command is None


class TestDetectFormatCommand:
    def test_npm_format_script(self, tmp_path: Path):
        pkg = {"scripts": {"format": "prettier --write ."}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command == "npm run format"

    def test_prettierrc(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        (tmp_path / ".prettierrc").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command == "npx prettier --check ."

    def test_prettier_config_js(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        (tmp_path / "prettier.config.js").write_text("module.exports = {};")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command == "npx prettier --check ."

    def test_biome_format(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        (tmp_path / "biome.json").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command == "npx biome format ."

    def test_ruff_format_from_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'app'\n\n[tool.ruff]\nline-length = 100"
        )
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command == "ruff format --check ."

    def test_ruff_format_from_ruff_toml(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'")
        (tmp_path / "ruff.toml").write_text("line-length = 100")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command == "ruff format --check ."

    def test_rust_format(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command == "cargo fmt --check"

    def test_no_format_command(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command is None

    def test_go_no_format_command(self, tmp_path: Path):
        """Go doesn't have a separate format command detected."""
        (tmp_path / "go.mod").write_text("module example.com/app")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.format_command is None


class TestRepoContextLintFields:
    def test_defaults(self):
        from dockcheck.init.detect import RepoContext

        ctx = RepoContext()
        assert ctx.lint_command is None
        assert ctx.format_command is None

    def test_serialization(self):
        from dockcheck.init.detect import RepoContext

        ctx = RepoContext(lint_command="ruff check .", format_command="ruff format --check .")
        data = ctx.model_dump()
        assert data["lint_command"] == "ruff check ."
        assert data["format_command"] == "ruff format --check ."
