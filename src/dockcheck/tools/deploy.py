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


class FlyProvider(DeployProvider):
    """Deploys via `fly deploy`."""

    @property
    def name(self) -> str:
        return "fly"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("fly") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        run_env = {**os.environ, **(env or {})}

        try:
            result = subprocess.run(
                ["fly", "deploy"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=300,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False, provider=self.name, error="fly CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, provider=self.name,
                error="Deploy timed out after 300 seconds",
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
        match = re.search(r"https://\S+\.fly\.dev", stdout)
        return match.group(0) if match else None


class NetlifyProvider(DeployProvider):
    """Deploys via `netlify deploy --prod`."""

    @property
    def name(self) -> str:
        return "netlify"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("netlify") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        run_env = {**os.environ, **(env or {})}

        try:
            result = subprocess.run(
                ["netlify", "deploy", "--prod"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=180,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False, provider=self.name, error="netlify CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, provider=self.name,
                error="Deploy timed out after 180 seconds",
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
        match = re.search(r"https://\S+\.netlify\.app", stdout)
        return match.group(0) if match else None


class DockerRegistryProvider(DeployProvider):
    """Deploys via `docker build` + `docker push`."""

    @property
    def name(self) -> str:
        return "docker-registry"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("docker") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os
        from pathlib import Path

        run_env = {**os.environ, **(env or {})}
        image = os.environ.get("DOCKER_IMAGE")
        if not image:
            username = os.environ.get("DOCKER_USERNAME", "app")
            dirname = Path(workdir).resolve().name
            image = f"{username}/{dirname}:latest"

        # Build
        try:
            build = subprocess.run(
                ["docker", "build", "-t", image, "."],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=300,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False, provider=self.name, error="docker CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, provider=self.name,
                error="Build timed out after 300 seconds",
            )

        if build.returncode != 0:
            return DeployResult(
                success=False, provider=self.name,
                stdout=build.stdout, stderr=build.stderr,
                error=build.stderr or "docker build failed",
            )

        # Push
        try:
            push = subprocess.run(
                ["docker", "push", image],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=300,
                env=run_env,
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, provider=self.name,
                error="Push timed out after 300 seconds",
            )

        return DeployResult(
            success=push.returncode == 0,
            provider=self.name,
            url=None,
            stdout=push.stdout,
            stderr=push.stderr,
            error=push.stderr if push.returncode != 0 else None,
        )


class AwsLambdaProvider(DeployProvider):
    """Deploys via `sam deploy --no-confirm-changeset`."""

    @property
    def name(self) -> str:
        return "aws-lambda"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("sam") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        run_env = {**os.environ, **(env or {})}

        try:
            result = subprocess.run(
                ["sam", "deploy", "--no-confirm-changeset"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=600,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False, provider=self.name, error="sam CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, provider=self.name,
                error="Deploy timed out after 600 seconds",
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
        match = re.search(r"https://\S+\.execute-api\.\S+\.amazonaws\.com\S*", stdout)
        return match.group(0) if match else None


class GcpCloudRunProvider(DeployProvider):
    """Deploys via `gcloud run deploy --source .`."""

    @property
    def name(self) -> str:
        return "gcp-cloudrun"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("gcloud") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        run_env = {**os.environ, **(env or {})}

        try:
            result = subprocess.run(
                ["gcloud", "run", "deploy", "--source", "."],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=600,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False, provider=self.name, error="gcloud CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, provider=self.name,
                error="Deploy timed out after 600 seconds",
            )

        # Cloud Run may output URL to stdout or stderr
        url = self._extract_url(result.stdout) or self._extract_url(result.stderr)
        return DeployResult(
            success=result.returncode == 0,
            provider=self.name,
            url=url,
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.stderr if result.returncode != 0 else None,
        )

    @staticmethod
    def _extract_url(output: str) -> str | None:
        match = re.search(r"https://\S+\.run\.app", output)
        return match.group(0) if match else None


class RailwayProvider(DeployProvider):
    """Deploys via `railway up`."""

    @property
    def name(self) -> str:
        return "railway"

    def is_available(self) -> bool:
        import shutil

        return shutil.which("railway") is not None

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        run_env = {**os.environ, **(env or {})}

        try:
            result = subprocess.run(
                ["railway", "up"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=300,
                env=run_env,
            )
        except FileNotFoundError:
            return DeployResult(
                success=False, provider=self.name, error="railway CLI not found",
            )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, provider=self.name,
                error="Deploy timed out after 300 seconds",
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
        match = re.search(r"https://\S+\.up\.railway\.app", stdout)
        return match.group(0) if match else None


class RenderProvider(DeployProvider):
    """Deploys via Render deploy hook (HTTP POST)."""

    @property
    def name(self) -> str:
        return "render"

    def is_available(self) -> bool:
        import os

        return bool(os.environ.get("RENDER_DEPLOY_HOOK_URL"))

    def deploy(
        self,
        workdir: str = ".",
        env: dict[str, str] | None = None,
    ) -> DeployResult:
        import os

        import httpx

        hook_url = os.environ.get("RENDER_DEPLOY_HOOK_URL")
        if not hook_url:
            return DeployResult(
                success=False,
                provider=self.name,
                error="RENDER_DEPLOY_HOOK_URL not set",
            )

        try:
            response = httpx.post(hook_url, timeout=30)
        except httpx.HTTPError as exc:
            return DeployResult(
                success=False,
                provider=self.name,
                error=f"Deploy hook request failed: {exc}",
            )

        if response.status_code >= 400:
            return DeployResult(
                success=False,
                provider=self.name,
                error=f"Deploy hook returned {response.status_code}",
                stderr=response.text,
            )

        return DeployResult(
            success=True,
            provider=self.name,
            stdout=response.text,
        )


class DeployProviderFactory:
    """Factory for creating deploy providers by name."""

    _providers: dict[str, type[DeployProvider]] = {
        "cloudflare": CloudflareProvider,
        "vercel": VercelProvider,
        "fly": FlyProvider,
        "netlify": NetlifyProvider,
        "docker-registry": DockerRegistryProvider,
        "aws-lambda": AwsLambdaProvider,
        "gcp-cloudrun": GcpCloudRunProvider,
        "railway": RailwayProvider,
        "render": RenderProvider,
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
