"""End-to-end integration tests for the smart init flow."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from dockcheck.cli import cli


class TestSmartInitE2E:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_init_detects_cf_worker(self, runner):
        """Init in a CF Worker project detects wrangler.toml and generates workflow."""
        with runner.isolated_filesystem():
            # Set up a CF Worker project
            Path("wrangler.toml").write_text('name = "hello-world"')
            pkg = {
                "devDependencies": {"wrangler": "^3.0.0"},
                "scripts": {"deploy": "wrangler deploy"},
            }
            Path("package.json").write_text(json.dumps(pkg))

            # Mock git remote
            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )

            with patch("subprocess.run", return_value=mock_git):
                result = runner.invoke(
                    cli,
                    ["init", "--provider", "cloudflare",
                     "--non-interactive"],
                )

            assert result.exit_code == 0
            assert "Cloudflare Workers" in result.output
            assert Path(".dockcheck/policy.yaml").exists()
            assert Path(".github/workflows/dockcheck.yml").exists()

    def test_init_generates_valid_workflow_yaml(self, runner):
        """Generated workflow should be valid YAML with CF deploy step."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )

            with patch("subprocess.run", return_value=mock_git):
                runner.invoke(
                    cli,
                    ["init", "--provider", "cloudflare", "--non-interactive"],
                )

            wf_path = Path(".github/workflows/dockcheck.yml")
            assert wf_path.exists()
            parsed = yaml.safe_load(wf_path.read_text())
            assert parsed["name"] == "dockcheck CI/CD"

            # Should have CF deploy step
            steps = parsed["jobs"]["dockcheck"]["steps"]
            step_names = [s.get("name", "") for s in steps]
            assert any("Cloudflare" in n for n in step_names)

    def test_init_template_fallback(self, runner):
        """--template flag should still work (legacy path)."""
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init", "--template", "hackathon"])
            assert result.exit_code == 0
            assert Path(".dockcheck/policy.yaml").exists()
            assert "hackathon" in result.output

    def test_init_no_provider_detected_falls_back(self, runner):
        """Empty repo with no config falls back to template init."""
        with runner.isolated_filesystem():
            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )
            with patch("subprocess.run", return_value=mock_git):
                result = runner.invoke(cli, ["init", "--non-interactive"])

            assert result.exit_code == 0
            assert "No deploy target detected" in result.output
            assert Path(".dockcheck/policy.yaml").exists()

    def test_init_gitignore_created(self, runner):
        """Init should ensure .gitignore covers .env files."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )

            with patch("subprocess.run", return_value=mock_git):
                runner.invoke(
                    cli,
                    ["init", "--provider", "cloudflare", "--non-interactive"],
                )

            gitignore = Path(".gitignore")
            assert gitignore.exists()
            content = gitignore.read_text()
            assert ".env" in content

    def test_init_already_exists_blocks(self, runner):
        """Re-running init should not overwrite existing config."""
        with runner.isolated_filesystem():
            Path(".dockcheck").mkdir()
            result = runner.invoke(
                cli,
                ["init", "--provider", "cloudflare", "--non-interactive"],
            )
            assert "already exists" in result.output

    def test_init_check_after_init(self, runner):
        """After init, `dockcheck check` should work."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )

            with patch("subprocess.run", return_value=mock_git):
                runner.invoke(
                    cli,
                    ["init", "--provider", "cloudflare", "--non-interactive"],
                )

            result = runner.invoke(cli, ["check"])
            assert result.exit_code == 0

    def test_init_dry_run_after_init(self, runner):
        """After init, `dockcheck run --dry-run` should show pipeline."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )

            with patch("subprocess.run", return_value=mock_git):
                runner.invoke(
                    cli,
                    ["init", "--provider", "cloudflare", "--non-interactive"],
                )

            result = runner.invoke(cli, ["run", "--dry-run"])
            assert result.exit_code == 0
            assert "CHECK" in result.output
            assert "DEPLOY" in result.output
            assert "cloudflare" in result.output

    def test_init_with_env_secrets_present(self, runner, monkeypatch):
        """Init when env vars are already set should report all ready."""
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")

        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )

            with patch("subprocess.run", return_value=mock_git):
                result = runner.invoke(
                    cli,
                    ["init", "--provider", "cloudflare",
                     "--non-interactive"],
                )

            assert result.exit_code == 0
            assert "All secrets available" in result.output

    def test_init_vercel_provider(self, runner):
        """Init with --provider vercel generates vercel workflow."""
        with runner.isolated_filesystem():
            Path("vercel.json").write_text("{}")
            Path("package.json").write_text('{"name": "test"}')

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr=""
            )

            with patch("subprocess.run", return_value=mock_git):
                result = runner.invoke(
                    cli,
                    ["init", "--provider", "vercel", "--non-interactive"],
                )

            assert result.exit_code == 0
            assert "Vercel" in result.output

    def test_init_scan_output(self, runner):
        """Init should show scan results."""
        with runner.isolated_filesystem():
            Path("wrangler.toml").write_text('name = "test"')
            pkg = {"dependencies": {"hono": "^4.0.0"}}
            Path("package.json").write_text(json.dumps(pkg))

            mock_git = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="https://github.com/user/repo.git\n",
                stderr="",
            )

            with patch("subprocess.run", return_value=mock_git):
                result = runner.invoke(
                    cli,
                    ["init", "--provider", "cloudflare", "--non-interactive"],
                )

            assert "Scanning repository" in result.output
            assert "javascript" in result.output.lower() or "Language" in result.output
