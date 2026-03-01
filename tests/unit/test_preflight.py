"""Tests for preflight checks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from dockcheck.init.detect import RepoContext
from dockcheck.init.preflight import PreflightChecker, PreflightItem, PreflightResult


class TestPreflightItem:
    def test_passed_item(self):
        item = PreflightItem(name="test", passed=True, message="ok")
        assert item.passed is True

    def test_failed_required_item(self):
        item = PreflightItem(
            name="test", passed=False, message="fail", required=True
        )
        assert item.passed is False
        assert item.required is True

    def test_failed_optional_item(self):
        item = PreflightItem(
            name="test", passed=False, message="warn", required=False
        )
        assert item.passed is False
        assert item.required is False


class TestPreflightResult:
    def test_blocking_items(self):
        result = PreflightResult(items=[
            PreflightItem(name="a", passed=True, message="ok"),
            PreflightItem(name="b", passed=False, message="fail", required=True),
            PreflightItem(name="c", passed=False, message="warn", required=False),
        ])
        assert len(result.blocking) == 1
        assert result.blocking[0].name == "b"

    def test_ready_when_no_blocking(self):
        result = PreflightResult(
            items=[PreflightItem(name="a", passed=True, message="ok")],
            ready=True,
        )
        assert result.ready is True

    def test_not_ready_when_blocking(self):
        result = PreflightResult(
            items=[
                PreflightItem(
                    name="a", passed=False, message="fail", required=True
                ),
            ],
            ready=False,
        )
        assert result.ready is False


class TestPreflightChecker:
    def test_cf_worker_all_ready(self, tmp_path: Path):
        """CF Worker project with wrangler installed and secrets ready."""
        (tmp_path / "wrangler.toml").write_text('name = "test"')
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / ".gitignore").write_text(".env\n")
        (tmp_path / ".dockcheck").mkdir()
        (tmp_path / ".dockcheck" / "policy.yaml").write_text("version: '1'")

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/wrangler"),
            patch("os.environ.get", side_effect=lambda k, d=None: {
                "CLOUDFLARE_API_TOKEN": "tok",
                "CLOUDFLARE_ACCOUNT_ID": "acc",
            }.get(k, d)),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.ready is True
        assert result.provider_name == "cloudflare"
        assert result.needs_init is False
        assert result.needs_auth is False

    def test_cf_worker_no_wrangler(self, tmp_path: Path):
        """CF Worker detected but wrangler not installed."""
        (tmp_path / "wrangler.toml").write_text('name = "test"')
        (tmp_path / "package.json").write_text('{"name": "test"}')

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value=None),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.ready is False
        assert result.missing_cli == "wrangler"
        cli_items = [i for i in result.items if i.name == "cli_tool"]
        assert len(cli_items) == 1
        assert cli_items[0].passed is False

    def test_no_provider_detected(self, tmp_path: Path):
        """Empty project — no deploy target."""
        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )
        with patch("subprocess.run", return_value=mock_git):
            result = PreflightChecker().check(str(tmp_path))

        assert result.ready is False
        assert result.provider_name is None

    def test_needs_init(self, tmp_path: Path):
        """Project with wrangler but no .dockcheck/ dir."""
        (tmp_path / "wrangler.toml").write_text('name = "test"')
        (tmp_path / "package.json").write_text('{"name": "test"}')

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/wrangler"),
            patch("os.environ.get", side_effect=lambda k, d=None: {
                "CLOUDFLARE_API_TOKEN": "tok",
                "CLOUDFLARE_ACCOUNT_ID": "acc",
            }.get(k, d)),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.needs_init is True
        init_items = [i for i in result.items if i.name == "init"]
        assert len(init_items) == 1
        assert init_items[0].passed is False

    def test_needs_auth(self, tmp_path: Path):
        """Project missing deploy secrets."""
        (tmp_path / "wrangler.toml").write_text('name = "test"')
        (tmp_path / "package.json").write_text('{"name": "test"}')

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/wrangler"),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.needs_auth is True
        assert "CLOUDFLARE_API_TOKEN" in result.missing_secrets

    def test_detects_lint_and_test(self, tmp_path: Path):
        """Preflight reports detected lint/test commands."""
        pkg = {"scripts": {"lint": "eslint .", "test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "wrangler.toml").write_text('name = "test"')

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/wrangler"),
        ):
            result = PreflightChecker().check(str(tmp_path))

        lint_items = [i for i in result.items if i.name == "lint"]
        test_items = [i for i in result.items if i.name == "test"]
        assert len(lint_items) == 1
        assert "npm run lint" in lint_items[0].message
        assert len(test_items) == 1
        assert "npm test" in test_items[0].message

    def test_accepts_prebuilt_context(self, tmp_path: Path):
        """PreflightChecker accepts an existing RepoContext."""
        ctx = RepoContext(
            language="javascript",
            has_wrangler_config=True,
            gitignore_covers_env=True,
        )

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/wrangler"),
        ):
            result = PreflightChecker().check(str(tmp_path), ctx=ctx)

        assert result.provider_name == "cloudflare"

    def test_render_availability_via_env_var(self, tmp_path: Path):
        """Render uses env var check instead of shutil.which for CLI availability."""
        (tmp_path / "render.yaml").write_text("services:\n- type: web")
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / ".gitignore").write_text(".env\n")

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value=None),
            patch("os.environ.get", side_effect=lambda k, d=None: {
                "RENDER_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-xxx",
            }.get(k, d)),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.provider_name == "render"
        # CLI should be available because Render uses env var, not shutil.which
        cli_items = [i for i in result.items if i.name == "cli_tool"]
        assert len(cli_items) == 1
        assert cli_items[0].passed is True
        assert "RENDER_DEPLOY_HOOK_URL set" in cli_items[0].message

    def test_render_missing_shows_env_var_message(self, tmp_path: Path):
        """Render missing shows 'RENDER_DEPLOY_HOOK_URL not set', not 'render not found'."""
        (tmp_path / "render.yaml").write_text("services:\n- type: web")
        (tmp_path / "package.json").write_text('{"name": "test"}')

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value=None),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.provider_name == "render"
        cli_items = [i for i in result.items if i.name == "cli_tool"]
        assert len(cli_items) == 1
        assert cli_items[0].passed is False
        assert "RENDER_DEPLOY_HOOK_URL not set" in cli_items[0].message
        assert "render not found" not in cli_items[0].message

    def test_install_hint_on_preflight_result(self, tmp_path: Path):
        """PreflightResult carries install_hint when CLI is missing."""
        (tmp_path / "fly.toml").write_text('app = "test"')
        (tmp_path / "package.json").write_text('{"name": "test"}')

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value=None),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.missing_cli == "fly"
        assert "curl -L https://fly.io/install.sh" in result.install_hint

    def test_optional_secrets_not_in_missing_secrets(self, tmp_path: Path):
        """Optional secrets should not appear in result.missing_secrets."""
        (tmp_path / "netlify.toml").write_text('[build]\ncommand = "npm build"')
        (tmp_path / "package.json").write_text('{"name": "test"}')

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/netlify"),
            patch("os.environ.get", side_effect=lambda k, d=None: {
                "NETLIFY_AUTH_TOKEN": "tok",
            }.get(k, d)),
        ):
            result = PreflightChecker().check(str(tmp_path))

        # NETLIFY_SITE_ID is optional — should NOT be in missing_secrets
        assert "NETLIFY_SITE_ID" not in result.missing_secrets
        # Auth should be ready since only required secrets matter
        assert result.needs_auth is False

    def test_new_provider_detection_sam(self, tmp_path: Path):
        """SAM template.yaml triggers aws-lambda provider detection."""
        (tmp_path / "template.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'")

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/sam"),
        ):
            result = PreflightChecker().check(str(tmp_path))

        assert result.provider_name == "aws-lambda"

    def test_gitignore_check(self, tmp_path: Path):
        """Preflight reports .gitignore status."""
        (tmp_path / "wrangler.toml").write_text('name = "test"')
        (tmp_path / "package.json").write_text('{"name": "test"}')
        # No .gitignore

        mock_git = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )

        with (
            patch("subprocess.run", return_value=mock_git),
            patch("shutil.which", return_value="/usr/local/bin/wrangler"),
        ):
            result = PreflightChecker().check(str(tmp_path))

        gi_items = [i for i in result.items if i.name == "gitignore"]
        assert len(gi_items) == 1
        assert gi_items[0].passed is False
        assert gi_items[0].required is False  # auto-fixable
