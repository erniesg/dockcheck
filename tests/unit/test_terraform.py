"""Tests for TerraformTool — destroy always blocked, plan parsing, apply gating."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from dockcheck.core.policy import Policy, PolicyEngine
from dockcheck.tools.terraform import (
    PlanResult,
    ResourceChange,
    TerraformResult,
    TerraformTool,
    _DESTROY_BLOCK_REASON,
    _count_actions,
    _extract_resource_changes,
    _parse_plan_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(staging_threshold: float = 0.8) -> PolicyEngine:
    policy = Policy.from_dict(
        {
            "version": "1",
            "confidence_thresholds": {
                "auto_deploy_staging": staging_threshold,
                "auto_promote_prod": 0.9,
                "notify_human": 0.6,
            },
        }
    )
    return PolicyEngine(policy)


def _completed_process(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


SAMPLE_PLAN_JSON = json.dumps(
    {
        "format_version": "1.0",
        "resource_changes": [
            {
                "address": "aws_instance.web",
                "type": "aws_instance",
                "name": "web",
                "change": {"actions": ["create"]},
            },
            {
                "address": "aws_s3_bucket.data",
                "type": "aws_s3_bucket",
                "name": "data",
                "change": {"actions": ["update"]},
            },
            {
                "address": "aws_security_group.old",
                "type": "aws_security_group",
                "name": "old",
                "change": {"actions": ["delete"]},
            },
        ],
    }
)


# ---------------------------------------------------------------------------
# destroy — ALWAYS blocked
# ---------------------------------------------------------------------------

class TestTerraformDestroyAlwaysBlocked:
    def test_destroy_is_blocked(self):
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.destroy()
        assert result.blocked is True
        assert result.success is False

    def test_destroy_block_reason_is_set(self):
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.destroy()
        assert result.block_reason == _DESTROY_BLOCK_REASON

    def test_destroy_blocked_even_with_no_policy(self):
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=None)
        result = tool.destroy()
        assert result.blocked is True

    def test_destroy_blocked_with_permissive_policy(self):
        engine = _make_engine(staging_threshold=0.0)
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=engine)
        result = tool.destroy()
        assert result.blocked is True

    def test_destroy_never_calls_subprocess(self):
        tool = TerraformTool(workdir="/tmp/infra")
        with patch("dockcheck.tools.terraform.subprocess.run") as mock_run:
            tool.destroy()
        mock_run.assert_not_called()

    def test_destroy_returns_terraform_result_type(self):
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.destroy()
        assert isinstance(result, TerraformResult)

    def test_destroy_command_field_set(self):
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.destroy()
        assert result.command == "terraform destroy"

    def test_destroy_block_reason_explains_human_required(self):
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.destroy()
        assert "manual" in result.block_reason.lower() or "human" in result.block_reason.lower()


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestTerraformInit:
    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_init_success(self, mock_run):
        mock_run.return_value = _completed_process(stdout="Terraform initialized.")
        tool = TerraformTool(workdir="/tmp/infra")

        result = tool.init()

        assert result.success is True
        assert result.command == "terraform init"
        assert "terraform" in mock_run.call_args[0][0][0]
        assert "init" in mock_run.call_args[0][0]

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_init_failure(self, mock_run):
        mock_run.return_value = _completed_process(returncode=1, stderr="Error initializing.")
        tool = TerraformTool(workdir="/tmp/infra")

        result = tool.init()

        assert result.success is False
        assert result.error is not None

    @patch("dockcheck.tools.terraform.subprocess.run", side_effect=FileNotFoundError)
    def test_init_terraform_not_found(self, _mock):
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.init()
        assert result.success is False
        assert "not found" in result.error.lower() or "path" in result.error.lower()

    @patch(
        "dockcheck.tools.terraform.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="terraform", timeout=300),
    )
    def test_init_timeout(self, _mock):
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.init()
        assert result.success is False
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_init_passes_workdir(self, mock_run):
        mock_run.return_value = _completed_process()
        tool = TerraformTool(workdir="/custom/infra")
        tool.init()
        assert mock_run.call_args[1]["cwd"] == "/custom/infra"

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_init_result_is_pydantic_model(self, mock_run):
        mock_run.return_value = _completed_process()
        result = TerraformTool().init()
        assert isinstance(result, TerraformResult)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestTerraformValidate:
    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_validate_success(self, mock_run):
        mock_run.return_value = _completed_process(stdout="Success! The configuration is valid.")
        tool = TerraformTool(workdir="/tmp/infra")

        result = tool.validate()

        assert result.success is True
        assert result.command == "terraform validate"

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_validate_failure(self, mock_run):
        mock_run.return_value = _completed_process(returncode=1, stderr="Invalid configuration")
        tool = TerraformTool(workdir="/tmp/infra")

        result = tool.validate()

        assert result.success is False
        assert result.error is not None

    @patch("dockcheck.tools.terraform.subprocess.run", side_effect=FileNotFoundError)
    def test_validate_terraform_not_found(self, _mock):
        result = TerraformTool().validate()
        assert result.success is False

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_validate_passes_workdir(self, mock_run):
        mock_run.return_value = _completed_process()
        tool = TerraformTool(workdir="/custom/infra")
        tool.validate()
        assert mock_run.call_args[1]["cwd"] == "/custom/infra"


# ---------------------------------------------------------------------------
# plan — JSON parsing
# ---------------------------------------------------------------------------

class TestTerraformPlan:
    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_plan_success_with_json(self, mock_run):
        # First call: terraform plan; second: terraform show -json
        mock_run.side_effect = [
            _completed_process(stdout="Plan: 1 to add."),
            _completed_process(stdout=SAMPLE_PLAN_JSON),
        ]
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.plan(output_json=True)

        assert result.success is True
        assert result.add_count == 1
        assert result.change_count == 1
        assert result.destroy_count == 1

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_plan_resource_changes_parsed(self, mock_run):
        mock_run.side_effect = [
            _completed_process(stdout="Plan: 1 to add."),
            _completed_process(stdout=SAMPLE_PLAN_JSON),
        ]
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.plan(output_json=True)

        assert len(result.resource_changes) == 3
        addresses = [rc.address for rc in result.resource_changes]
        assert "aws_instance.web" in addresses

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_plan_without_json(self, mock_run):
        mock_run.return_value = _completed_process(stdout="Plan: 1 to add, 0 to change, 0 to destroy.")
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.plan(output_json=False)

        assert result.success is True
        # JSON parsing should not have been attempted
        assert mock_run.call_count == 1

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_plan_failure_returns_error(self, mock_run):
        mock_run.return_value = _completed_process(returncode=1, stderr="No such file or directory")
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.plan()

        assert result.success is False
        assert result.error is not None

    @patch("dockcheck.tools.terraform.subprocess.run", side_effect=FileNotFoundError)
    def test_plan_terraform_not_found(self, _mock):
        result = TerraformTool().plan()
        assert result.success is False

    @patch(
        "dockcheck.tools.terraform.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="terraform", timeout=300),
    )
    def test_plan_timeout(self, _mock):
        result = TerraformTool().plan()
        assert result.success is False
        assert "timed out" in result.error.lower()

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_plan_result_is_pydantic_model(self, mock_run):
        mock_run.side_effect = [
            _completed_process(),
            _completed_process(stdout="{}"),
        ]
        result = TerraformTool().plan()
        assert isinstance(result, PlanResult)

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_plan_passes_workdir(self, mock_run):
        mock_run.side_effect = [_completed_process(), _completed_process(stdout="{}")]
        tool = TerraformTool(workdir="/custom/infra")
        tool.plan()
        assert mock_run.call_args_list[0][1]["cwd"] == "/custom/infra"

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_plan_raw_json_stored(self, mock_run):
        mock_run.side_effect = [
            _completed_process(),
            _completed_process(stdout=SAMPLE_PLAN_JSON),
        ]
        result = TerraformTool().plan(output_json=True)
        assert result.raw_json == SAMPLE_PLAN_JSON


# ---------------------------------------------------------------------------
# apply — policy gating
# ---------------------------------------------------------------------------

class TestTerraformApply:
    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_succeeds_with_sufficient_confidence(self, mock_run):
        mock_run.return_value = _completed_process(stdout="Apply complete!")
        engine = _make_engine(staging_threshold=0.8)
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=engine)

        result = tool.apply(confidence=0.85)

        assert result.success is True
        assert result.blocked is False

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_blocked_below_threshold(self, mock_run):
        engine = _make_engine(staging_threshold=0.8)
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=engine)

        result = tool.apply(confidence=0.5)

        assert result.success is False
        assert result.blocked is True
        assert result.block_reason is not None
        mock_run.assert_not_called()

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_at_exact_threshold_succeeds(self, mock_run):
        mock_run.return_value = _completed_process(stdout="Apply complete!")
        engine = _make_engine(staging_threshold=0.8)
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=engine)

        result = tool.apply(confidence=0.8)

        assert result.blocked is False
        assert result.success is True

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_no_policy_always_runs(self, mock_run):
        mock_run.return_value = _completed_process(stdout="Apply complete!")
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=None)

        result = tool.apply(confidence=0.0)  # Low confidence but no policy

        assert result.success is True
        assert result.blocked is False

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_failure_propagated(self, mock_run):
        mock_run.return_value = _completed_process(returncode=1, stderr="Apply failed")
        engine = _make_engine(staging_threshold=0.0)
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=engine)

        result = tool.apply(confidence=0.9)

        assert result.success is False
        assert result.blocked is False
        assert result.error is not None

    @patch("dockcheck.tools.terraform.subprocess.run", side_effect=FileNotFoundError)
    def test_apply_terraform_not_found(self, _mock):
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=None)
        result = tool.apply()
        assert result.success is False

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_block_reason_contains_threshold(self, mock_run):
        engine = _make_engine(staging_threshold=0.8)
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=engine)

        result = tool.apply(confidence=0.3)

        assert "0.8" in result.block_reason or "threshold" in result.block_reason.lower()

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_result_is_pydantic_model(self, mock_run):
        mock_run.return_value = _completed_process()
        result = TerraformTool(workdir="/tmp/infra").apply()
        assert isinstance(result, TerraformResult)

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_uses_auto_approve_flag(self, mock_run):
        mock_run.return_value = _completed_process()
        tool = TerraformTool(workdir="/tmp/infra", policy_engine=None)
        tool.apply()
        cmd = mock_run.call_args[0][0]
        assert "-auto-approve" in cmd

    @patch("dockcheck.tools.terraform.subprocess.run")
    def test_apply_command_field(self, mock_run):
        mock_run.return_value = _completed_process()
        tool = TerraformTool(workdir="/tmp/infra")
        result = tool.apply()
        assert result.command == "terraform apply"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestParsePlanJson:
    def test_valid_json(self):
        data = _parse_plan_json(SAMPLE_PLAN_JSON)
        assert "resource_changes" in data

    def test_invalid_json_returns_empty_dict(self):
        data = _parse_plan_json("not valid json {{{")
        assert data == {}

    def test_empty_string_returns_empty_dict(self):
        data = _parse_plan_json("")
        assert data == {}


class TestExtractResourceChanges:
    def test_extracts_all_changes(self):
        plan_data = json.loads(SAMPLE_PLAN_JSON)
        changes = _extract_resource_changes(plan_data)
        assert len(changes) == 3

    def test_address_populated(self):
        plan_data = json.loads(SAMPLE_PLAN_JSON)
        changes = _extract_resource_changes(plan_data)
        addresses = [c.address for c in changes]
        assert "aws_instance.web" in addresses

    def test_action_populated(self):
        plan_data = json.loads(SAMPLE_PLAN_JSON)
        changes = _extract_resource_changes(plan_data)
        create_change = next(c for c in changes if c.address == "aws_instance.web")
        assert create_change.action == ["create"]

    def test_empty_plan_returns_empty_list(self):
        changes = _extract_resource_changes({})
        assert changes == []

    def test_returns_list_of_resource_change_models(self):
        plan_data = json.loads(SAMPLE_PLAN_JSON)
        changes = _extract_resource_changes(plan_data)
        assert all(isinstance(c, ResourceChange) for c in changes)


class TestCountActions:
    def test_counts_creates(self):
        changes = [
            ResourceChange(address="a", action=["create"]),
            ResourceChange(address="b", action=["create"]),
        ]
        add, change, destroy = _count_actions(changes)
        assert add == 2
        assert change == 0
        assert destroy == 0

    def test_counts_updates(self):
        changes = [ResourceChange(address="a", action=["update"])]
        add, change, destroy = _count_actions(changes)
        assert add == 0
        assert change == 1
        assert destroy == 0

    def test_counts_deletes(self):
        changes = [ResourceChange(address="a", action=["delete"])]
        add, change, destroy = _count_actions(changes)
        assert add == 0
        assert change == 0
        assert destroy == 1

    def test_counts_mixed(self):
        changes = [
            ResourceChange(address="a", action=["create"]),
            ResourceChange(address="b", action=["update"]),
            ResourceChange(address="c", action=["delete"]),
        ]
        add, change, destroy = _count_actions(changes)
        assert add == 1
        assert change == 1
        assert destroy == 1

    def test_empty_list(self):
        add, change, destroy = _count_actions([])
        assert add == 0
        assert change == 0
        assert destroy == 0
