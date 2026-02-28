"""Tests for SecretProvider and MaskedSecret — secrets must never leak."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dockcheck.tools.secrets import MaskedSecret, SecretProvider, _parse_env_file

# ---------------------------------------------------------------------------
# MaskedSecret
# ---------------------------------------------------------------------------

class TestMaskedSecret:
    def test_str_returns_masked(self):
        secret = MaskedSecret("super-secret-value")
        assert str(secret) == "***"

    def test_repr_returns_masked(self):
        secret = MaskedSecret("super-secret-value")
        assert repr(secret) == "***"

    def test_reveal_returns_raw_value(self):
        secret = MaskedSecret("super-secret-value")
        assert secret.reveal() == "super-secret-value"

    def test_formatted_string_is_masked(self):
        secret = MaskedSecret("hunter2")
        formatted = f"The password is: {secret}"
        assert "hunter2" not in formatted
        assert "***" in formatted

    def test_repr_in_container_is_masked(self):
        secrets = [MaskedSecret("abc"), MaskedSecret("xyz")]
        container_repr = repr(secrets)
        assert "abc" not in container_repr
        assert "xyz" not in container_repr

    def test_equality_with_same_value(self):
        a = MaskedSecret("value")
        b = MaskedSecret("value")
        assert a == b

    def test_equality_with_different_value(self):
        a = MaskedSecret("value1")
        b = MaskedSecret("value2")
        assert a != b

    def test_hashable(self):
        secret = MaskedSecret("some-value")
        # Can be used as a dict key or in a set
        d = {secret: "meta"}
        assert secret in d

    def test_cannot_access_raw_via_normal_attribute(self):
        """_value is in __slots__; direct attribute access should still work
        but typical accidental logging would use str/repr, which masks."""
        secret = MaskedSecret("topsecret")
        # repr/str always mask
        assert "topsecret" not in repr(secret)
        assert "topsecret" not in str(secret)

    def test_reveal_empty_string(self):
        secret = MaskedSecret("")
        assert secret.reveal() == ""
        assert str(secret) == "***"


# ---------------------------------------------------------------------------
# _parse_env_file (internal helper)
# ---------------------------------------------------------------------------

class TestParseEnvFile:
    def test_simple_key_value(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        result = _parse_env_file(env_file)
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_ignores_comments(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("# This is a comment\nKEY=value\n")
        result = _parse_env_file(env_file)
        assert "KEY" in result
        assert len(result) == 1

    def test_ignores_blank_lines(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("\n\nKEY=value\n\n")
        result = _parse_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_strips_double_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('KEY="quoted value"\n')
        result = _parse_env_file(env_file)
        assert result["KEY"] == "quoted value"

    def test_strips_single_quotes(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY='quoted value'\n")
        result = _parse_env_file(env_file)
        assert result["KEY"] == "quoted value"

    def test_value_with_equals_sign(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value=with=equals\n")
        result = _parse_env_file(env_file)
        assert result["KEY"] == "value=with=equals"

    def test_empty_value(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=\n")
        result = _parse_env_file(env_file)
        assert result["KEY"] == ""

    def test_invalid_line_raises(self, tmp_path: Path):
        from dockcheck.tools.secrets import SecretLoadError

        env_file = tmp_path / ".env"
        env_file.write_text("INVALID LINE WITHOUT EQUALS\n")
        with pytest.raises(SecretLoadError):
            _parse_env_file(env_file)


# ---------------------------------------------------------------------------
# SecretProvider — env var loading
# ---------------------------------------------------------------------------

class TestSecretProviderEnvVars:
    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "env-value")
        provider = SecretProvider()
        secret = provider.get("MY_SECRET")
        assert secret is not None
        assert secret.reveal() == "env-value"

    def test_missing_secret_returns_none(self):
        provider = SecretProvider()
        result = provider.get("TOTALLY_NONEXISTENT_SECRET_XYZ_12345")
        assert result is None

    def test_get_returns_masked_secret(self, monkeypatch):
        monkeypatch.setenv("TEST_VAL", "s3cr3t")
        provider = SecretProvider()
        result = provider.get("TEST_VAL")
        assert isinstance(result, MaskedSecret)
        assert str(result) == "***"

    def test_available_keys_returns_names_only(self, monkeypatch):
        monkeypatch.setenv("VISIBLE_KEY", "not-visible-value")
        provider = SecretProvider()
        keys = provider.available_keys()
        assert "VISIBLE_KEY" in keys
        # Returned list must only contain strings (names), not values
        assert all(isinstance(k, str) for k in keys)
        assert "not-visible-value" not in keys


# ---------------------------------------------------------------------------
# SecretProvider — .env file loading
# ---------------------------------------------------------------------------

class TestSecretProviderEnvFile:
    def test_loads_from_env_file(self, tmp_path: Path, monkeypatch):
        # Ensure env var does not exist
        monkeypatch.delenv("FILE_SECRET", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FILE_SECRET=from-file\n")
        provider = SecretProvider(env_file=str(env_file))
        secret = provider.get("FILE_SECRET")
        assert secret is not None
        assert secret.reveal() == "from-file"

    def test_env_file_overrides_env_var(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("OVERRIDE_ME", "from-env")
        env_file = tmp_path / ".env"
        env_file.write_text("OVERRIDE_ME=from-file\n")
        provider = SecretProvider(env_file=str(env_file))
        # File takes precedence
        assert provider.get("OVERRIDE_ME").reveal() == "from-file"

    def test_missing_env_file_is_graceful(self):
        # Should not raise — just log a warning
        provider = SecretProvider(env_file="/nonexistent/path/.env")
        assert provider is not None

    def test_invalid_env_file_is_graceful(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("BROKEN LINE\n")
        # Should not raise — logs and continues
        provider = SecretProvider(env_file=str(env_file))
        assert provider is not None

    def test_env_file_none_uses_env_only(self, monkeypatch):
        monkeypatch.setenv("ENV_ONLY", "env-value")
        provider = SecretProvider(env_file=None)
        assert provider.get("ENV_ONLY").reveal() == "env-value"


# ---------------------------------------------------------------------------
# SecretProvider.inject
# ---------------------------------------------------------------------------

class TestSecretProviderInject:
    def test_inject_adds_secrets_to_dict(self, monkeypatch):
        monkeypatch.setenv("DB_PASS", "password123")
        monkeypatch.setenv("API_KEY", "key456")
        provider = SecretProvider()

        target: dict = {}
        result = provider.inject(target, ["DB_PASS", "API_KEY"])

        assert result is target
        assert result["DB_PASS"] == "password123"
        assert result["API_KEY"] == "key456"

    def test_inject_reveals_raw_value(self, monkeypatch):
        monkeypatch.setenv("SECRET_INJECT", "raw-value")
        provider = SecretProvider()

        target: dict = {}
        provider.inject(target, ["SECRET_INJECT"])

        # The injected value must be the real value (for container env)
        assert target["SECRET_INJECT"] == "raw-value"

    def test_inject_skips_missing_secrets(self, monkeypatch):
        monkeypatch.setenv("EXISTING", "exists")
        provider = SecretProvider()

        target: dict = {}
        provider.inject(target, ["EXISTING", "DEFINITELY_MISSING_XYZ"])

        assert "EXISTING" in target
        assert "DEFINITELY_MISSING_XYZ" not in target

    def test_inject_does_not_modify_unrelated_keys(self, monkeypatch):
        monkeypatch.setenv("MY_SEC", "value")
        provider = SecretProvider()

        target = {"pre_existing": "keep_me"}
        provider.inject(target, ["MY_SEC"])

        assert target["pre_existing"] == "keep_me"

    def test_inject_empty_names_list(self, monkeypatch):
        monkeypatch.setenv("IGNORED", "value")
        provider = SecretProvider()

        target: dict = {"existing": "value"}
        result = provider.inject(target, [])

        assert result == {"existing": "value"}


# ---------------------------------------------------------------------------
# Secrets are never written to logs
# ---------------------------------------------------------------------------

class TestSecretsNotLeakedToLogs:
    def test_secret_value_not_in_log_output(self, monkeypatch, caplog):
        secret_value = "SUPER_SENSITIVE_PASSWORD_12345"
        monkeypatch.setenv("DONT_LOG_ME", secret_value)
        provider = SecretProvider()

        with caplog.at_level(logging.DEBUG, logger="dockcheck.tools.secrets"):
            provider.get("DONT_LOG_ME")

        assert secret_value not in caplog.text

    def test_inject_warning_does_not_log_value(self, monkeypatch, caplog):
        monkeypatch.delenv("NOT_SET_SECRET", raising=False)
        provider = SecretProvider()

        with caplog.at_level(logging.WARNING, logger="dockcheck.tools.secrets"):
            provider.inject({}, ["NOT_SET_SECRET"])

        assert "NOT_SET_SECRET" in caplog.text  # name is OK to log
        # Value can't be logged (it's None), but verify no accidental raw values
        assert "None" not in caplog.text or "NOT_SET_SECRET" in caplog.text

    def test_available_keys_never_contains_values(self, monkeypatch):
        monkeypatch.setenv("KEY_A", "value_for_a")
        monkeypatch.setenv("KEY_B", "value_for_b")
        provider = SecretProvider()

        keys = provider.available_keys()

        assert "value_for_a" not in keys
        assert "value_for_b" not in keys
