"""Tests for confidence scoring — aggregation from mock agent results."""

import json
from pathlib import Path

import pytest

from dockcheck.core.confidence import (
    ActionNeeded,
    AgentStepResult,
    ConfidenceScorer,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mock_agent_responses"


def load_step_result(filename: str) -> AgentStepResult:
    with open(FIXTURES / filename) as f:
        return AgentStepResult.model_validate(json.load(f))


class TestAgentStepResult:
    def test_parse_analyze_pass(self):
        result = load_step_result("analyze_pass.json")
        assert result.step == "analyze"
        assert result.completed is True
        assert result.confidence == 0.95
        assert result.action_needed == ActionNeeded.NONE

    def test_parse_test_pass(self):
        result = load_step_result("test_pass.json")
        assert result.step == "test"
        assert result.completed is True
        assert result.confidence == 0.90

    def test_parse_test_fail(self):
        result = load_step_result("test_fail.json")
        assert result.step == "test"
        assert result.confidence == 0.40
        assert result.action_needed == ActionNeeded.RETRY
        assert len(result.findings) == 3

    def test_parse_security_critical(self):
        result = load_step_result("security_critical.json")
        assert result.step == "security"
        assert result.confidence == 0.0
        assert result.action_needed == ActionNeeded.ESCALATE
        assert result.findings[0].severity == "critical"

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            AgentStepResult(step="x", completed=True, confidence=1.5)
        with pytest.raises(Exception):
            AgentStepResult(step="x", completed=True, confidence=-0.1)


class TestConfidenceScorer:
    @pytest.fixture()
    def scorer(self):
        return ConfidenceScorer()

    def test_empty_results(self, scorer):
        score = scorer.score([])
        assert score.score == 0.0
        assert "No agent results" in score.reason

    def test_all_passing(self, scorer):
        results = [
            load_step_result("analyze_pass.json"),
            load_step_result("test_pass.json"),
        ]
        score = scorer.score(results)
        assert score.score > 0.8
        assert score.has_critical is False
        assert score.has_errors is False
        assert score.incomplete_steps == []

    def test_critical_finding_zeros_score(self, scorer):
        results = [
            load_step_result("analyze_pass.json"),
            load_step_result("test_pass.json"),
            load_step_result("security_critical.json"),
        ]
        score = scorer.score(results)
        assert score.score == 0.0
        assert score.has_critical is True
        assert "Critical finding" in score.reason

    def test_error_findings_penalize(self, scorer):
        results = [
            load_step_result("analyze_pass.json"),
            load_step_result("test_fail.json"),
        ]
        score = scorer.score(results)
        # Test fail has errors, so 20% penalty applies
        assert score.has_errors is True
        assert score.score < 0.8

    def test_incomplete_steps_penalize(self, scorer):
        incomplete = AgentStepResult(
            step="test",
            completed=False,
            confidence=0.7,
            summary="Timed out",
        )
        results = [load_step_result("analyze_pass.json"), incomplete]
        score = scorer.score(results)
        assert "test" in score.incomplete_steps
        assert score.score < scorer.score([
            load_step_result("analyze_pass.json"),
            load_step_result("test_pass.json"),
        ]).score

    def test_custom_weights(self):
        scorer = ConfidenceScorer(weights={"analyze": 0.5, "test": 0.5})
        results = [
            AgentStepResult(step="analyze", completed=True, confidence=1.0),
            AgentStepResult(step="test", completed=True, confidence=0.0),
        ]
        score = scorer.score(results)
        assert score.score == pytest.approx(0.5, abs=0.01)

    def test_unknown_step_gets_default_weight(self, scorer):
        results = [
            AgentStepResult(step="custom_step", completed=True, confidence=0.8),
        ]
        score = scorer.score(results)
        assert score.score > 0.0

    def test_step_scores_tracked(self, scorer):
        results = [
            AgentStepResult(step="analyze", completed=True, confidence=0.9),
            AgentStepResult(step="test", completed=True, confidence=0.8),
        ]
        score = scorer.score(results)
        assert score.step_scores["analyze"] == 0.9
        assert score.step_scores["test"] == 0.8

    def test_score_clamped_to_1(self):
        scorer = ConfidenceScorer(weights={"x": 1.0})
        results = [
            AgentStepResult(step="x", completed=True, confidence=1.0),
        ]
        score = scorer.score(results)
        assert score.score <= 1.0

    def test_score_clamped_to_0(self):
        scorer = ConfidenceScorer(weights={"x": 1.0})
        results = [
            AgentStepResult(step="x", completed=False, confidence=0.0),
        ]
        score = scorer.score(results)
        assert score.score >= 0.0
