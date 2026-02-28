"""Deploy providers — abstract deploy interface with CF Workers and Vercel."""

from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod

from pydantic import BaseModel


class DeployResult(BaseModel):
    """Result of a deploy operation."""

    success: bool
    provider: str
    url: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class DeployProvider(ABC):
    """Abstract base for deploy providers."""

    @abstractmethod
    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class CloudflareProvider(DeployProvider):
    """Deploys via `wrangler deploy`."""

    @property
    def name(self) -> str:
        return "cloudflare"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("wrangler") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        run_env = {**os.environ, **(env or {})}

        try:
            result = subprocess.run(
                ["wrangler", "deploy"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=120,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False,
                provider=self.name,
                error="wrangler CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False,
                provider=self.name,
                error="Deploy timed out after 120 seconds",
            )

        url = self._extract_url(result.stdout)

        return DeployResult(
            success=result.returncode == 0,
            provider=self.name,
            url=url,
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.stderr if result.returncode != 0 else None,
        )

    @staticmethod
    def _extract_url(stdout: str) -> str | None:
        """Extract deployed URL from wrangler output."""
        match = re.search(r"https://\S+\.workers\.dev", stdout)
        return match.group(0) if match else None


class VercelProvider(DeployProvider):
    """Deploys via `vercel deploy --prod --yes`."""

    @property
    def name(self) -> str:
        return "vercel"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("vercel") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        run_env = {**os.environ, **(env or {})}

        try:
            result = subprocess.run(
                ["vercel", "deploy", "--prod", "--yes"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=120,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False,
                provider=self.name,
                error="vercel CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False,
                provider=self.name,
                error="Deploy timed out after 120 seconds",
            )

        url = self._extract_url(result.stdout)

        return DeployResult(
            success=result.returncode == 0,
            provider=self.name,
            url=url,
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.stderr if result.returncode != 0 else None,
        )

    @staticmethod
    def _extract_url(stdout: str) -> str | None:
        """Extract production URL from vercel output."""
        match = re.search(r"https://\S+\.vercel\.app", stdout)
        return match.group(0) if match else None


class DeployProviderFactory:
    """Factory for creating deploy providers by name."""

    _providers: dict[str, type[DeployProvider]] = {
        "cloudflare": CloudflareProvider,
        "vercel": VercelProvider,
    }

    @classmethod
    def get(cls, provider_name: str) -> DeployProvider:
        provider_cls = cls._providers.get(provider_name)
        if provider_cls is None:
            raise KeyError(
                f"Unknown deploy provider: {provider_name!r}. "
                f"Available: {sorted(cls._providers)}"
            )
        return provider_cls()

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._providers.keys())
