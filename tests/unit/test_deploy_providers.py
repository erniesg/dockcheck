"""Tests for deploy providers — mock subprocess for wrangler/vercel, URL extraction."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from dockcheck.tools.deploy import (
    CloudflareProvider,
    DeployProviderFactory,
    DeployResult,
    VercelProvider,
)


class TestCloudflareProvider:
    def test_name(self):
        provider = CloudflareProvider()
        assert provider.name == "cloudflare"

    def test_is_available_when_installed(self):
        provider = CloudflareProvider()
        with patch("shutil.which", return_value="/usr/local/bin/wrangler"):
            assert provider.is_available() is True

    def test_is_not_available(self):
        provider = CloudflareProvider()
        with patch("shutil.which", return_value=None):
            assert provider.is_available() is False

    def test_deploy_success(self):
        provider = CloudflareProvider()
        mock_result = subprocess.CompletedProcess(
            args=["wrangler", "deploy"],
            returncode=0,
            stdout="Deployed https://hello-world.workers.dev\nCurrent Version ID: abc",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = provider.deploy(workdir="/tmp/test")
        assert result.success is True
        assert result.provider == "cloudflare"
        assert result.url == "https://hello-world.workers.dev"
        assert result.error is None

    def test_deploy_failure(self):
        provider = CloudflareProvider()
        mock_result = subprocess.CompletedProcess(
            args=["wrangler", "deploy"],
            returncode=1,
            stdout="",
            stderr="Error: Authentication failed",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = provider.deploy()
        assert result.success is False
        assert result.error == "Error: Authentication failed"

    def test_deploy_cli_not_found(self):
        provider = CloudflareProvider()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = provider.deploy()
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_timeout(self):
        provider = CloudflareProvider()
        timeout_err = subprocess.TimeoutExpired("wrangler", 120)
        with patch("subprocess.run", side_effect=timeout_err):
            result = provider.deploy()
        assert result.success is False
        assert "timed out" in result.error

    def test_extract_url_standard(self):
        stdout = "Published hello-world (3.14 sec)\nhttps://hello-world.workers.dev"
        url = CloudflareProvider._extract_url(stdout)
        assert url == "https://hello-world.workers.dev"

    def test_extract_url_with_subdomain(self):
        stdout = "Deployed https://api.hello-world.workers.dev"
        url = CloudflareProvider._extract_url(stdout)
        assert url == "https://api.hello-world.workers.dev"

    def test_extract_url_no_match(self):
        stdout = "Error: something went wrong"
        url = CloudflareProvider._extract_url(stdout)
        assert url is None

    def test_deploy_with_env(self):
        provider = CloudflareProvider()
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://x.workers.dev", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.deploy(env={"CLOUDFLARE_API_TOKEN": "test"})
        # Verify env was passed
        call_kwargs = mock_run.call_args
        assert "CLOUDFLARE_API_TOKEN" in call_kwargs.kwargs["env"]


class TestVercelProvider:
    def test_name(self):
        provider = VercelProvider()
        assert provider.name == "vercel"

    def test_is_available_when_installed(self):
        provider = VercelProvider()
        with patch("shutil.which", return_value="/usr/local/bin/vercel"):
            assert provider.is_available() is True

    def test_is_not_available(self):
        provider = VercelProvider()
        with patch("shutil.which", return_value=None):
            assert provider.is_available() is False

    def test_deploy_success(self):
        provider = VercelProvider()
        mock_result = subprocess.CompletedProcess(
            args=["vercel", "deploy", "--prod", "--yes"],
            returncode=0,
            stdout="Production: https://my-app.vercel.app [1s]",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = provider.deploy()
        assert result.success is True
        assert result.provider == "vercel"
        assert result.url == "https://my-app.vercel.app"

    def test_deploy_failure(self):
        provider = VercelProvider()
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: auth failed"
        )
        with patch("subprocess.run", return_value=mock_result):
            result = provider.deploy()
        assert result.success is False
        assert result.error is not None

    def test_deploy_cli_not_found(self):
        provider = VercelProvider()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = provider.deploy()
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_timeout(self):
        provider = VercelProvider()
        timeout_err = subprocess.TimeoutExpired("vercel", 120)
        with patch("subprocess.run", side_effect=timeout_err):
            result = provider.deploy()
        assert result.success is False
        assert "timed out" in result.error

    def test_extract_url_standard(self):
        stdout = "Production: https://my-app.vercel.app [1s]"
        url = VercelProvider._extract_url(stdout)
        assert url == "https://my-app.vercel.app"

    def test_extract_url_no_match(self):
        url = VercelProvider._extract_url("Error occurred")
        assert url is None


class TestDeployProviderFactory:
    def test_get_cloudflare(self):
        provider = DeployProviderFactory.get("cloudflare")
        assert isinstance(provider, CloudflareProvider)

    def test_get_vercel(self):
        provider = DeployProviderFactory.get("vercel")
        assert isinstance(provider, VercelProvider)

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown deploy provider"):
            DeployProviderFactory.get("aws-lambda")

    def test_available_providers(self):
        available = DeployProviderFactory.available()
        assert "cloudflare" in available
        assert "vercel" in available


class TestDeployResult:
    def test_success_result(self):
        result = DeployResult(
            success=True,
            provider="cloudflare",
            url="https://hello.workers.dev",
            stdout="ok",
        )
        assert result.success is True
        assert result.url == "https://hello.workers.dev"

    def test_failure_result(self):
        result = DeployResult(
            success=False,
            provider="vercel",
            error="Auth failed",
        )
        assert result.success is False
        assert result.error == "Auth failed"
        assert result.url is None
