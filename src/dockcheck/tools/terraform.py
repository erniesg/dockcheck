"""Terraform CLI wrapper — plan/apply with policy gating; destroy is unconditionally blocked."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dockcheck.core.policy import PolicyEngine, Verdict

logger = logging.getLogger(__name__)

# destroy is ALWAYS blocked — no policy can override this
_DESTROY_BLOCK_REASON = (
    "terraform destroy is unconditionally blocked by dockcheck. "
    "Infrastructure destruction requires manual human execution."
)


class TerraformResult(BaseModel):
    success: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    error: str | None = None
    blocked: bool = False
    block_reason: str | None = None


class ResourceChange(BaseModel):
    address: str
    action: list[str] = Field(default_factory=list)  # e.g. ["create"], ["destroy"]
    resource_type: str = ""
    name: str = ""


class PlanResult(BaseModel):
    success: bool
    command: str = "terraform plan"
    raw_json: str = ""
    resource_changes: list[ResourceChange] = Field(default_factory=list)
    add_count: int = 0
    change_count: int = 0
    destroy_count: int = 0
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    error: str | None = None
    blocked: bool = False
    block_reason: str | None = None


def _run_terraform(
    args: list[str],
    workdir: str,
    timeout: int = 300,
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run a terraform sub-command and return the CompletedProcess."""
    cmd = ["terraform"] + args
    logger.debug("Running: %s (cwd=%s)", " ".join(cmd), workdir)
    return subprocess.run(
        cmd,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_plan_json(raw: str) -> dict[str, Any]:
    """Parse terraform plan JSON output, returning an empty dict on failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse terraform plan JSON: %s", exc)
        return {}


def _extract_resource_changes(plan_data: dict[str, Any]) -> list[ResourceChange]:
    """Extract resource changes from parsed terraform plan output."""
    changes: list[ResourceChange] = []
    for rc in plan_data.get("resource_changes", []):
        change = rc.get("change", {})
        actions = change.get("actions", [])
        changes.append(
            ResourceChange(
                address=rc.get("address", ""),
                action=actions,
                resource_type=rc.get("type", ""),
                name=rc.get("name", ""),
            )
        )
    return changes


def _count_actions(changes: list[ResourceChange]) -> tuple:  # type: ignore[type-arg]
    """Return (add, change, destroy) counts from a list of ResourceChange objects."""
    add = sum(1 for c in changes if c.action == ["create"])
    change = sum(1 for c in changes if c.action == ["update"])
    destroy = sum(1 for c in changes if "delete" in c.action)
    return add, change, destroy


class TerraformTool:
    """
    Thin subprocess wrapper around the terraform CLI.

    Commands:
    - ``init()`` — run terraform init (auto-approved)
    - ``validate()`` — run terraform validate (auto-approved)
    - ``plan()`` — run terraform plan, parse JSON output (auto-approved, read-only)
    - ``apply()`` — gated by PolicyEngine confidence threshold
    - ``destroy()`` — ALWAYS blocked, no exceptions

    Args:
        workdir: Directory containing .tf files (default ``"./infra"``).
        policy_engine: Optional PolicyEngine for apply gating.
    """

    def __init__(
        self,
        workdir: str = "./infra",
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self.workdir = workdir
        self.policy_engine = policy_engine

    # ------------------------------------------------------------------
    # init — auto-approved
    # ------------------------------------------------------------------

    def init(self) -> TerraformResult:
        """Run ``terraform init``. Always permitted."""
        logger.info("terraform init: workdir=%s", self.workdir)
        try:
            proc = _run_terraform(["init", "-no-color"], self.workdir)
            success = proc.returncode == 0
            if not success:
                logger.warning("terraform init failed (rc=%d): %s", proc.returncode, proc.stderr)
            return TerraformResult(
                success=success,
                command="terraform init",
                stdout=proc.stdout,
                stderr=proc.stderr,
                return_code=proc.returncode,
                error=proc.stderr if not success else None,
            )
        except FileNotFoundError:
            error_msg = "terraform CLI not found on PATH."
            logger.error(error_msg)
            return TerraformResult(
                success=False,
                command="terraform init",
                error=error_msg,
                return_code=-1,
            )
        except subprocess.TimeoutExpired:
            error_msg = "terraform init timed out."
            logger.error(error_msg)
            return TerraformResult(
                success=False,
                command="terraform init",
                error=error_msg,
                return_code=-1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("terraform init unexpected error: %s", exc)
            return TerraformResult(
                success=False,
                command="terraform init",
                error=str(exc),
                return_code=-1,
            )

    # ------------------------------------------------------------------
    # validate — auto-approved
    # ------------------------------------------------------------------

    def validate(self) -> TerraformResult:
        """Run ``terraform validate``. Always permitted."""
        logger.info("terraform validate: workdir=%s", self.workdir)
        try:
            proc = _run_terraform(["validate", "-no-color"], self.workdir)
            success = proc.returncode == 0
            if not success:
                logger.warning(
                    "terraform validate failed (rc=%d): %s", proc.returncode, proc.stderr
                )
            return TerraformResult(
                success=success,
                command="terraform validate",
                stdout=proc.stdout,
                stderr=proc.stderr,
                return_code=proc.returncode,
                error=proc.stderr if not success else None,
            )
        except FileNotFoundError:
            error_msg = "terraform CLI not found on PATH."
            logger.error(error_msg)
            return TerraformResult(
                success=False,
                command="terraform validate",
                error=error_msg,
                return_code=-1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("terraform validate unexpected error: %s", exc)
            return TerraformResult(
                success=False,
                command="terraform validate",
                error=str(exc),
                return_code=-1,
            )

    # ------------------------------------------------------------------
    # plan — auto-approved, read-only
    # ------------------------------------------------------------------

    def plan(self, output_json: bool = True) -> PlanResult:
        """
        Run ``terraform plan``.

        When *output_json* is True, saves plan to a binary file then converts
        to JSON for structured parsing.  Always permitted (read-only operation).
        """
        logger.info("terraform plan: workdir=%s output_json=%s", self.workdir, output_json)
        plan_file = str(Path(self.workdir) / "tfplan.binary")

        try:
            # Step 1: generate plan binary
            proc = _run_terraform(
                ["plan", "-no-color", f"-out={plan_file}"], self.workdir
            )
            if proc.returncode != 0:
                logger.warning("terraform plan failed (rc=%d): %s", proc.returncode, proc.stderr)
                return PlanResult(
                    success=False,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    return_code=proc.returncode,
                    error=proc.stderr,
                )

            if not output_json:
                return PlanResult(
                    success=True,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    return_code=proc.returncode,
                )

            # Step 2: convert to JSON
            json_proc = _run_terraform(
                ["show", "-json", "-no-color", plan_file], self.workdir
            )
            raw_json = json_proc.stdout
            plan_data = _parse_plan_json(raw_json)
            resource_changes = _extract_resource_changes(plan_data)
            add, change, destroy = _count_actions(resource_changes)

            logger.info(
                "terraform plan: +%d ~%d -%d", add, change, destroy
            )
            return PlanResult(
                success=True,
                raw_json=raw_json,
                resource_changes=resource_changes,
                add_count=add,
                change_count=change,
                destroy_count=destroy,
                stdout=proc.stdout,
                stderr=proc.stderr,
                return_code=proc.returncode,
            )

        except FileNotFoundError:
            error_msg = "terraform CLI not found on PATH."
            logger.error(error_msg)
            return PlanResult(success=False, error=error_msg, return_code=-1)
        except subprocess.TimeoutExpired:
            error_msg = "terraform plan timed out."
            logger.error(error_msg)
            return PlanResult(success=False, error=error_msg, return_code=-1)
        except Exception as exc:  # noqa: BLE001
            logger.error("terraform plan unexpected error: %s", exc)
            return PlanResult(success=False, error=str(exc), return_code=-1)

    # ------------------------------------------------------------------
    # apply — gated by policy
    # ------------------------------------------------------------------

    def apply(self, confidence: float = 0.0) -> TerraformResult:
        """
        Run ``terraform apply``.

        Gated by PolicyEngine.  If a PolicyEngine is configured, the confidence
        score must satisfy the ``auto_deploy_staging`` threshold; otherwise the
        apply is blocked.
        """
        logger.info(
            "terraform apply requested: confidence=%.3f workdir=%s", confidence, self.workdir
        )

        # Policy gate
        if self.policy_engine is not None:
            if not self.policy_engine.should_auto_deploy_staging(confidence):
                threshold = self.policy_engine.policy.confidence_thresholds.auto_deploy_staging
                block_reason = (
                    f"terraform apply blocked: confidence {confidence:.3f} is below "
                    f"auto_deploy_staging threshold {threshold:.3f}."
                )
                logger.warning(block_reason)
                return TerraformResult(
                    success=False,
                    command="terraform apply",
                    blocked=True,
                    block_reason=block_reason,
                )

            # Also check via full policy evaluate for command patterns
            result = self.policy_engine.evaluate(commands=["terraform apply"])
            if result.verdict == Verdict.BLOCK:
                block_reason = "; ".join(result.reasons) or "Blocked by policy command patterns."
                logger.warning("terraform apply blocked by command policy: %s", block_reason)
                return TerraformResult(
                    success=False,
                    command="terraform apply",
                    blocked=True,
                    block_reason=block_reason,
                )

        try:
            proc = _run_terraform(
                ["apply", "-auto-approve", "-no-color"], self.workdir
            )
            success = proc.returncode == 0
            if not success:
                logger.warning(
                    "terraform apply failed (rc=%d): %s", proc.returncode, proc.stderr
                )
            return TerraformResult(
                success=success,
                command="terraform apply",
                stdout=proc.stdout,
                stderr=proc.stderr,
                return_code=proc.returncode,
                error=proc.stderr if not success else None,
            )
        except FileNotFoundError:
            error_msg = "terraform CLI not found on PATH."
            logger.error(error_msg)
            return TerraformResult(
                success=False,
                command="terraform apply",
                error=error_msg,
                return_code=-1,
            )
        except subprocess.TimeoutExpired:
            error_msg = "terraform apply timed out."
            logger.error(error_msg)
            return TerraformResult(
                success=False,
                command="terraform apply",
                error=error_msg,
                return_code=-1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("terraform apply unexpected error: %s", exc)
            return TerraformResult(
                success=False,
                command="terraform apply",
                error=str(exc),
                return_code=-1,
            )

    # ------------------------------------------------------------------
    # destroy — ALWAYS blocked
    # ------------------------------------------------------------------

    def destroy(self) -> TerraformResult:
        """
        terraform destroy is UNCONDITIONALLY blocked.

        No policy, confidence score, or flag can bypass this restriction.
        Infrastructure destruction must always be performed manually.
        """
        logger.critical(
            "terraform destroy was called and unconditionally blocked. workdir=%s",
            self.workdir,
        )
        return TerraformResult(
            success=False,
            command="terraform destroy",
            blocked=True,
            block_reason=_DESTROY_BLOCK_REASON,
        )
