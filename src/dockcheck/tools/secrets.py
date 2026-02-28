"""Read-only secret provider — secrets are never logged or printed."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MaskedSecret:
    """
    Wraps a secret value so it is never accidentally exposed via repr/str.

    ``__repr__`` and ``__str__`` both return ``'***'``.
    Use ``.reveal()`` only when the raw value is genuinely required
    (e.g., injecting into a container environment dict).
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        # Store in a slot — not a normal attribute — to reduce accidental exposure
        object.__setattr__(self, "_value", value)

    def __repr__(self) -> str:
        return "***"

    def __str__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MaskedSecret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def reveal(self) -> str:
        """Return the raw secret value. Use sparingly."""
        return self._value


class SecretLoadError(Exception):
    """Raised when the .env file cannot be parsed."""


def _parse_env_file(path: Path) -> Dict[str, str]:
    """
    Minimal .env parser — handles ``KEY=VALUE`` lines, ignores comments.

    Does not execute shell expansions or support multiline values.
    """
    result: Dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SecretLoadError(
                f"Invalid .env line {lineno}: expected KEY=VALUE, got: {line!r}"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


class SecretProvider:
    """
    Read-only secret provider.

    Loads secrets from environment variables and an optional ``.env`` file.
    Secrets are **never** logged or printed — all values are wrapped in
    ``MaskedSecret``.

    Example usage::

        provider = SecretProvider(env_file=".env")
        secret = provider.get("DATABASE_URL")
        env = provider.inject({}, ["DATABASE_URL", "API_KEY"])
    """

    def __init__(self, env_file: Optional[str] = None) -> None:
        self._store: Dict[str, MaskedSecret] = {}

        # Load process environment first
        for key, value in os.environ.items():
            self._store[key] = MaskedSecret(value)

        # Override / extend with .env file (file values take precedence)
        if env_file is not None:
            env_path = Path(env_file)
            if env_path.exists():
                try:
                    file_secrets = _parse_env_file(env_path)
                    for key, value in file_secrets.items():
                        self._store[key] = MaskedSecret(value)
                    logger.debug(
                        "Loaded %d secret(s) from env file (names only logged).",
                        len(file_secrets),
                    )
                except SecretLoadError:
                    logger.exception("Failed to parse env file: %s", env_file)
            else:
                logger.warning("env_file not found, skipping: %s", env_file)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[MaskedSecret]:
        """
        Return the ``MaskedSecret`` for *name*, or ``None`` if not set.

        The value is **never** logged.
        """
        secret = self._store.get(name)
        if secret is None:
            logger.debug("Secret requested but not found: %s", name)
        return secret

    def inject(self, target: Dict[str, str], secret_names: List[str]) -> Dict[str, str]:
        """
        Copy named secrets into *target* dict, revealing values for container injection.

        Missing secrets are logged at WARNING level but are not injected.
        The resulting dict is returned (same object as *target*).
        """
        for name in secret_names:
            secret = self._store.get(name)
            if secret is None:
                logger.warning("inject: secret '%s' not available — skipping.", name)
                continue
            target[name] = secret.reveal()
        return target

    def available_keys(self) -> List[str]:
        """Return the names of all loaded secrets. Values are never returned."""
        return sorted(self._store.keys())
