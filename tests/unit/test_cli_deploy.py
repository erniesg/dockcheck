"""Tests for deploy CLI command and run pipeline."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from dockcheck.cli import _load_env_file, _run_deploy, cli

# Mock subprocess that returns empty/failure for all calls (git, gh, etc.)
_MOCK_SUBPROCESS_EMPTY = subprocess.CompletedProcess(
    args=[], returncode=128, stdout="", stderr=""
)


def _mock_subprocess_factory(**overrides):
    """Create a mock subprocess.run that returns empty results."""
    def mock_run(*args, **kwargs):
        return _MOCK_SUBPROCESS_EMPTY
    return mock_run


class TestDeployCommand:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_deploy_no_provider_detected(self, runner):
        with runner.isolated_filesystem():
            with patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY):
                result = runner.invoke(
                    cli, ["deploy", "--non-interactive"]
                )
        assert result.exit_code != 0

    def test_deploy_with_explicit_provider_not_available(self, runner):
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')
            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value=None),
            ):
                result = runner.invoke(
                    cli,
                    ["deploy", "--provider", "cloudflare", "--non-interactive"],
                )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_deploy_detects_provider_from_wrangler(self, runner):
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')
            Path(".gitignore").write_text(".env\n")

            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.workers.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=lambda k, d=None: {
                    "CLOUDFLARE_API_TOKEN": "tok",
                    "CLOUDFLARE_ACCOUNT_ID": "acc",
                }.get(k, d)),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(
                    cli, ["deploy", "--non-interactive"]
                )

            assert result.exit_code == 0
            assert "Deployed successfully" in result.output

    def test_deploy_shows_live_url(self, runner):
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')
            Path(".gitignore").write_text(".env\n")

            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://hello.workers.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=lambda k, d=None: {
                    "CLOUDFLARE_API_TOKEN": "tok",
                    "CLOUDFLARE_ACCOUNT_ID": "acc",
                }.get(k, d)),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(
                    cli, ["deploy", "--non-interactive"]
                )

            assert "https://hello.workers.dev" in result.output

    def test_deploy_failure_shows_error(self, runner):
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')
            Path(".gitignore").write_text(".env\n")

            mock_deploy = MagicMock()
            mock_deploy.success = False
            mock_deploy.error = "Authentication failed"
            mock_deploy.stderr = ""

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=lambda k, d=None: {
                    "CLOUDFLARE_API_TOKEN": "tok",
                    "CLOUDFLARE_ACCOUNT_ID": "acc",
                }.get(k, d)),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(
                    cli,
                    ["deploy", "--provider", "cloudflare", "--non-interactive"],
                )

            assert result.exit_code != 0

    def test_deploy_auto_inits(self, runner):
        """Deploy should auto-create .dockcheck/ if missing."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')
            Path(".gitignore").write_text(".env\n")

            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.workers.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=lambda k, d=None: {
                    "CLOUDFLARE_API_TOKEN": "tok",
                    "CLOUDFLARE_ACCOUNT_ID": "acc",
                }.get(k, d)),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(
                    cli, ["deploy", "--non-interactive"]
                )

            assert result.exit_code == 0
            assert Path(".dockcheck/policy.yaml").exists()
            assert Path(".github/workflows/dockcheck.yml").exists()
            assert "Initializing" in result.output

    def test_deploy_dry_run_preflight_only(self, runner):
        """--dry-run should show preflight without deploying."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=lambda k, d=None: {
                    "CLOUDFLARE_API_TOKEN": "tok",
                    "CLOUDFLARE_ACCOUNT_ID": "acc",
                }.get(k, d)),
            ):
                result = runner.invoke(
                    cli,
                    ["deploy", "--dry-run", "--non-interactive"],
                )

            assert result.exit_code == 0
            assert "Preflight" in result.output
            assert "Deployed" not in result.output

    def test_deploy_missing_auth_non_interactive_fails(self, runner):
        """Non-interactive deploy with missing secrets should fail."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
            ):
                result = runner.invoke(
                    cli,
                    ["deploy", "--non-interactive"],
                )

            assert result.exit_code != 0
            assert "Missing secrets" in result.output


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
