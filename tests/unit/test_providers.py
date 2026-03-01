"""Tests for provider registry — listing, detection, CLI/auth checks."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dockcheck.init.detect import RepoContext
from dockcheck.init.providers import (
    ProviderRegistry,
)


class TestProviderRegistry:
    def test_list_providers(self):
        registry = ProviderRegistry()
        providers = registry.list_providers()
        assert len(providers) >= 9
        names = {p.name for p in providers}
        assert "cloudflare" in names
        assert "vercel" in names
        assert "fly" in names
        assert "netlify" in names
        assert "docker-registry" in names
        assert "aws-lambda" in names
        assert "gcp-cloudrun" in names
        assert "railway" in names
        assert "render" in names

    def test_get_cloudflare(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        assert cf.display_name == "Cloudflare Workers"
        assert cf.cli_tool == "wrangler"
        assert cf.deploy_command == "wrangler deploy"
        assert cf.github_action == "cloudflare/wrangler-action@v3"

    def test_get_vercel(self):
        registry = ProviderRegistry()
        vercel = registry.get("vercel")
        assert vercel.display_name == "Vercel"
        assert vercel.cli_tool == "vercel"

    def test_get_fly(self):
        registry = ProviderRegistry()
        fly = registry.get("fly")
        assert fly.display_name == "Fly.io"

    def test_get_netlify(self):
        registry = ProviderRegistry()
        netlify = registry.get("netlify")
        assert netlify.display_name == "Netlify"

    def test_get_docker_registry(self):
        registry = ProviderRegistry()
        docker = registry.get("docker-registry")
        assert docker.display_name == "Docker Registry"

    def test_get_aws_lambda(self):
        registry = ProviderRegistry()
        aws = registry.get("aws-lambda")
        assert aws.display_name == "AWS Lambda (SAM)"
        assert aws.cli_tool == "sam"

    def test_get_gcp_cloudrun(self):
        registry = ProviderRegistry()
        gcp = registry.get("gcp-cloudrun")
        assert gcp.display_name == "GCP Cloud Run"
        assert gcp.cli_tool == "gcloud"

    def test_get_railway(self):
        registry = ProviderRegistry()
        rw = registry.get("railway")
        assert rw.display_name == "Railway"
        assert rw.cli_tool == "railway"

    def test_get_render(self):
        registry = ProviderRegistry()
        rn = registry.get("render")
        assert rn.display_name == "Render"

    def test_get_unknown_raises(self):
        registry = ProviderRegistry()
        with pytest.raises(KeyError, match="Unknown provider"):
            registry.get("nonexistent")


class TestProviderSecrets:
    def test_cloudflare_requires_api_token(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        names = [s.name for s in cf.required_secrets]
        assert "CLOUDFLARE_API_TOKEN" in names
        assert "CLOUDFLARE_ACCOUNT_ID" in names

    def test_vercel_requires_token(self):
        registry = ProviderRegistry()
        vercel = registry.get("vercel")
        names = [s.name for s in vercel.required_secrets]
        assert "VERCEL_TOKEN" in names

    def test_secret_spec_has_setup_url(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        for secret in cf.required_secrets:
            assert secret.setup_url.startswith("https://")

    def test_docker_registry_secrets(self):
        registry = ProviderRegistry()
        docker = registry.get("docker-registry")
        names = [s.name for s in docker.required_secrets]
        assert "DOCKER_USERNAME" in names
        assert "DOCKER_PASSWORD" in names


    def test_aws_lambda_secrets(self):
        registry = ProviderRegistry()
        aws = registry.get("aws-lambda")
        names = [s.name for s in aws.required_secrets]
        assert "AWS_ACCESS_KEY_ID" in names
        assert "AWS_SECRET_ACCESS_KEY" in names

    def test_gcp_cloudrun_secrets(self):
        registry = ProviderRegistry()
        gcp = registry.get("gcp-cloudrun")
        names = [s.name for s in gcp.required_secrets]
        assert "GCP_PROJECT_ID" in names
        assert "GCP_SERVICE_ACCOUNT_KEY" in names

    def test_railway_secrets(self):
        registry = ProviderRegistry()
        rw = registry.get("railway")
        names = [s.name for s in rw.required_secrets]
        assert "RAILWAY_TOKEN" in names

    def test_render_secrets(self):
        registry = ProviderRegistry()
        rn = registry.get("render")
        names = [s.name for s in rn.required_secrets]
        assert "RENDER_DEPLOY_HOOK_URL" in names


class TestProviderDetection:
    def test_detect_cloudflare_from_wrangler(self):
        ctx = RepoContext(has_wrangler_config=True, language="javascript")
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "cloudflare" in names

    def test_detect_vercel_from_config(self):
        ctx = RepoContext(has_vercel_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "vercel" in names

    def test_detect_fly_from_config(self):
        ctx = RepoContext(has_fly_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "fly" in names

    def test_detect_netlify_from_config(self):
        ctx = RepoContext(has_netlify_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "netlify" in names

    def test_detect_docker_from_dockerfile(self):
        ctx = RepoContext(has_dockerfile=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "docker-registry" in names

    def test_detect_aws_lambda_from_sam_config(self):
        ctx = RepoContext(has_sam_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "aws-lambda" in names

    def test_detect_gcp_cloudrun_from_config(self):
        ctx = RepoContext(has_cloudrun_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "gcp-cloudrun" in names

    def test_detect_railway_from_config(self):
        ctx = RepoContext(has_railway_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "railway" in names

    def test_detect_render_from_config(self):
        ctx = RepoContext(has_render_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        names = [p.name for p in detected]
        assert "render" in names

    def test_detect_nothing_for_empty_repo(self):
        ctx = RepoContext()
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        assert detected == []

    def test_detect_multiple_providers(self):
        ctx = RepoContext(has_wrangler_config=True, has_dockerfile=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        assert len(detected) >= 2

    def test_no_duplicate_detection(self):
        """Providers should not be detected twice even if multiple files match."""
        ctx = RepoContext(has_wrangler_config=True)
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        cf_count = sum(1 for p in detected if p.name == "cloudflare")
        assert cf_count == 1


class TestCLICheck:
    def test_check_cli_found(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        with patch("shutil.which", return_value="/usr/local/bin/wrangler"):
            assert registry.check_cli(cf) is True

    def test_check_cli_not_found(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        with patch("shutil.which", return_value=None):
            assert registry.check_cli(cf) is False


class TestAuthCheck:
    def test_auth_all_ready(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token123")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc123")
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        status = registry.check_auth(cf)
        assert status.all_ready is True
        assert status.missing_secrets == []

    def test_auth_missing_secrets(self, monkeypatch):
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        status = registry.check_auth(cf)
        assert status.all_ready is False
        assert "CLOUDFLARE_API_TOKEN" in status.missing_secrets

    def test_auth_partial(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token123")
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        status = registry.check_auth(cf)
        assert status.all_ready is False
        assert "CLOUDFLARE_ACCOUNT_ID" in status.missing_secrets
        assert "CLOUDFLARE_API_TOKEN" not in status.missing_secrets

    def test_auth_optional_not_required(self, monkeypatch):
        """Optional secrets don't block all_ready."""
        monkeypatch.setenv("VERCEL_TOKEN", "token")
        monkeypatch.delenv("VERCEL_ORG_ID", raising=False)
        monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)
        registry = ProviderRegistry()
        vercel = registry.get("vercel")
        status = registry.check_auth(vercel)
        # VERCEL_ORG_ID and VERCEL_PROJECT_ID are optional (required=False)
        assert status.all_ready is True


class TestGitHubAction:
    def test_cloudflare_github_action(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        assert cf.github_action == "cloudflare/wrangler-action@v3"
        assert "apiToken" in cf.github_action_secrets
        assert cf.github_action_secrets["apiToken"] == "CLOUDFLARE_API_TOKEN"

    def test_vercel_github_action(self):
        registry = ProviderRegistry()
        vercel = registry.get("vercel")
        assert vercel.github_action is not None

    def test_fly_github_action(self):
        registry = ProviderRegistry()
        fly = registry.get("fly")
        assert fly.github_action == "superfly/flyctl-actions/setup-flyctl@master"
        assert "FLY_API_TOKEN" in fly.github_action_secrets

    def test_netlify_github_action(self):
        registry = ProviderRegistry()
        netlify = registry.get("netlify")
        assert netlify.github_action == "nwtgck/actions-netlify@v3"

    def test_docker_registry_github_action(self):
        registry = ProviderRegistry()
        docker = registry.get("docker-registry")
        assert docker.github_action == "docker/build-push-action@v5"

    def test_aws_lambda_github_action(self):
        registry = ProviderRegistry()
        aws = registry.get("aws-lambda")
        assert aws.github_action == "aws-actions/configure-aws-credentials@v4"

    def test_gcp_cloudrun_github_action(self):
        registry = ProviderRegistry()
        gcp = registry.get("gcp-cloudrun")
        assert gcp.github_action == "google-github-actions/auth@v2"


class TestProviderSpecModel:
    def test_detect_files_list(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        assert "wrangler.toml" in cf.detect_files
        assert "wrangler.jsonc" in cf.detect_files

    def test_supported_languages(self):
        registry = ProviderRegistry()
        cf = registry.get("cloudflare")
        assert "javascript" in cf.supported_languages
        assert "typescript" in cf.supported_languages


class TestInstallHint:
    """Every provider has a non-empty install_hint."""

    @pytest.mark.parametrize(
        "provider_name,expected_substring",
        [
            ("cloudflare", "npm install -g wrangler"),
            ("vercel", "npm install -g vercel"),
            ("fly", "curl -L https://fly.io/install.sh"),
            ("netlify", "npm install -g netlify-cli"),
            ("docker-registry", "https://docs.docker.com/get-docker/"),
            ("aws-lambda", "pip install aws-sam-cli"),
            ("gcp-cloudrun", "https://cloud.google.com/sdk/docs/install"),
            ("railway", "npm install -g @railway/cli"),
            ("render", "RENDER_DEPLOY_HOOK_URL"),
        ],
    )
    def test_install_hint_per_provider(self, provider_name, expected_substring):
        registry = ProviderRegistry()
        provider = registry.get(provider_name)
        assert provider.install_hint, f"{provider_name} has no install_hint"
        assert expected_substring in provider.install_hint
