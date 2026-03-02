"""Tests for deploy providers — mock subprocess for wrangler/vercel, URL extraction."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from dockcheck.tools.deploy import (
    AwsLambdaProvider,
    CloudflareProvider,
    DeployProviderFactory,
    DeployResult,
    DockerRegistryProvider,
    FlyProvider,
    GcpCloudRunProvider,
    NetlifyProvider,
    RailwayProvider,
    RenderProvider,
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

    def test_destroy_success(self):
        provider = CloudflareProvider()
        mock_result = subprocess.CompletedProcess(
            args=["wrangler", "delete"], returncode=0,
            stdout="Successfully deleted", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = provider.destroy(workdir="/tmp/test")
        assert result.success is True
        assert result.provider == "cloudflare"
        assert result.error is None

    def test_destroy_cli_not_found(self):
        provider = CloudflareProvider()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = provider.destroy()
        assert result.success is False
        assert "not found" in result.error

    def test_destroy_timeout(self):
        provider = CloudflareProvider()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("wrangler", 120)):
            result = provider.destroy()
        assert result.success is False
        assert "timed out" in result.error


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

    def test_destroy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Removed", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = VercelProvider().destroy()
        assert result.success is True

    def test_destroy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = VercelProvider().destroy()
        assert result.success is False
        assert "not found" in result.error


class TestFlyProvider:
    def test_name(self):
        assert FlyProvider().name == "fly"

    def test_is_available_when_installed(self):
        with patch("shutil.which", return_value="/usr/local/bin/fly"):
            assert FlyProvider().is_available() is True

    def test_is_not_available(self):
        with patch("shutil.which", return_value=None):
            assert FlyProvider().is_available() is False

    def test_deploy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=["fly", "deploy"], returncode=0,
            stdout="Deployed app https://my-app.fly.dev\n", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = FlyProvider().deploy()
        assert result.success is True
        assert result.url == "https://my-app.fly.dev"

    def test_deploy_failure(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: not authenticated",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = FlyProvider().deploy()
        assert result.success is False

    def test_deploy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = FlyProvider().deploy()
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("fly", 300)):
            result = FlyProvider().deploy()
        assert result.success is False
        assert "timed out" in result.error

    def test_extract_url(self):
        assert FlyProvider._extract_url("https://my-app.fly.dev") == "https://my-app.fly.dev"
        assert FlyProvider._extract_url("no url here") is None

    def test_destroy_success(self, tmp_path):
        (tmp_path / "fly.toml").write_text('app = "my-app"\n')
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Destroyed app my-app", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = FlyProvider().destroy(workdir=str(tmp_path))
        assert result.success is True
        # Verify app name was passed to the command
        cmd = mock_run.call_args[0][0]
        assert "my-app" in cmd

    def test_destroy_cli_not_found(self, tmp_path):
        (tmp_path / "fly.toml").write_text('app = "my-app"\n')
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = FlyProvider().destroy(workdir=str(tmp_path))
        assert result.success is False
        assert "not found" in result.error

    def test_destroy_no_fly_toml(self, tmp_path):
        result = FlyProvider().destroy(workdir=str(tmp_path))
        assert result.success is False
        assert "app name" in result.error.lower()


class TestNetlifyProvider:
    def test_name(self):
        assert NetlifyProvider().name == "netlify"

    def test_is_available_when_installed(self):
        with patch("shutil.which", return_value="/usr/local/bin/netlify"):
            assert NetlifyProvider().is_available() is True

    def test_is_not_available(self):
        with patch("shutil.which", return_value=None):
            assert NetlifyProvider().is_available() is False

    def test_deploy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Website URL: https://my-site.netlify.app\n", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = NetlifyProvider().deploy()
        assert result.success is True
        assert result.url == "https://my-site.netlify.app"

    def test_deploy_failure(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: auth",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = NetlifyProvider().deploy()
        assert result.success is False

    def test_deploy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = NetlifyProvider().deploy()
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("netlify", 180)):
            result = NetlifyProvider().deploy()
        assert result.success is False
        assert "timed out" in result.error

    def test_extract_url(self):
        assert NetlifyProvider._extract_url("https://my-site.netlify.app") == "https://my-site.netlify.app"
        assert NetlifyProvider._extract_url("error") is None

    def test_destroy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Site deleted", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = NetlifyProvider().destroy()
        assert result.success is True

    def test_destroy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = NetlifyProvider().destroy()
        assert result.success is False
        assert "not found" in result.error


class TestDockerRegistryProvider:
    def test_name(self):
        assert DockerRegistryProvider().name == "docker-registry"

    def test_is_available_when_installed(self):
        with patch("shutil.which", return_value="/usr/local/bin/docker"):
            assert DockerRegistryProvider().is_available() is True

    def test_is_not_available(self):
        with patch("shutil.which", return_value=None):
            assert DockerRegistryProvider().is_available() is False

    def test_deploy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Successfully pushed", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = DockerRegistryProvider().deploy(
                env={"DOCKER_IMAGE": "myuser/myapp:latest"},
            )
        assert result.success is True
        assert result.url is None  # Docker push doesn't produce a URL

    def test_deploy_build_failure(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="build error",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = DockerRegistryProvider().deploy(
                env={"DOCKER_IMAGE": "myuser/myapp:latest"},
            )
        assert result.success is False

    def test_deploy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = DockerRegistryProvider().deploy(
                env={"DOCKER_IMAGE": "myuser/myapp:latest"},
            )
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_fallback_image_tag(self):
        """Falls back to username/dirname:latest when DOCKER_IMAGE not set."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = DockerRegistryProvider().deploy(
                workdir="/tmp/myapp",
                env={"DOCKER_USERNAME": "testuser"},
            )
        assert result.success is True
        # Build and push should both be called
        assert mock_run.call_count == 2

    def test_deploy_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 300)):
            result = DockerRegistryProvider().deploy(
                env={"DOCKER_IMAGE": "myuser/myapp:latest"},
            )
        assert result.success is False
        assert "timed out" in result.error

    def test_destroy_noop(self):
        result = DockerRegistryProvider().destroy()
        assert result.success is True
        assert "no-op" in result.stdout.lower()


class TestAwsLambdaProvider:
    def test_name(self):
        assert AwsLambdaProvider().name == "aws-lambda"

    def test_is_available_when_installed(self):
        with patch("shutil.which", return_value="/usr/local/bin/sam"):
            assert AwsLambdaProvider().is_available() is True

    def test_is_not_available(self):
        with patch("shutil.which", return_value=None):
            assert AwsLambdaProvider().is_available() is False

    def test_deploy_success(self):
        build_ok = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Build Succeeded", stderr="",
        )
        deploy_ok = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Outputs:\nApiUrl: https://abc123.execute-api.us-east-1.amazonaws.com/Prod\n",
            stderr="",
        )
        with patch("subprocess.run", side_effect=[build_ok, deploy_ok]):
            result = AwsLambdaProvider().deploy()
        assert result.success is True
        assert "execute-api" in result.url

    def test_deploy_build_failure(self):
        build_fail = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: build failed",
        )
        with patch("subprocess.run", return_value=build_fail):
            result = AwsLambdaProvider().deploy()
        assert result.success is False
        assert "build failed" in result.error

    def test_deploy_failure(self):
        build_ok = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Build Succeeded", stderr="",
        )
        deploy_fail = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: no credentials",
        )
        with patch("subprocess.run", side_effect=[build_ok, deploy_fail]):
            result = AwsLambdaProvider().deploy()
        assert result.success is False

    def test_deploy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = AwsLambdaProvider().deploy()
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sam", 300)):
            result = AwsLambdaProvider().deploy()
        assert result.success is False
        assert "timed out" in result.error

    def test_extract_url(self):
        stdout = "https://abc.execute-api.us-east-1.amazonaws.com/Prod"
        assert AwsLambdaProvider._extract_url(stdout) is not None
        assert AwsLambdaProvider._extract_url("no url") is None

    def test_destroy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Deleted stack", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = AwsLambdaProvider().destroy()
        assert result.success is True

    def test_destroy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = AwsLambdaProvider().destroy()
        assert result.success is False
        assert "not found" in result.error


class TestGcpCloudRunProvider:
    def test_name(self):
        assert GcpCloudRunProvider().name == "gcp-cloudrun"

    def test_is_available_when_installed(self):
        with patch("shutil.which", return_value="/usr/local/bin/gcloud"):
            assert GcpCloudRunProvider().is_available() is True

    def test_is_not_available(self):
        with patch("shutil.which", return_value=None):
            assert GcpCloudRunProvider().is_available() is False

    def test_deploy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Service URL: https://my-service-abc123.run.app\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = GcpCloudRunProvider().deploy(
                env={"GCP_PROJECT_ID": "my-project"},
            )
        assert result.success is True
        assert "run.app" in result.url

    def test_deploy_url_from_stderr(self):
        """Cloud Run sometimes outputs URL to stderr."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Deploying...",
            stderr="Service URL: https://my-service-abc123.run.app\n",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = GcpCloudRunProvider().deploy()
        assert result.success is True
        assert "run.app" in result.url

    def test_deploy_failure(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ERROR: not authenticated",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = GcpCloudRunProvider().deploy()
        assert result.success is False

    def test_deploy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = GcpCloudRunProvider().deploy()
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gcloud", 600)):
            result = GcpCloudRunProvider().deploy()
        assert result.success is False
        assert "timed out" in result.error

    def test_extract_url(self):
        assert GcpCloudRunProvider._extract_url("https://svc-abc.run.app") is not None
        assert GcpCloudRunProvider._extract_url("no url") is None

    def test_destroy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Deleted service", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = GcpCloudRunProvider().destroy(
                env={"GCP_PROJECT_ID": "my-project"},
            )
        assert result.success is True

    def test_destroy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = GcpCloudRunProvider().destroy()
        assert result.success is False
        assert "not found" in result.error


class TestRailwayProvider:
    def test_name(self):
        assert RailwayProvider().name == "railway"

    def test_is_available_when_installed(self):
        with patch("shutil.which", return_value="/usr/local/bin/railway"):
            assert RailwayProvider().is_available() is True

    def test_is_not_available(self):
        with patch("shutil.which", return_value=None):
            assert RailwayProvider().is_available() is False

    def test_deploy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Deployed to https://my-app.up.railway.app\n", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = RailwayProvider().deploy()
        assert result.success is True
        assert "railway.app" in result.url

    def test_deploy_failure(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: no project",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = RailwayProvider().deploy()
        assert result.success is False

    def test_deploy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = RailwayProvider().deploy()
        assert result.success is False
        assert "not found" in result.error

    def test_deploy_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("railway", 300)):
            result = RailwayProvider().deploy()
        assert result.success is False
        assert "timed out" in result.error

    def test_extract_url(self):
        assert RailwayProvider._extract_url("https://my-app.up.railway.app") is not None
        assert RailwayProvider._extract_url("error") is None

    def test_destroy_success(self):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Service stopped", stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            result = RailwayProvider().destroy()
        assert result.success is True

    def test_destroy_cli_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = RailwayProvider().destroy()
        assert result.success is False
        assert "not found" in result.error


class TestRenderProvider:
    def test_name(self):
        assert RenderProvider().name == "render"

    def test_is_available_with_hook_url(self):
        with patch("os.environ.get", side_effect=lambda k, d=None: {
            "RENDER_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-xxx",
        }.get(k, d)):
            assert RenderProvider().is_available() is True

    def test_is_not_available_without_hook(self):
        with patch("os.environ.get", return_value=None):
            assert RenderProvider().is_available() is False

    def test_deploy_success(self):
        import httpx

        mock_response = httpx.Response(200, json={"ok": True})
        with patch("httpx.post", return_value=mock_response):
            result = RenderProvider().deploy(
                env={"RENDER_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-xxx"},
            )
        assert result.success is True

    def test_deploy_no_hook_url(self):
        result = RenderProvider().deploy()
        assert result.success is False
        assert "RENDER_DEPLOY_HOOK_URL" in result.error

    def test_deploy_http_error(self):
        import httpx

        mock_response = httpx.Response(500, text="Internal Server Error")
        with patch("httpx.post", return_value=mock_response):
            result = RenderProvider().deploy(
                env={"RENDER_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-xxx"},
            )
        assert result.success is False
        assert "500" in result.error

    def test_deploy_network_error(self):
        import httpx

        with patch("httpx.post", side_effect=httpx.HTTPError("connection failed")):
            result = RenderProvider().deploy(
                env={"RENDER_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-xxx"},
            )
        assert result.success is False

    def test_destroy_returns_error(self):
        result = RenderProvider().destroy()
        assert result.success is False
        assert "dashboard" in result.error.lower()


class TestDeployProviderFactory:
    def test_get_cloudflare(self):
        provider = DeployProviderFactory.get("cloudflare")
        assert isinstance(provider, CloudflareProvider)

    def test_get_vercel(self):
        provider = DeployProviderFactory.get("vercel")
        assert isinstance(provider, VercelProvider)

    def test_get_fly(self):
        provider = DeployProviderFactory.get("fly")
        assert isinstance(provider, FlyProvider)

    def test_get_netlify(self):
        provider = DeployProviderFactory.get("netlify")
        assert isinstance(provider, NetlifyProvider)

    def test_get_docker_registry(self):
        provider = DeployProviderFactory.get("docker-registry")
        assert isinstance(provider, DockerRegistryProvider)

    def test_get_aws_lambda(self):
        provider = DeployProviderFactory.get("aws-lambda")
        assert isinstance(provider, AwsLambdaProvider)

    def test_get_gcp_cloudrun(self):
        provider = DeployProviderFactory.get("gcp-cloudrun")
        assert isinstance(provider, GcpCloudRunProvider)

    def test_get_railway(self):
        provider = DeployProviderFactory.get("railway")
        assert isinstance(provider, RailwayProvider)

    def test_get_render(self):
        provider = DeployProviderFactory.get("render")
        assert isinstance(provider, RenderProvider)

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown deploy provider"):
            DeployProviderFactory.get("heroku")

    def test_available_providers(self):
        available = DeployProviderFactory.available()
        assert "cloudflare" in available
        assert "vercel" in available
        assert "fly" in available
        assert "netlify" in available
        assert "docker-registry" in available
        assert "aws-lambda" in available
        assert "gcp-cloudrun" in available
        assert "railway" in available
        assert "render" in available


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
