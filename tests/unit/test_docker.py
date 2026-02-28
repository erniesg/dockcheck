"""Tests for DockerTool — build, run, push with security flags and policy gating."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dockcheck.core.policy import Policy, PolicyEngine
from dockcheck.tools.docker import BuildResult, DockerTool, PushResult, RunResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(client: MagicMock | None = None) -> tuple[DockerTool, MagicMock]:
    """Return (DockerTool, mock_client) with a pre-configured mock docker client."""
    mock_client = client or MagicMock()
    tool = DockerTool(client=mock_client)
    return tool, mock_client


def _make_policy_engine(commands: list[str] | None = None) -> PolicyEngine:
    """Return a PolicyEngine with optional blocked command patterns."""
    blocked = [{"pattern": cmd} for cmd in (commands or [])]
    policy = Policy.from_dict(
        {
            "version": "1",
            "hard_stops": {"commands": blocked, "critical_paths": []},
            "confidence_thresholds": {
                "auto_deploy_staging": 0.8,
                "auto_promote_prod": 0.9,
                "notify_human": 0.6,
            },
        }
    )
    return PolicyEngine(policy)


# ---------------------------------------------------------------------------
# DockerTool initialisation
# ---------------------------------------------------------------------------

class TestDockerToolInit:
    def test_accepts_injected_client(self):
        mock_client = MagicMock()
        tool = DockerTool(client=mock_client)
        assert tool.client is mock_client

    @patch("dockcheck.tools.docker.docker.from_env")
    def test_creates_default_client(self, mock_from_env):
        mock_from_env.return_value = MagicMock()
        tool = DockerTool()
        mock_from_env.assert_called_once()
        assert tool.client is not None

    @patch("dockcheck.tools.docker.docker.from_env", side_effect=Exception("daemon not running"))
    def test_graceful_on_missing_daemon(self, _mock):
        # Should not raise — client will be None but object is created
        tool = DockerTool()
        assert tool.client is None


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

class TestDockerBuild:
    def test_build_success(self):
        tool, mock_client = _make_tool()
        mock_image = MagicMock()
        mock_client.images.build.return_value = (
            mock_image,
            [{"stream": "Step 1/3\n"}, {"stream": "Successfully built abc123\n"}],
        )

        result = tool.build(path=".", tag="myapp:latest")

        assert result.success is True
        assert result.image_tag == "myapp:latest"
        assert len(result.logs) == 2
        assert result.error is None
        mock_client.images.build.assert_called_once_with(
            path=".", tag="myapp:latest", dockerfile="Dockerfile", rm=True
        )

    def test_build_default_args(self):
        tool, mock_client = _make_tool()
        mock_client.images.build.return_value = (MagicMock(), [])

        result = tool.build()

        assert result.image_tag == "dockcheck-build"
        call_kwargs = mock_client.images.build.call_args[1]
        assert call_kwargs["path"] == "."
        assert call_kwargs["dockerfile"] == "Dockerfile"

    def test_build_failure_returns_result(self):
        import docker.errors

        tool, mock_client = _make_tool()
        mock_client.images.build.side_effect = docker.errors.BuildError(
            reason="syntax error", build_log=[]
        )

        result = tool.build(path=".", tag="bad-image")

        assert result.success is False
        assert result.error is not None
        assert "syntax error" in result.error

    def test_build_api_error(self):
        import docker.errors

        tool, mock_client = _make_tool()
        mock_client.images.build.side_effect = docker.errors.APIError("server error")

        result = tool.build()

        assert result.success is False
        assert result.error is not None

    def test_build_unexpected_error(self):
        tool, mock_client = _make_tool()
        mock_client.images.build.side_effect = RuntimeError("unexpected")

        result = tool.build()

        assert result.success is False
        assert "unexpected" in result.error

    def test_build_result_is_pydantic_model(self):
        tool, mock_client = _make_tool()
        mock_client.images.build.return_value = (MagicMock(), [])

        result = tool.build()

        assert isinstance(result, BuildResult)

    def test_build_logs_captured_from_status_key(self):
        """Logs may use 'status' key instead of 'stream'."""
        tool, mock_client = _make_tool()
        mock_client.images.build.return_value = (
            MagicMock(),
            [{"status": "Pulling layer"}, {"stream": "Done"}],
        )

        result = tool.build()

        assert result.success is True
        assert "Pulling layer" in result.logs


# ---------------------------------------------------------------------------
# run — security flags
# ---------------------------------------------------------------------------

class TestDockerRun:
    def test_run_success(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b"Hello, world!"

        result = tool.run(image="alpine:latest", command="echo hello")

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "Hello, world!"

    def test_run_uses_network_disabled_by_default(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        tool.run(image="alpine:latest")

        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["network_disabled"] is True

    def test_run_uses_read_only_rootfs_by_default(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        tool.run(image="alpine:latest")

        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["read_only"] is True

    def test_run_uses_memory_limit_by_default(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        tool.run(image="alpine:latest")

        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["mem_limit"] == "512m"

    def test_run_security_flags_can_be_overridden(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        tool.run(image="alpine:latest", network_disabled=False, read_only=False, mem_limit="1g")

        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["network_disabled"] is False
        assert call_kwargs["read_only"] is False
        assert call_kwargs["mem_limit"] == "1g"

    def test_run_injects_env_vars(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        tool.run(image="alpine:latest", env={"FOO": "bar", "BAZ": "qux"})

        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["environment"] == {"FOO": "bar", "BAZ": "qux"}

    def test_run_no_env_when_not_provided(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        tool.run(image="alpine:latest")

        call_kwargs = mock_client.containers.run.call_args[1]
        assert "environment" not in call_kwargs

    def test_run_container_error_returns_result(self):
        import docker.errors

        tool, mock_client = _make_tool()
        exc = docker.errors.ContainerError(
            container=MagicMock(),
            exit_status=1,
            command="sh",
            image="alpine:latest",
            stderr=b"error output",
        )
        mock_client.containers.run.side_effect = exc

        result = tool.run(image="alpine:latest")

        assert result.success is False
        assert result.exit_code == 1
        assert result.stderr == "error output"

    def test_run_image_not_found(self):
        import docker.errors

        tool, mock_client = _make_tool()
        mock_client.containers.run.side_effect = docker.errors.ImageNotFound("no such image")

        result = tool.run(image="nonexistent:latest")

        assert result.success is False
        assert result.exit_code == -1
        assert result.error is not None

    def test_run_api_error(self):
        import docker.errors

        tool, mock_client = _make_tool()
        mock_client.containers.run.side_effect = docker.errors.APIError("server error")

        result = tool.run(image="alpine:latest")

        assert result.success is False

    def test_run_result_is_pydantic_model(self):
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        result = tool.run(image="alpine:latest")

        assert isinstance(result, RunResult)

    def test_run_sets_remove_true(self):
        """Containers should be auto-removed after execution."""
        tool, mock_client = _make_tool()
        mock_client.containers.run.return_value = b""

        tool.run(image="alpine:latest")

        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["remove"] is True


# ---------------------------------------------------------------------------
# push — policy gating
# ---------------------------------------------------------------------------

class TestDockerPush:
    def test_push_success_no_policy(self):
        tool, mock_client = _make_tool()
        mock_client.images.push.return_value = iter([{"status": "Pushed"}])

        result = tool.push(image="myapp:latest", registry="registry.example.com")

        assert result.success is True
        assert result.blocked is False
        assert result.error is None

    def test_push_with_permissive_policy_succeeds(self):
        tool, mock_client = _make_tool()
        mock_client.images.push.return_value = iter([{"status": "Pushed"}])
        engine = _make_policy_engine()  # no blocked commands

        result = tool.push(
            image="myapp:latest",
            registry="registry.example.com",
            policy_engine=engine,
        )

        assert result.success is True
        assert result.blocked is False

    def test_push_blocked_by_policy(self):
        tool, mock_client = _make_tool()
        # Block any command containing "docker push"
        engine = _make_policy_engine(commands=["docker push"])

        result = tool.push(
            image="myapp:latest",
            registry="registry.example.com",
            policy_engine=engine,
        )

        assert result.success is False
        assert result.blocked is True
        assert result.block_reason is not None
        assert "docker push" in result.block_reason.lower() or result.block_reason != ""
        # Docker SDK should NOT have been called
        mock_client.images.push.assert_not_called()

    def test_push_api_error(self):
        import docker.errors

        tool, mock_client = _make_tool()
        mock_client.images.push.side_effect = docker.errors.APIError("push failed")

        result = tool.push(image="myapp:latest", registry="registry.example.com")

        assert result.success is False
        assert result.error is not None

    def test_push_stream_error_entry(self):
        tool, mock_client = _make_tool()
        mock_client.images.push.return_value = iter([
            {"status": "Pushing"},
            {"error": "unauthorized: access denied"},
        ])

        result = tool.push(image="myapp:latest", registry="registry.example.com")

        assert result.success is False
        assert "unauthorized" in result.error

    def test_push_result_contains_image_and_registry(self):
        tool, mock_client = _make_tool()
        mock_client.images.push.return_value = iter([{"status": "Pushed"}])

        result = tool.push(image="myapp:v1.2.3", registry="gcr.io/myproject")

        assert result.image == "myapp:v1.2.3"
        assert result.registry == "gcr.io/myproject"

    def test_push_result_is_pydantic_model(self):
        tool, mock_client = _make_tool()
        mock_client.images.push.return_value = iter([])

        result = tool.push(image="myapp:latest", registry="registry.example.com")

        assert isinstance(result, PushResult)

    def test_push_without_policy_always_attempts(self):
        """With no policy_engine, push should always be attempted."""
        tool, mock_client = _make_tool()
        mock_client.images.push.return_value = iter([{"status": "Pushed"}])

        result = tool.push(image="myapp:latest", registry="registry.example.com")

        mock_client.images.push.assert_called_once()
        assert result.success is True
