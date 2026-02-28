"""Provider registry — deploy target definitions and detection."""

from __future__ import annotations

import shutil

from pydantic import BaseModel, Field

from dockcheck.init.detect import RepoContext


class SecretSpec(BaseModel):
    """A secret required by a deploy provider."""

    name: str
    description: str
    setup_url: str
    required: bool = True


class ProviderSpec(BaseModel):
    """Definition of a deploy provider."""

    name: str
    display_name: str
    required_secrets: list[SecretSpec] = Field(default_factory=list)
    cli_tool: str
    deploy_command: str
    detect_files: list[str] = Field(default_factory=list)
    supported_languages: list[str] = Field(default_factory=list)
    github_action: str | None = None
    github_action_secrets: dict[str, str] = Field(default_factory=dict)


class AuthStatus(BaseModel):
    """Result of checking a provider's auth readiness."""

    provider: str
    all_ready: bool
    missing_secrets: list[str] = Field(default_factory=list)


def _cloudflare_provider() -> ProviderSpec:
    return ProviderSpec(
        name="cloudflare",
        display_name="Cloudflare Workers",
        required_secrets=[
            SecretSpec(
                name="CLOUDFLARE_API_TOKEN",
                description="Cloudflare API token with Workers edit permission",
                setup_url="https://dash.cloudflare.com/profile/api-tokens",
            ),
            SecretSpec(
                name="CLOUDFLARE_ACCOUNT_ID",
                description="Cloudflare account ID (Workers & Pages dashboard)",
                setup_url="https://dash.cloudflare.com",
            ),
        ],
        cli_tool="wrangler",
        deploy_command="wrangler deploy",
        detect_files=["wrangler.toml", "wrangler.jsonc"],
        supported_languages=["javascript", "typescript"],
        github_action="cloudflare/wrangler-action@v3",
        github_action_secrets={
            "apiToken": "CLOUDFLARE_API_TOKEN",
            "accountId": "CLOUDFLARE_ACCOUNT_ID",
        },
    )


def _vercel_provider() -> ProviderSpec:
    return ProviderSpec(
        name="vercel",
        display_name="Vercel",
        required_secrets=[
            SecretSpec(
                name="VERCEL_TOKEN",
                description="Vercel personal access token",
                setup_url="https://vercel.com/account/tokens",
            ),
            SecretSpec(
                name="VERCEL_ORG_ID",
                description="Vercel organization/team ID",
                setup_url="https://vercel.com/account",
                required=False,
            ),
            SecretSpec(
                name="VERCEL_PROJECT_ID",
                description="Vercel project ID (from .vercel/project.json)",
                setup_url="https://vercel.com",
                required=False,
            ),
        ],
        cli_tool="vercel",
        deploy_command="vercel deploy --prod --yes",
        detect_files=["vercel.json"],
        supported_languages=["javascript", "typescript", "python"],
        github_action="amondnet/vercel-action@v25",
        github_action_secrets={"vercel-token": "VERCEL_TOKEN"},
    )


def _fly_provider() -> ProviderSpec:
    return ProviderSpec(
        name="fly",
        display_name="Fly.io",
        required_secrets=[
            SecretSpec(
                name="FLY_API_TOKEN",
                description="Fly.io API token",
                setup_url="https://fly.io/user/personal_access_tokens",
            ),
        ],
        cli_tool="fly",
        deploy_command="fly deploy",
        detect_files=["fly.toml"],
        supported_languages=["javascript", "typescript", "python", "go", "rust"],
    )


def _netlify_provider() -> ProviderSpec:
    return ProviderSpec(
        name="netlify",
        display_name="Netlify",
        required_secrets=[
            SecretSpec(
                name="NETLIFY_AUTH_TOKEN",
                description="Netlify personal access token",
                setup_url="https://app.netlify.com/user/applications#personal-access-tokens",
            ),
            SecretSpec(
                name="NETLIFY_SITE_ID",
                description="Netlify site ID",
                setup_url="https://app.netlify.com",
                required=False,
            ),
        ],
        cli_tool="netlify",
        deploy_command="netlify deploy --prod",
        detect_files=["netlify.toml"],
        supported_languages=["javascript", "typescript"],
    )


def _docker_registry_provider() -> ProviderSpec:
    return ProviderSpec(
        name="docker-registry",
        display_name="Docker Registry",
        required_secrets=[
            SecretSpec(
                name="DOCKER_USERNAME",
                description="Docker Hub / registry username",
                setup_url="https://hub.docker.com/settings/security",
            ),
            SecretSpec(
                name="DOCKER_PASSWORD",
                description="Docker Hub / registry access token",
                setup_url="https://hub.docker.com/settings/security",
            ),
        ],
        cli_tool="docker",
        deploy_command="docker push",
        detect_files=["Dockerfile"],
        supported_languages=["javascript", "typescript", "python", "go", "rust"],
    )


class ProviderRegistry:
    """Registry of known deploy providers."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderSpec] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        for factory in (
            _cloudflare_provider,
            _vercel_provider,
            _fly_provider,
            _netlify_provider,
            _docker_registry_provider,
        ):
            spec = factory()
            self._providers[spec.name] = spec

    def list_providers(self) -> list[ProviderSpec]:
        return list(self._providers.values())

    def get(self, name: str) -> ProviderSpec:
        if name not in self._providers:
            raise KeyError(f"Unknown provider: {name!r}")
        return self._providers[name]

    def detect(self, context: RepoContext) -> list[ProviderSpec]:
        """Return providers whose detect_files match the repo context."""
        matched: list[ProviderSpec] = []

        config_checks = {
            "wrangler.toml": context.has_wrangler_config,
            "wrangler.jsonc": context.has_wrangler_config,
            "vercel.json": context.has_vercel_config,
            "fly.toml": context.has_fly_config,
            "netlify.toml": context.has_netlify_config,
            "Dockerfile": context.has_dockerfile,
        }

        for provider in self._providers.values():
            for detect_file in provider.detect_files:
                if config_checks.get(detect_file, False):
                    matched.append(provider)
                    break  # Don't double-add the same provider

        return matched

    def check_cli(self, provider: ProviderSpec) -> bool:
        """Check if the provider's CLI tool is installed."""
        return shutil.which(provider.cli_tool) is not None

    def check_auth(self, provider: ProviderSpec) -> AuthStatus:
        """Check which required secrets are available in the environment."""
        import os

        missing: list[str] = []
        for secret in provider.required_secrets:
            if secret.required and not os.environ.get(secret.name):
                missing.append(secret.name)

        return AuthStatus(
            provider=provider.name,
            all_ready=len(missing) == 0,
            missing_secrets=missing,
        )
