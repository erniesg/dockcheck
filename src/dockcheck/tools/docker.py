"""Docker SDK wrapper — build, run, and push with policy gating and security defaults."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import docker
import docker.errors
from pydantic import BaseModel, Field

from dockcheck.core.policy import PolicyEngine, Verdict

logger = logging.getLogger(__name__)


class BuildResult(BaseModel):
    success: bool
    image_tag: str
    logs: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class RunResult(BaseModel):
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None


class PushResult(BaseModel):
    success: bool
    image: str
    registry: str
    blocked: bool = False
    block_reason: Optional[str] = None
    error: Optional[str] = None


class DockerTool:
    """
    Wraps the Docker Python SDK with security-hardened defaults.

    Build and run are auto-approved.
    Push is gated by PolicyEngine when one is provided.

    Security defaults for container runs:
    - Network disabled (``network_disabled=True``)
    - Read-only root filesystem
    - Memory limited to 512 MiB
    """

    def __init__(self, client: Optional[Any] = None) -> None:
        if client is not None:
            self.client = client
            return
        try:
            self.client = docker.from_env()
        except Exception as exc:  # noqa: BLE001
            # Covers DockerException, ConnectionError, and any environment issues
            logger.warning("Docker client initialisation failed: %s", exc)
            self.client = None

    # ------------------------------------------------------------------
    # build — auto-approved
    # ------------------------------------------------------------------

    def build(
        self,
        path: str = ".",
        tag: str = "dockcheck-build",
        dockerfile: str = "Dockerfile",
    ) -> BuildResult:
        """Build a Docker image. Always permitted — no policy check required."""
        logger.info("docker build: path=%s tag=%s dockerfile=%s", path, tag, dockerfile)
        logs: List[str] = []
        try:
            _image, build_logs = self.client.images.build(
                path=path,
                tag=tag,
                dockerfile=dockerfile,
                rm=True,
            )
            for entry in build_logs:
                if isinstance(entry, dict):
                    line = entry.get("stream", entry.get("status", ""))
                    if line:
                        logs.append(line.rstrip("\n"))
            logger.info("docker build succeeded: %s", tag)
            return BuildResult(success=True, image_tag=tag, logs=logs)
        except docker.errors.BuildError as exc:
            error_msg = str(exc)
            logger.error("docker build failed: %s", error_msg)
            return BuildResult(success=False, image_tag=tag, logs=logs, error=error_msg)
        except docker.errors.APIError as exc:
            error_msg = str(exc)
            logger.error("docker API error during build: %s", error_msg)
            return BuildResult(success=False, image_tag=tag, logs=logs, error=error_msg)
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            logger.error("unexpected error during docker build: %s", error_msg)
            return BuildResult(success=False, image_tag=tag, logs=logs, error=error_msg)

    # ------------------------------------------------------------------
    # run — auto-approved, security-hardened
    # ------------------------------------------------------------------

    def run(
        self,
        image: str,
        command: str = "",
        env: Optional[Dict[str, str]] = None,
        timeout: int = 300,
        network_disabled: bool = True,
        read_only: bool = True,
        mem_limit: str = "512m",
    ) -> RunResult:
        """
        Run a container with security-hardened defaults.

        Always permitted — no policy check required.
        Defaults: network disabled, read-only rootfs, 512 MiB memory cap.
        """
        logger.info(
            "docker run: image=%s command=%r network_disabled=%s read_only=%s mem_limit=%s",
            image,
            command,
            network_disabled,
            read_only,
            mem_limit,
        )
        try:
            run_kwargs: Dict[str, Any] = {
                "image": image,
                "remove": True,
                "stdout": True,
                "stderr": True,
                "mem_limit": mem_limit,
                "read_only": read_only,
                "network_disabled": network_disabled,
            }
            if command:
                run_kwargs["command"] = command
            if env:
                run_kwargs["environment"] = env

            output: bytes = self.client.containers.run(**run_kwargs)
            stdout = output.decode("utf-8", errors="replace") if output else ""
            logger.info("docker run succeeded: image=%s", image)
            return RunResult(success=True, exit_code=0, stdout=stdout)
        except docker.errors.ContainerError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            logger.warning("docker run container error: exit_code=%s", exc.exit_status)
            return RunResult(
                success=False,
                exit_code=exc.exit_status,
                stderr=stderr,
                error=str(exc),
            )
        except docker.errors.ImageNotFound as exc:
            error_msg = str(exc)
            logger.error("docker run image not found: %s", image)
            return RunResult(success=False, exit_code=-1, error=error_msg)
        except docker.errors.APIError as exc:
            error_msg = str(exc)
            logger.error("docker API error during run: %s", error_msg)
            return RunResult(success=False, exit_code=-1, error=error_msg)
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            logger.error("unexpected error during docker run: %s", error_msg)
            return RunResult(success=False, exit_code=-1, error=error_msg)

    # ------------------------------------------------------------------
    # push — gated by PolicyEngine
    # ------------------------------------------------------------------

    def push(
        self,
        image: str,
        registry: str,
        policy_engine: Optional[PolicyEngine] = None,
    ) -> PushResult:
        """
        Push an image to a registry.

        When a PolicyEngine is supplied the command ``docker push`` is evaluated
        against policy rules.  A BLOCK verdict prevents the push.
        """
        push_command = f"docker push {registry}/{image}"
        logger.info("docker push: image=%s registry=%s", image, registry)

        # Policy gate
        if policy_engine is not None:
            result = policy_engine.evaluate(commands=[push_command])
            if result.verdict == Verdict.BLOCK:
                reason = "; ".join(result.reasons) or "Blocked by policy"
                logger.warning("docker push blocked by policy: %s", reason)
                return PushResult(
                    success=False,
                    image=image,
                    registry=registry,
                    blocked=True,
                    block_reason=reason,
                )

        try:
            full_tag = f"{registry}/{image}" if registry else image
            push_output = self.client.images.push(full_tag, stream=True, decode=True)
            for entry in push_output:
                if "error" in entry:
                    error_msg = entry["error"]
                    logger.error("docker push error: %s", error_msg)
                    return PushResult(
                        success=False,
                        image=image,
                        registry=registry,
                        error=error_msg,
                    )
            logger.info("docker push succeeded: %s/%s", registry, image)
            return PushResult(success=True, image=image, registry=registry)
        except docker.errors.APIError as exc:
            error_msg = str(exc)
            logger.error("docker API error during push: %s", error_msg)
            return PushResult(success=False, image=image, registry=registry, error=error_msg)
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            logger.error("unexpected error during docker push: %s", error_msg)
            return PushResult(success=False, image=image, registry=registry, error=error_msg)
