"""Smoke tests — deploy + destroy against real providers.

Skipped by default (not in testpaths). Run explicitly:
    pytest tests/smoke/ -m slow -v
"""

from __future__ import annotations

import pytest

from dockcheck.tools.deploy import CloudflareProvider

from .conftest import require_env


@pytest.mark.slow
class TestCloudflareSmoke:
    """Full deploy + destroy against Cloudflare Workers (free tier)."""

    def test_deploy_and_destroy(self, copy_example):
        env = require_env("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")
        provider = CloudflareProvider()

        if not provider.is_available():
            pytest.skip("wrangler CLI not installed")

        workdir = str(copy_example("cf-worker-hello"))
        result = provider.deploy(workdir=workdir, env=env)
        try:
            assert result.success is True, f"Deploy failed: {result.error}"
            assert result.url is not None
        finally:
            destroy = provider.destroy(workdir=workdir, env=env)
            assert destroy.success is True, f"Destroy failed: {destroy.error}"


@pytest.mark.slow
class TestVercelSmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("Vercel smoke test: needs project setup")


@pytest.mark.slow
class TestFlySmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("Fly smoke test: needs app created")


@pytest.mark.slow
class TestNetlifySmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("Netlify smoke test: needs site linked")


@pytest.mark.slow
class TestDockerRegistrySmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("Docker registry smoke test: needs registry")


@pytest.mark.slow
class TestAwsLambdaSmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("AWS Lambda smoke test: costs money")


@pytest.mark.slow
class TestGcpCloudRunSmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("GCP Cloud Run smoke test: costs money")


@pytest.mark.slow
class TestRailwaySmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("Railway smoke test: credit-based")


@pytest.mark.slow
class TestRenderSmoke:
    def test_deploy_and_destroy(self, copy_example):
        pytest.skip("Render smoke test: no programmatic delete")
