"""Tests for deploy, ship, and run CLI commands."""

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

# Standard mocks for a ready-to-deploy CF project
_CF_MOCKS = {
    "subprocess.run": _MOCK_SUBPROCESS_EMPTY,
    "shutil.which": "/usr/local/bin/wrangler",
    "CLOUDFLARE_API_TOKEN": "tok",
    "CLOUDFLARE_ACCOUNT_ID": "acc",
}


def _cf_env(k, d=None):
    """Mock os.environ.get with CF secrets."""
    return {
        "CLOUDFLARE_API_TOKEN": "tok",
        "CLOUDFLARE_ACCOUNT_ID": "acc",
    }.get(k, d)


def _setup_cf_project(gitignore: bool = True) -> None:
    """Write minimal wrangler.toml + package.json for CF Worker."""
    Path("wrangler.toml").write_text('name = "test"')
    Path("package.json").write_text('{"name": "test"}')
    if gitignore:
        Path(".gitignore").write_text(".env\n")


# ---------------------------------------------------------------------------
# deploy — thin wrapper, just calls provider
# ---------------------------------------------------------------------------
class TestDeployCommand:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_deploy_no_provider_detected(self, runner):
        """Empty dir → helpful error about no provider."""
        with runner.isolated_filesystem():
            with patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY):
                result = runner.invoke(cli, ["deploy"])
        assert result.exit_code != 0
        assert "no deploy provider" in result.output.lower()

    def test_deploy_explicit_provider_cli_missing(self, runner):
        """Provider specified but CLI not on PATH → helpful error."""
        with runner.isolated_filesystem():
            _setup_cf_project()
            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value=None),
            ):
                result = runner.invoke(
                    cli, ["deploy", "--provider", "cloudflare"]
                )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_deploy_detects_provider_from_wrangler(self, runner):
        """Auto-detects CF from wrangler.toml and deploys."""
        with runner.isolated_filesystem():
            _setup_cf_project()
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.workers.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
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
        """Successful deploy prints live URL."""
        with runner.isolated_filesystem():
            _setup_cf_project()
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://hello.workers.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy"])

            assert "https://hello.workers.dev" in result.output

    def test_deploy_failure_shows_error(self, runner):
        """Failed deploy shows error message."""
        with runner.isolated_filesystem():
            _setup_cf_project()
            mock_deploy = MagicMock()
            mock_deploy.success = False
            mock_deploy.error = "Authentication failed"
            mock_deploy.stderr = ""

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
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

    def test_deploy_suggests_ship_when_no_provider(self, runner):
        """Error message points user to `dockcheck ship`."""
        with runner.isolated_filesystem():
            with patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY):
                result = runner.invoke(cli, ["deploy"])
        assert "ship" in result.output.lower()

    def test_deploy_fly_provider(self, runner):
        """Can deploy with --provider fly."""
        with runner.isolated_filesystem():
            Path("fly.toml").write_text('app = "test"')
            Path("package.json").write_text('{"name": "test"}')
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.fly.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/fly"),
                patch(
                    "dockcheck.tools.deploy.FlyProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy", "--provider", "fly"])
            assert result.exit_code == 0
            assert "Deployed successfully" in result.output

    def test_deploy_netlify_provider(self, runner):
        """Can deploy with --provider netlify."""
        with runner.isolated_filesystem():
            Path("netlify.toml").write_text('[build]\ncommand = "npm build"')
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.netlify.app"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/netlify"),
                patch(
                    "dockcheck.tools.deploy.NetlifyProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy", "--provider", "netlify"])
            assert result.exit_code == 0

    def test_deploy_aws_lambda_provider(self, runner):
        """Can deploy with --provider aws-lambda."""
        with runner.isolated_filesystem():
            Path("template.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'")
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://abc.execute-api.us-east-1.amazonaws.com"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/sam"),
                patch(
                    "dockcheck.tools.deploy.AwsLambdaProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy", "--provider", "aws-lambda"])
            assert result.exit_code == 0

    def test_deploy_gcp_cloudrun_provider(self, runner):
        """Can deploy with --provider gcp-cloudrun."""
        with runner.isolated_filesystem():
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://svc.run.app"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/gcloud"),
                patch(
                    "dockcheck.tools.deploy.GcpCloudRunProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy", "--provider", "gcp-cloudrun"])
            assert result.exit_code == 0

    def test_deploy_railway_provider(self, runner):
        """Can deploy with --provider railway."""
        with runner.isolated_filesystem():
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.up.railway.app"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/railway"),
                patch(
                    "dockcheck.tools.deploy.RailwayProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy", "--provider", "railway"])
            assert result.exit_code == 0

    def test_deploy_render_provider(self, runner):
        """Can deploy with --provider render."""
        with runner.isolated_filesystem():
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = None

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("os.environ.get", side_effect=lambda k, d=None: {
                    "RENDER_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-xxx",
                }.get(k, d)),
                patch(
                    "dockcheck.tools.deploy.RenderProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy", "--provider", "render"])
            assert result.exit_code == 0

    def test_deploy_docker_registry_provider(self, runner):
        """Can deploy with --provider docker-registry."""
        with runner.isolated_filesystem():
            Path("Dockerfile").write_text("FROM python:3.10")
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = None

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/docker"),
                patch(
                    "dockcheck.tools.deploy.DockerRegistryProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(cli, ["deploy", "--provider", "docker-registry"])
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# ship — the magic "do everything" command
# ---------------------------------------------------------------------------
class TestShipCommand:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_ship_auto_inits(self, runner):
        """Ship auto-creates .dockcheck/ if missing."""
        with runner.isolated_filesystem():
            _setup_cf_project()
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.workers.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=_cf_env),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(
                    cli, ["ship", "--non-interactive", "--skip-lint", "--skip-test"]
                )

            assert result.exit_code == 0
            assert Path(".dockcheck/policy.yaml").exists()
            assert Path(".github/workflows/dockcheck.yml").exists()
            assert "Initializing" in result.output

    def test_ship_dry_run_preflight_only(self, runner):
        """--dry-run shows preflight without deploying."""
        with runner.isolated_filesystem():
            _setup_cf_project(gitignore=False)

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=_cf_env),
            ):
                result = runner.invoke(
                    cli, ["ship", "--dry-run", "--non-interactive"]
                )

            assert result.exit_code == 0
            assert "Preflight" in result.output
            assert "Deployed" not in result.output

    def test_ship_missing_auth_non_interactive_fails(self, runner):
        """Non-interactive ship with missing secrets → helpful error."""
        with runner.isolated_filesystem():
            _setup_cf_project(gitignore=False)

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
            ):
                result = runner.invoke(
                    cli, ["ship", "--non-interactive"]
                )

            assert result.exit_code != 0
            assert "Missing secrets" in result.output or "CLOUDFLARE_API_TOKEN" in result.output

    def test_ship_no_provider_detected(self, runner):
        """Empty project → helpful error about deploy target."""
        with runner.isolated_filesystem():
            with patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY):
                result = runner.invoke(
                    cli, ["ship", "--non-interactive"]
                )
        assert result.exit_code != 0
        assert "no deploy target" in result.output.lower()

    def test_ship_missing_cli(self, runner):
        """Wrangler not installed → helpful install hint."""
        with runner.isolated_filesystem():
            _setup_cf_project(gitignore=False)
            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value=None),
            ):
                result = runner.invoke(
                    cli, ["ship", "--non-interactive"]
                )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
        assert "npm install -g wrangler" in result.output

    def test_ship_missing_cli_fly_hint(self, runner):
        """Fly not installed → shows curl install hint, not npm."""
        with runner.isolated_filesystem():
            Path("fly.toml").write_text('app = "test"')
            Path("package.json").write_text('{"name": "test"}')
            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value=None),
            ):
                result = runner.invoke(
                    cli, ["ship", "--non-interactive"]
                )
        assert result.exit_code != 0
        assert "curl -L https://fly.io/install.sh" in result.output

    def test_ship_missing_cli_sam_hint(self, runner):
        """SAM not installed → shows pip install hint."""
        with runner.isolated_filesystem():
            Path("template.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'")
            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value=None),
            ):
                result = runner.invoke(
                    cli, ["ship", "--non-interactive"]
                )
        assert result.exit_code != 0
        assert "pip install aws-sam-cli" in result.output

    def test_ship_full_success(self, runner):
        """Happy path: preflight → init → pipeline → deploy."""
        with runner.isolated_filesystem():
            _setup_cf_project()
            mock_deploy = MagicMock()
            mock_deploy.success = True
            mock_deploy.url = "https://test.workers.dev"

            with (
                patch("subprocess.run", return_value=_MOCK_SUBPROCESS_EMPTY),
                patch("shutil.which", return_value="/usr/local/bin/wrangler"),
                patch("os.environ.get", side_effect=_cf_env),
                patch(
                    "dockcheck.tools.deploy.CloudflareProvider.deploy",
                    return_value=mock_deploy,
                ),
            ):
                result = runner.invoke(
                    cli,
                    ["ship", "--non-interactive", "--skip-lint", "--skip-test"],
                )

            assert result.exit_code == 0
            assert "Deployed successfully" in result.output
            assert "https://test.workers.dev" in result.output


# ---------------------------------------------------------------------------
# run — pipeline execution
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
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
