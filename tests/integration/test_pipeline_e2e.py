"""End-to-end integration tests for the dockcheck pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from dockcheck.cli import cli
from dockcheck.core.confidence import AgentStepResult, ConfidenceScorer
from dockcheck.core.policy import PolicyEngine, Verdict

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestCLIIntegration:
    @pytest.fixture()
    def runner(self):
        return CliRunner()

    def test_init_creates_dockcheck_dir(self, runner):
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert Path(".dockcheck/policy.yaml").exists()
            assert Path("dockcheck.yml").exists()

    def test_init_with_template(self, runner):
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init", "--template", "trading-bot"])
            assert result.exit_code == 0
            policy = Path(".dockcheck/policy.yaml").read_text()
            assert "0.95" in policy  # strict staging threshold
            assert "0.99" in policy  # strict prod threshold

    def test_init_already_exists(self, runner):
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["init"])
            assert "already exists" in result.output

    def test_check_with_safe_diff(self, runner):
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, [
                "check",
                "--diff", str(FIXTURES / "sample_diffs" / "safe_change.diff"),
            ])
            assert result.exit_code == 0
            assert "PASS" in result.output

    def test_check_with_dangerous_diff(self, runner):
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, [
                "check",
                "--diff", str(FIXTURES / "sample_diffs" / "dangerous_change.diff"),
            ])
            assert result.exit_code == 2  # BLOCK
            assert "BLOCK" in result.output

    def test_check_with_blocked_command(self, runner):
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, [
                "check",
                "--commands", "rm -rf /tmp/data",
            ])
            assert result.exit_code == 2
            assert "BLOCK" in result.output

    def test_check_json_output(self, runner):
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, [
                "check",
                "--commands", "pytest",
                "--json-output",
            ])
            assert result.exit_code == 0
            assert '"verdict": "pass"' in result.output

    def test_validate_policy(self, runner):
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["validate"])
            assert result.exit_code == 0
            assert "Policy valid" in result.output

    def test_run_dry_run(self, runner):
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init"])
            result = runner.invoke(cli, ["run", "--dry-run"])
            assert result.exit_code == 0
            assert "Pipeline plan" in result.output
            assert "CHECK" in result.output

    def test_check_no_policy(self, runner):
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["check"])
            assert result.exit_code == 1

    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestPolicyToConfidenceIntegration:
    """Test the full path: policy evaluation → confidence scoring → deployment decision."""

    def test_high_confidence_auto_deploys(self):
        engine = PolicyEngine.from_yaml(FIXTURES / "sample_policy.yaml")
        scorer = ConfidenceScorer()

        # Simulate all-green agent results
        results = [
            AgentStepResult(step="analyze", completed=True, confidence=0.95),
            AgentStepResult(step="test", completed=True, confidence=0.92),
            AgentStepResult(step="security", completed=True, confidence=0.98),
        ]
        confidence = scorer.score(results)

        # Policy check passes
        eval_result = engine.evaluate(commands=["pytest"], file_paths=["src/app.py"])
        assert eval_result.verdict == Verdict.PASS

        # Confidence above staging threshold
        assert engine.should_auto_deploy_staging(confidence.score) is True

    def test_low_confidence_notifies(self):
        engine = PolicyEngine.from_yaml(FIXTURES / "sample_policy.yaml")
        scorer = ConfidenceScorer()

        results = [
            AgentStepResult(step="analyze", completed=True, confidence=0.5),
            AgentStepResult(step="test", completed=False, confidence=0.3),
        ]
        confidence = scorer.score(results)

        assert engine.should_notify_human(confidence.score) is True
        assert engine.should_auto_deploy_staging(confidence.score) is False

    def test_hard_stop_blocks_regardless_of_confidence(self):
        engine = PolicyEngine.from_yaml(FIXTURES / "sample_policy.yaml")

        eval_result = engine.evaluate(
            commands=["rm -rf /"],
            file_paths=["deploy/production/main.tf"],
        )
        assert eval_result.verdict == Verdict.BLOCK
        # Even high confidence shouldn't override hard stops
