"""Tests for deploy CLI command and run pipeline."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from dockcheck.cli import _load_env_file, _run_deploy, cli


class TestDeployCommand:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_deploy_no_provider_detected(self, runner):
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["deploy"])
        assert result.exit_code != 0
        assert "no deploy provider" in result.output.lower() or result.exit_code == 1

    def test_deploy_with_explicit_provider_not_available(self, runner):
        with runner.isolated_filesystem():
            with patch("shutil.which", return_value=None):
                result = runner.invoke(cli, ["deploy", "--provider", "cloudflare"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or result.exit_code == 1

    def test_deploy_detects_provider_from_wrangler(self, runner):
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.workers.dev"

            with (
                patch("subprocess.run", return_value=mock_git),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy"])

            assert result.exit_code == 0
            assert "Deployed successfully" in result.output

    def test_deploy_shows_live_url(self, runner):
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://hello.workers.dev"

            with (
                patch("subprocess.run", return_value=mock_git),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy"])

            assert "https://hello.workers.dev" in result.output

    def test_deploy_failure_shows_error(self, runner):
        with runner.isolated_filesystem():
            mock_deploy = MagicMock()
            mock_deploy.success = False
            mock_deploy.error = "Authentication failed"
            mock_deploy.stderr = ""

            with (
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(
                    cli, ["deploy", "--provider", "cloudflare"]
                )

            assert result.exit_code != 0


class TestRunPipeline:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_dry_run_detects_lint_command(self, runner):
        with runner.isolated_filesystem():
            pkg = {"scripts": {"lint": "eslint .", "test": "jest"}}
            Path("package.json").write_text(json.dumps(pkg))
            Path("wrangler.toml").write_text('name = "test"')

            result = runner.invoke(cli, ["run", "--dry-run"])
            assert result.exit_code == 0
            assert "LINT" in result.output
            assert "npm run lint" in result.output

    def test_dry_run_detects_test_command(self, runner):
        with runner.isolated_filesystem():
            pkg = {"scripts": {"test": "jest"}}
            Path("package.json").write_text(json.dumps(pkg))

            result = runner.invoke(cli, ["run", "--dry-run"])
            assert result.exit_code == 0
            assert "TEST" in result.output
            assert "npm test" in result.output

    def test_dry_run_shows_deploy_provider(self, runner):
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            result = runner.invoke(cli, ["run", "--dry-run"])
            assert result.exit_code == 0
            assert "DEPLOY" in result.output
            assert "cloudflare" in result.output

    def test_dry_run_skip_flags(self, runner):
        with runner.isolated_filesystem():
            pkg = {"scripts": {"lint": "eslint .", "test": "jest"}}
            Path("package.json").write_text(json.dumps(pkg))
            Path("wrangler.toml").write_text('name = "test"')

            result = runner.invoke(
                cli,
                ["run", "--dry-run", "--skip-lint", "--skip-test", "--skip-deploy"],
            )
            assert result.exit_code == 0
            assert "LINT" not in result.output
            assert "TEST" not in result.output
            assert "DEPLOY" not in result.output
            assert "CHECK" in result.output  # always present

    def test_dry_run_python_project(self, runner):
        with runner.isolated_filesystem():
            Path("pyproject.toml").write_text(
                '[project]\nname = "app"\n\n[tool.ruff]\nline-length = 100'
            )

            result = runner.invoke(cli, ["run", "--dry-run"])
            assert result.exit_code == 0
            assert "LINT" in result.output
            assert "ruff check" in result.output
            assert "TEST" in result.output
            assert "pytest" in result.output

    def test_dry_run_empty_project(self, runner):
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["run", "--dry-run"])
            assert result.exit_code == 0
            # Only CHECK step, no lint/test/deploy
            assert "CHECK" in result.output
            lines = [
                line for line in result.output.splitlines()
                if line.strip().startswith("1.")
            ]
            assert len(lines) == 1  # only one step


class TestRunDeployHelpers:
    def test_load_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=secret123\nDB_URL=postgres://localhost\n")
        env = _load_env_file(str(tmp_path))
        assert env == {"API_KEY": "secret123", "DB_URL": "postgres://localhost"}

    def test_load_env_file_skips_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nKEY=val\n\n")
        env = _load_env_file(str(tmp_path))
        assert env == {"KEY": "val"}

    def test_load_env_file_missing(self, tmp_path):
        env = _load_env_file(str(tmp_path))
        assert env == {}

    def test_run_deploy_unknown_provider(self):
        result = _run_deploy("nonexistent", "/tmp")
        assert result is False
