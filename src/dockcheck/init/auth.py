"""Auth bootstrap — check, prompt, and store deploy secrets safely."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click
from pydantic import BaseModel, Field

from dockcheck.init.providers import ProviderSpec
from dockcheck.tools.secrets import MaskedSecret


class SecretStatus(BaseModel):
    """Status of a single secret across local and GitHub."""

    name: str
    available_local: bool = False
    available_github: bool = False
    setup_url: str = ""
    required: bool = True


class AuthStatus(BaseModel):
    """Auth readiness for a provider."""

    provider: str
    secrets: list[SecretStatus] = Field(default_factory=list)
    all_ready: bool = False


class AuthBootstrapper:
    """Checks, prompts, and stores deploy secrets."""

    def __init__(self, env_file: str = ".env") -> None:
        self._env_file = env_file

    def check(self, provider: ProviderSpec) -> AuthStatus:
        """Check which secrets are available locally and on GitHub."""
        gh_secrets = self._list_github_secrets()
        statuses: list[SecretStatus] = []

        for spec in provider.required_secrets:
            local = self._has_local(spec.name)
            github = spec.name in gh_secrets

            statuses.append(
                SecretStatus(
                    name=spec.name,
                    available_local=local,
                    available_github=github,
                    setup_url=spec.setup_url,
                    required=spec.required,
                )
            )

        all_ready = all(s.available_local for s in statuses if s.required)

        return AuthStatus(
            provider=provider.name,
            secrets=statuses,
            all_ready=all_ready,
        )

    def prompt_missing(self, status: AuthStatus) -> dict[str, MaskedSecret]:
        """Interactively prompt for missing secrets. Returns name→MaskedSecret."""
        collected: dict[str, MaskedSecret] = {}

        for secret in status.secrets:
            if secret.available_local:
                continue
            click.echo(f"  Get at: {secret.setup_url}")
            value = click.prompt(
                f"  {secret.name}",
                hide_input=True,
            )
            collected[secret.name] = MaskedSecret(value)

        return collected

    def store_local(
        self,
        secrets: dict[str, MaskedSecret],
        env_file: str | None = None,
    ) -> None:
        """Append secrets to .env file (values revealed only here)."""
        target = Path(env_file or self._env_file)

        # Read existing content to avoid duplicates
        existing_keys: set[str] = set()
        if target.exists():
            for line in target.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    existing_keys.add(stripped.partition("=")[0].strip())

        lines_to_add: list[str] = []
        for name, masked in secrets.items():
            if name not in existing_keys:
                lines_to_add.append(f"{name}={masked.reveal()}")

        if lines_to_add:
            with target.open("a", encoding="utf-8") as f:
                # Add newline separator if file exists and doesn't end with newline
                if target.stat().st_size > 0:
                    content = target.read_text(encoding="utf-8")
                    if content and not content.endswith("\n"):
                        f.write("\n")
                for line in lines_to_add:
                    f.write(line + "\n")

    def store_github(self, secrets: dict[str, MaskedSecret]) -> bool:
        """Store secrets as GitHub Actions secrets via `gh secret set`.

        Values are piped to stdin — never passed as CLI arguments.
        Returns True if all secrets were stored successfully.
        """
        all_ok = True
        for name, masked in secrets.items():
            try:
                result = subprocess.run(
                    ["gh", "secret", "set", name],
                    input=masked.reveal(),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    click.echo(f"  Warning: failed to set {name} on GitHub")
                    all_ok = False
            except (FileNotFoundError, subprocess.TimeoutExpired):
                click.echo(f"  Warning: gh CLI not available, skipped {name}")
                all_ok = False
        return all_ok

    def ensure_gitignore(self, path: str = ".") -> bool:
        """Ensure .gitignore covers .env files. Returns True if modified."""
        gitignore = Path(path) / ".gitignore"
        required_patterns = [".env", ".env.*", ".dev.vars"]

        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            lines = {line.strip() for line in content.splitlines()}
        else:
            content = ""
            lines = set()

        to_add: list[str] = []
        for pattern in required_patterns:
            if pattern not in lines:
                to_add.append(pattern)

        if not to_add:
            return False

        with gitignore.open("a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            if lines:  # File has content, add a comment section
                f.write("\n# dockcheck — secrets\n")
            for pattern in to_add:
                f.write(pattern + "\n")

        return True

    def _has_local(self, name: str) -> bool:
        """Check if secret exists in environment or .env file."""
        if os.environ.get(name):
            return True
        env_path = Path(self._env_file)
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if (
                        stripped
                        and not stripped.startswith("#")
                        and "=" in stripped
                    ):
                        key = stripped.partition("=")[0].strip()
                        if key == name:
                            return True
            except OSError:
                pass
        return False

    def _list_github_secrets(self) -> set[str]:
        """List GitHub Actions secret names via `gh secret list`."""
        try:
            result = subprocess.run(
                ["gh", "secret", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return set()
            names: set[str] = set()
            for line in result.stdout.splitlines():
                # Format: NAME\tUpdated YYYY-MM-DD
                parts = line.split("\t")
                if parts:
                    names.add(parts[0].strip())
            return names
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return set()
