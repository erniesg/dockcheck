"""Tests for auth bootstrapper — secret checking, gitignore, mock gh CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from dockcheck.init.auth import AuthBootstrapper, AuthStatus, SecretStatus
from dockcheck.init.providers import ProviderRegistry
from dockcheck.tools.secrets import MaskedSecret


class TestAuthCheck:
    def test_all_secrets_available(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok123")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc123")

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")

        with patch.object(auth, "_list_github_secrets", return_value=set()):
            status = auth.check(cf)

        assert status.all_ready is True
        assert status.provider == "cloudflare"
        assert len(status.secrets) == 2

    def test_missing_secrets(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")

        with patch.object(auth, "_list_github_secrets", return_value=set()):
            status = auth.check(cf)

        assert status.all_ready is False
        missing_names = [s.name for s in status.secrets if not s.available_local]
        assert "CLOUDFLARE_API_TOKEN" in missing_names

    def test_secrets_from_env_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text("CLOUDFLARE_API_TOKEN=from-file\nCLOUDFLARE_ACCOUNT_ID=acc\n")

        auth = AuthBootstrapper(env_file=str(env_file))
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")

        with patch.object(auth, "_list_github_secrets", return_value=set()):
            status = auth.check(cf)

        assert status.all_ready is True

    def test_github_secret_detected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")

        with patch.object(
            auth, "_list_github_secrets",
            return_value={"CLOUDFLARE_API_TOKEN"},
        ):
            status = auth.check(cf)

        token_status = next(
            s for s in status.secrets
            if s.name == "CLOUDFLARE_API_TOKEN"
        )
        assert token_status.available_github is True


class TestGitignoreEnforcement:
    def test_creates_gitignore_if_missing(self, tmp_path: Path):
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        modified = auth.ensure_gitignore(str(tmp_path))
        assert modified is True
        content = (tmp_path / ".gitignore").read_text()
        assert ".env" in content
        assert ".env.*" in content
        assert ".dev.vars" in content

    def test_updates_existing_gitignore(self, tmp_path: Path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n")

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        modified = auth.ensure_gitignore(str(tmp_path))
        assert modified is True
        content = gitignore.read_text()
        assert "node_modules/" in content
        assert ".env" in content
        assert "dockcheck" in content  # comment section

    def test_no_change_if_already_covered(self, tmp_path: Path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".env\n.env.*\n.dev.vars\n")

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        modified = auth.ensure_gitignore(str(tmp_path))
        assert modified is False


class TestStoreLocal:
    def test_creates_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        auth = AuthBootstrapper(env_file=str(env_file))

        secrets = {
            "API_KEY": MaskedSecret("secret123"),
            "DB_URL": MaskedSecret("postgres://localhost"),
        }
        auth.store_local(secrets)

        content = env_file.read_text()
        assert "API_KEY=secret123" in content
        assert "DB_URL=postgres://localhost" in content

    def test_appends_to_existing_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=value\n")

        auth = AuthBootstrapper(env_file=str(env_file))
        secrets = {"NEW_KEY": MaskedSecret("new_value")}
        auth.store_local(secrets)

        content = env_file.read_text()
        assert "EXISTING=value" in content
        assert "NEW_KEY=new_value" in content

    def test_no_duplicate_keys(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=old_value\n")

        auth = AuthBootstrapper(env_file=str(env_file))
        secrets = {"API_KEY": MaskedSecret("new_value")}
        auth.store_local(secrets)

        content = env_file.read_text()
        # Should not add a duplicate
        assert content.count("API_KEY=") == 1
        assert "API_KEY=old_value" in content


class TestStoreGitHub:
    def test_store_github_success(self, tmp_path: Path):
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        secrets = {"MY_SECRET": MaskedSecret("value123")}

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            ok = auth.store_github(secrets)

        assert ok is True
        # Verify value was piped to stdin, not as CLI arg
        call_args = mock_run.call_args
        assert call_args.kwargs["input"] == "value123"
        assert "value123" not in call_args.args[0]

    def test_store_github_failure(self, tmp_path: Path):
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        secrets = {"MY_SECRET": MaskedSecret("value")}

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        with patch("subprocess.run", return_value=mock_result):
            ok = auth.store_github(secrets)

        assert ok is False

    def test_store_github_no_gh_cli(self, tmp_path: Path):
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        secrets = {"MY_SECRET": MaskedSecret("value")}

        with patch("subprocess.run", side_effect=FileNotFoundError):
            ok = auth.store_github(secrets)

        assert ok is False


class TestListGitHubSecrets:
    def test_list_parses_output(self, tmp_path: Path):
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        mock_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "CLOUDFLARE_API_TOKEN\tUpdated 2026-01-01\n"
                "CLOUDFLARE_ACCOUNT_ID\tUpdated 2026-01-01\n"
            ),
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            secrets = auth._list_github_secrets()

        assert "CLOUDFLARE_API_TOKEN" in secrets
        assert "CLOUDFLARE_ACCOUNT_ID" in secrets

    def test_list_empty_on_failure(self, tmp_path: Path):
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not logged in"
        )
        with patch("subprocess.run", return_value=mock_result):
            secrets = auth._list_github_secrets()

        assert secrets == set()

    def test_list_empty_on_no_gh(self, tmp_path: Path):
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        with patch("subprocess.run", side_effect=FileNotFoundError):
            secrets = auth._list_github_secrets()

        assert secrets == set()


class TestSecretSafety:
    def test_masked_secret_not_in_logs(self, tmp_path: Path):
        """Verify MaskedSecret prevents leaking in string operations."""
        secret = MaskedSecret("super-secret-value")
        # str/repr should mask
        assert "super-secret-value" not in str(secret)
        assert "super-secret-value" not in repr(secret)
        assert "super-secret-value" not in f"Secret: {secret}"

    def test_gh_secret_set_uses_stdin(self, tmp_path: Path):
        """Verify secrets go to stdin, not CLI args."""
        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        secrets = {"TOKEN": MaskedSecret("my-secret-token")}

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            auth.store_github(secrets)

        call_args = mock_run.call_args
        # Command should be ["gh", "secret", "set", "TOKEN"]
        cmd = call_args.args[0]
        assert cmd == ["gh", "secret", "set", "TOKEN"]
        # Value goes via input kwarg
        assert call_args.kwargs["input"] == "my-secret-token"


class TestOptionalSecrets:
    def test_optional_secrets_dont_block_all_ready(self, tmp_path: Path, monkeypatch):
        """Optional secrets missing should not block all_ready."""
        monkeypatch.setenv("NETLIFY_AUTH_TOKEN", "tok")
        monkeypatch.delenv("NETLIFY_SITE_ID", raising=False)

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        registry = ProviderRegistry()
        netlify = registry.get("netlify")

        with patch.object(auth, "_list_github_secrets", return_value=set()):
            status = auth.check(netlify)

        assert status.all_ready is True
        # NETLIFY_SITE_ID is optional (required=False)
        site_id = next(s for s in status.secrets if s.name == "NETLIFY_SITE_ID")
        assert site_id.required is False
        assert site_id.available_local is False

    def test_required_secret_missing_blocks_all_ready(self, tmp_path: Path, monkeypatch):
        """Required secret missing should block all_ready even if optional ones are set."""
        monkeypatch.delenv("NETLIFY_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("NETLIFY_SITE_ID", "site-id")

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        registry = ProviderRegistry()
        netlify = registry.get("netlify")

        with patch.object(auth, "_list_github_secrets", return_value=set()):
            status = auth.check(netlify)

        assert status.all_ready is False

    def test_secret_status_carries_required_flag(self, tmp_path: Path, monkeypatch):
        """SecretStatus.required should reflect the spec's required field."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "key")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        monkeypatch.delenv("AWS_REGION", raising=False)

        auth = AuthBootstrapper(env_file=str(tmp_path / ".env"))
        registry = ProviderRegistry()
        aws = registry.get("aws-lambda")

        with patch.object(auth, "_list_github_secrets", return_value=set()):
            status = auth.check(aws)

        assert status.all_ready is True
        region = next(s for s in status.secrets if s.name == "AWS_REGION")
        assert region.required is False


class TestAuthStatusModel:
    def test_auth_status_defaults(self):
        status = AuthStatus(provider="test")
        assert status.secrets == []
        assert status.all_ready is False

    def test_secret_status_defaults(self):
        s = SecretStatus(name="MY_KEY")
        assert s.available_local is False
        assert s.available_github is False
        assert s.setup_url == ""
        assert s.required is True
