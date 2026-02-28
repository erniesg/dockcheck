"""Tests for policy engine — parsing, evaluation, and threshold logic."""

from pathlib import Path

import pytest

from dockcheck.core.policy import (
    Policy,
    PolicyEngine,
    Verdict,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestPolicyParsing:
    def test_load_from_yaml(self):
        policy = Policy.from_yaml(FIXTURES / "sample_policy.yaml")
        assert policy.version == "1"
        assert len(policy.hard_stops.commands) == 8
        assert len(policy.hard_stops.critical_paths) == 3

    def test_load_from_dict(self):
        data = {
            "version": "1",
            "hard_stops": {
                "commands": [{"pattern": "rm -rf"}],
                "critical_paths": ["**/prod/**"],
            },
        }
        policy = Policy.from_dict(data)
        assert len(policy.hard_stops.commands) == 1
        assert policy.hard_stops.critical_paths == ["**/prod/**"]

    def test_default_values(self):
        policy = Policy()
        assert policy.version == "1"
        assert policy.confidence_thresholds.auto_deploy_staging == 0.8
        assert policy.confidence_thresholds.auto_promote_prod == 0.9
        assert policy.confidence_thresholds.notify_human == 0.6
        assert policy.hard_stops.circuit_breakers.max_containers == 5

    def test_notifications_parsed(self):
        policy = Policy.from_yaml(FIXTURES / "sample_policy.yaml")
        assert policy.notifications.on_deploy is True
        assert len(policy.notifications.channels) == 3
        assert policy.notifications.channels[0].type == "stdout"
        assert policy.notifications.channels[2].type == "slack"
        assert policy.notifications.channels[2].webhook_url == "${SLACK_WEBHOOK_URL}"

    def test_circuit_breakers_parsed(self):
        policy = Policy.from_yaml(FIXTURES / "sample_policy.yaml")
        breakers = policy.hard_stops.circuit_breakers
        assert breakers.max_containers == 5
        assert breakers.max_cost_per_run_usd == 10.0
        assert breakers.max_deploys_per_hour == 3
        assert breakers.max_file_deletes_per_turn == 10


class TestPolicyEvaluation:
    @pytest.fixture()
    def engine(self):
        return PolicyEngine.from_yaml(FIXTURES / "sample_policy.yaml")

    def test_safe_commands_pass(self, engine):
        result = engine.evaluate(commands=["pytest", "docker build ."])
        assert result.verdict == Verdict.PASS
        assert result.reasons == []

    def test_dangerous_command_blocks(self, engine):
        result = engine.evaluate(commands=["rm -rf /tmp/data"])
        assert result.verdict == Verdict.BLOCK
        assert len(result.blocked_commands) == 1
        assert "rm -rf" in result.reasons[0]

    def test_multiple_dangerous_commands(self, engine):
        result = engine.evaluate(
            commands=["rm -rf /", "DROP TABLE users", "echo hello"]
        )
        assert result.verdict == Verdict.BLOCK
        assert len(result.blocked_commands) == 2

    def test_force_push_blocked(self, engine):
        result = engine.evaluate(commands=["git push --force origin main"])
        assert result.verdict == Verdict.BLOCK
        assert any("git push --force" in r for r in result.reasons)

    def test_terraform_destroy_blocked(self, engine):
        result = engine.evaluate(commands=["terraform destroy -auto-approve"])
        assert result.verdict == Verdict.BLOCK

    def test_critical_path_blocks(self, engine):
        result = engine.evaluate(file_paths=["deploy/production/config.yaml"])
        assert result.verdict == Verdict.BLOCK
        assert len(result.blocked_paths) == 1

    def test_env_file_blocks(self, engine):
        result = engine.evaluate(file_paths=[".env.production"])
        assert result.verdict == Verdict.BLOCK

    def test_secrets_path_blocks(self, engine):
        result = engine.evaluate(file_paths=["config/secrets/api_keys.json"])
        assert result.verdict == Verdict.BLOCK

    def test_safe_paths_pass(self, engine):
        result = engine.evaluate(file_paths=["src/app.py", "tests/test_app.py"])
        assert result.verdict == Verdict.PASS

    def test_container_limit_fails(self, engine):
        result = engine.evaluate(container_count=10)
        assert result.verdict == Verdict.FAIL
        assert len(result.breaker_violations) == 1

    def test_cost_limit_fails(self, engine):
        result = engine.evaluate(cost_usd=25.0)
        assert result.verdict == Verdict.FAIL

    def test_deploy_rate_limit_fails(self, engine):
        result = engine.evaluate(deploys_this_hour=5)
        assert result.verdict == Verdict.FAIL

    def test_file_delete_limit_fails(self, engine):
        result = engine.evaluate(file_deletes=15)
        assert result.verdict == Verdict.FAIL

    def test_block_overrides_fail(self, engine):
        """BLOCK from hard stops takes precedence over FAIL from breakers."""
        result = engine.evaluate(
            commands=["rm -rf /"],
            container_count=100,
        )
        assert result.verdict == Verdict.BLOCK

    def test_no_inputs_passes(self, engine):
        result = engine.evaluate()
        assert result.verdict == Verdict.PASS
        assert result.reasons == []

    def test_combined_commands_and_paths(self, engine):
        result = engine.evaluate(
            commands=["DROP TABLE users"],
            file_paths=["deploy/production/main.tf"],
        )
        assert result.verdict == Verdict.BLOCK
        assert len(result.blocked_commands) == 1
        assert len(result.blocked_paths) == 1


class TestConfidenceThresholds:
    @pytest.fixture()
    def engine(self):
        return PolicyEngine.from_yaml(FIXTURES / "sample_policy.yaml")

    def test_auto_deploy_staging(self, engine):
        assert engine.should_auto_deploy_staging(0.85) is True
        assert engine.should_auto_deploy_staging(0.80) is True
        assert engine.should_auto_deploy_staging(0.79) is False

    def test_auto_promote_prod(self, engine):
        assert engine.should_auto_promote_prod(0.95) is True
        assert engine.should_auto_promote_prod(0.90) is True
        assert engine.should_auto_promote_prod(0.89) is False

    def test_notify_human(self, engine):
        assert engine.should_notify_human(0.5) is True
        assert engine.should_notify_human(0.59) is True
        assert engine.should_notify_human(0.6) is False
        assert engine.should_notify_human(0.9) is False
