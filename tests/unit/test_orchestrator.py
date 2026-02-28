"""Tests for Orchestrator — dependency resolution, decision logic, retry
behaviour, parallel group execution, and policy integration."""

from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dockcheck.agents.dispatch import AgentDispatcher
from dockcheck.agents.schemas import (
    AgentResult,
    Finding,
    FindingSeverity,
    PipelineConfig,
    PipelineResult,
    StepConfig,
)
from dockcheck.core.confidence import ConfidenceScorer
from dockcheck.core.orchestrator import (
    NullNotifier,
    Orchestrator,
    StdoutNotifier,
    _agent_result_to_step_result,
    _group_by_parallel,
)
from dockcheck.core.policy import Policy, PolicyEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_policy(**overrides) -> PolicyEngine:
    """Build a PolicyEngine with permissive thresholds by default."""
    data: dict = {
        "version": "1",
        "confidence_thresholds": {
            "auto_deploy_staging": 0.8,
            "auto_promote_prod": 0.9,
            "notify_human": 0.6,
        },
        "hard_stops": {
            "commands": [{"pattern": "rm -rf"}],
            "critical_paths": ["**/production/**"],
        },
    }
    data.update(overrides)
    return PolicyEngine(Policy.from_dict(data))


def _make_dispatcher(*results: AgentResult) -> AgentDispatcher:
    """Build a dispatcher whose dispatch() returns results in order."""
    dispatcher = AgentDispatcher()
    dispatcher.dispatch = AsyncMock(side_effect=list(results))  # type: ignore[method-assign]
    return dispatcher


def _good_result(confidence: float = 0.9, summary: str = "ok") -> AgentResult:
    return AgentResult(completed=True, confidence=confidence, summary=summary)


def _fail_result(confidence: float = 0.3) -> AgentResult:
    return AgentResult(
        completed=True,
        confidence=confidence,
        summary="failed",
        action_needed="retry",
        findings=[Finding(severity=FindingSeverity.ERROR, message="test failed")],
    )


def _escalate_result() -> AgentResult:
    return AgentResult(
        completed=False,
        confidence=0.0,
        summary="critical issue",
        action_needed="escalate",
        findings=[Finding(severity=FindingSeverity.CRITICAL, message="SQL injection")],
    )


def _make_orchestrator(
    *results: AgentResult,
    policy: Optional[PolicyEngine] = None,
    max_retries: int = 1,
) -> Orchestrator:
    """Convenience factory: builds orchestrator with mocked dispatcher."""
    engine = policy or _make_policy()
    dispatcher = _make_dispatcher(*results)
    return Orchestrator(
        policy_engine=engine,
        dispatcher=dispatcher,
        scorer=ConfidenceScorer(),
        notifier=NullNotifier(),
        max_retries=max_retries,
    )


def _simple_pipeline(*step_names: str) -> PipelineConfig:
    """Create a linear pipeline with no dependencies."""
    return PipelineConfig(
        steps=[
            StepConfig(name=name, skill=name, agent="claude")
            for name in step_names
        ]
    )


# ---------------------------------------------------------------------------
# _agent_result_to_step_result helper
# ---------------------------------------------------------------------------


class TestAgentResultToStepResult:
    def test_converts_completed_flag(self):
        r = _good_result()
        sr = _agent_result_to_step_result("analyze", r)
        assert sr.step == "analyze"
        assert sr.completed is True
        assert sr.confidence == r.confidence

    def test_converts_findings(self):
        r = AgentResult(
            completed=True,
            confidence=0.7,
            findings=[
                Finding(severity=FindingSeverity.WARNING, message="slow query"),
            ],
        )
        sr = _agent_result_to_step_result("security", r)
        assert len(sr.findings) == 1
        assert sr.findings[0].severity == "warning"
        assert sr.findings[0].message == "slow query"

    def test_converts_action_needed_retry(self):
        from dockcheck.core.confidence import ActionNeeded

        r = _fail_result()
        sr = _agent_result_to_step_result("test", r)
        assert sr.action_needed == ActionNeeded.RETRY

    def test_converts_action_needed_escalate(self):
        from dockcheck.core.confidence import ActionNeeded

        r = _escalate_result()
        sr = _agent_result_to_step_result("security", r)
        assert sr.action_needed == ActionNeeded.ESCALATE

    def test_none_action_needed_maps_to_none(self):
        from dockcheck.core.confidence import ActionNeeded

        r = AgentResult(completed=True, confidence=0.8, action_needed=None)
        sr = _agent_result_to_step_result("x", r)
        assert sr.action_needed == ActionNeeded.NONE


# ---------------------------------------------------------------------------
# Dependency resolution (_resolve_dependencies)
# ---------------------------------------------------------------------------


class TestResolveDependencies:
    @pytest.fixture()
    def engine(self):
        return _make_policy()

    def test_no_dependencies_single_layer(self, engine):
        orch = Orchestrator(policy_engine=engine, notifier=NullNotifier())
        steps = [
            StepConfig(name="a", skill="a"),
            StepConfig(name="b", skill="b"),
        ]
        layers = orch._resolve_dependencies(steps)
        assert len(layers) == 1
        names = {s.name for s in layers[0]}
        assert names == {"a", "b"}

    def test_linear_chain_produces_sequential_layers(self, engine):
        orch = Orchestrator(policy_engine=engine, notifier=NullNotifier())
        steps = [
            StepConfig(name="a", skill="a"),
            StepConfig(name="b", skill="b", depends_on=["a"]),
            StepConfig(name="c", skill="c", depends_on=["b"]),
        ]
        layers = orch._resolve_dependencies(steps)
        assert len(layers) == 3
        assert layers[0][0].name == "a"
        assert layers[1][0].name == "b"
        assert layers[2][0].name == "c"

    def test_diamond_dependency(self, engine):
        """a -> b, a -> c, b+c -> d"""
        orch = Orchestrator(policy_engine=engine, notifier=NullNotifier())
        steps = [
            StepConfig(name="a", skill="a"),
            StepConfig(name="b", skill="b", depends_on=["a"]),
            StepConfig(name="c", skill="c", depends_on=["a"]),
            StepConfig(name="d", skill="d", depends_on=["b", "c"]),
        ]
        layers = orch._resolve_dependencies(steps)
        assert len(layers) == 3
        assert layers[0][0].name == "a"
        layer1_names = {s.name for s in layers[1]}
        assert layer1_names == {"b", "c"}
        assert layers[2][0].name == "d"

    def test_unknown_dependency_raises(self, engine):
        orch = Orchestrator(policy_engine=engine, notifier=NullNotifier())
        steps = [
            StepConfig(name="a", skill="a", depends_on=["nonexistent"]),
        ]
        with pytest.raises(ValueError, match="unknown step 'nonexistent'"):
            orch._resolve_dependencies(steps)

    def test_cyclic_dependency_raises(self, engine):
        orch = Orchestrator(policy_engine=engine, notifier=NullNotifier())
        steps = [
            StepConfig(name="a", skill="a", depends_on=["b"]),
            StepConfig(name="b", skill="b", depends_on=["a"]),
        ]
        with pytest.raises(ValueError, match="Cyclic dependency"):
            orch._resolve_dependencies(steps)

    def test_empty_pipeline(self, engine):
        orch = Orchestrator(policy_engine=engine, notifier=NullNotifier())
        layers = orch._resolve_dependencies([])
        assert layers == []


# ---------------------------------------------------------------------------
# Parallel group helper
# ---------------------------------------------------------------------------


class TestGroupByParallel:
    def test_all_ungrouped_are_singletons(self):
        steps = [
            StepConfig(name="a", skill="a"),
            StepConfig(name="b", skill="b"),
        ]
        groups = _group_by_parallel(steps)
        assert len(groups) == 2
        assert groups[0][0].name == "a"
        assert groups[1][0].name == "b"

    def test_shared_group_merged(self):
        steps = [
            StepConfig(name="a", skill="a", parallel_group="grp1"),
            StepConfig(name="b", skill="b", parallel_group="grp1"),
            StepConfig(name="c", skill="c"),
        ]
        groups = _group_by_parallel(steps)
        # grp1 = [a, b], solo c
        assert len(groups) == 2
        grp_names = {s.name for s in groups[0]}
        assert grp_names == {"a", "b"}
        assert groups[1][0].name == "c"

    def test_multiple_parallel_groups(self):
        steps = [
            StepConfig(name="a", skill="a", parallel_group="g1"),
            StepConfig(name="b", skill="b", parallel_group="g2"),
            StepConfig(name="c", skill="c", parallel_group="g1"),
        ]
        groups = _group_by_parallel(steps)
        # Order preserving: g1=[a,c], g2=[b]
        assert len(groups) == 2
        g1_names = {s.name for s in groups[0]}
        assert g1_names == {"a", "c"}
        g2_names = {s.name for s in groups[1]}
        assert g2_names == {"b"}

    def test_empty_layer(self):
        groups = _group_by_parallel([])
        assert groups == []


# ---------------------------------------------------------------------------
# Decision logic (_make_decision)
# ---------------------------------------------------------------------------


class TestMakeDecision:
    @pytest.fixture()
    def orch(self):
        engine = _make_policy()  # auto_deploy_staging=0.8, notify_human=0.6
        return Orchestrator(policy_engine=engine, notifier=NullNotifier())

    def test_blocked_result_returns_block(self, orch):
        pr = PipelineResult(success=False, blocked=True, confidence=0.95)
        assert orch._make_decision(pr) == "block"

    def test_high_confidence_returns_deploy(self, orch):
        pr = PipelineResult(success=True, blocked=False, confidence=0.85)
        assert orch._make_decision(pr) == "deploy"

    def test_at_staging_threshold_returns_deploy(self, orch):
        pr = PipelineResult(success=True, blocked=False, confidence=0.80)
        assert orch._make_decision(pr) == "deploy"

    def test_between_notify_and_staging_returns_notify(self, orch):
        pr = PipelineResult(success=True, blocked=False, confidence=0.70)
        assert orch._make_decision(pr) == "notify"

    def test_at_notify_threshold_returns_notify(self, orch):
        pr = PipelineResult(success=True, blocked=False, confidence=0.60)
        assert orch._make_decision(pr) == "notify"

    def test_below_notify_threshold_returns_block(self, orch):
        pr = PipelineResult(success=True, blocked=False, confidence=0.50)
        assert orch._make_decision(pr) == "block"

    def test_zero_confidence_returns_block(self, orch):
        pr = PipelineResult(success=True, blocked=False, confidence=0.0)
        assert orch._make_decision(pr) == "block"


# ---------------------------------------------------------------------------
# Full pipeline execution
# ---------------------------------------------------------------------------


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_single_step_success_deploys(self):
        orch = _make_orchestrator(_good_result(confidence=0.9))
        pipeline = _simple_pipeline("analyze")
        result = await orch.run_pipeline(pipeline)
        assert result.success is True
        assert result.blocked is False
        assert "analyze" in result.step_results

    @pytest.mark.asyncio
    async def test_low_confidence_does_not_deploy(self):
        orch = _make_orchestrator(_good_result(confidence=0.4))
        pipeline = _simple_pipeline("analyze")
        result = await orch.run_pipeline(pipeline)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_mid_confidence_triggers_notify(self):
        # confidence=0.7 is between notify_human(0.6) and auto_deploy_staging(0.8)
        orch = _make_orchestrator(_good_result(confidence=0.7))
        pipeline = _simple_pipeline("analyze")
        result = await orch.run_pipeline(pipeline)
        assert result.success is False
        assert result.blocked is False
        assert result.block_reasons  # reason explaining review needed

    @pytest.mark.asyncio
    async def test_escalation_blocks_pipeline(self):
        orch = _make_orchestrator(_escalate_result())
        pipeline = _simple_pipeline("security")
        result = await orch.run_pipeline(pipeline)
        assert result.success is False
        assert result.blocked is True
        assert any("security" in r or "escalated" in r for r in result.block_reasons)

    @pytest.mark.asyncio
    async def test_multi_step_linear_all_pass(self):
        orch = _make_orchestrator(
            _good_result(confidence=0.9, summary="analyze ok"),
            _good_result(confidence=0.88, summary="test ok"),
        )
        pipeline = PipelineConfig(
            steps=[
                StepConfig(name="analyze", skill="analyze"),
                StepConfig(name="test", skill="test", depends_on=["analyze"]),
            ]
        )
        result = await orch.run_pipeline(pipeline)
        assert result.success is True
        assert "analyze" in result.step_results
        assert "test" in result.step_results

    @pytest.mark.asyncio
    async def test_step_results_keyed_by_name(self):
        orch = _make_orchestrator(
            _good_result(confidence=0.9, summary="step1"),
            _good_result(confidence=0.85, summary="step2"),
        )
        pipeline = _simple_pipeline("step1", "step2")
        result = await orch.run_pipeline(pipeline)
        assert result.step_results["step1"].summary == "step1"
        assert result.step_results["step2"].summary == "step2"

    @pytest.mark.asyncio
    async def test_policy_blocks_step_on_dangerous_command(self):
        engine = _make_policy()
        dispatcher = AgentDispatcher()
        # dispatch should never be called because policy blocks first
        dispatcher.dispatch = AsyncMock(return_value=_good_result())  # type: ignore[method-assign]

        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=NullNotifier(),
        )
        pipeline = _simple_pipeline("analyze")
        # Inject dangerous command into context.
        result = await orch.run_pipeline(
            pipeline, context={"commands": ["rm -rf /var/data"]}
        )
        assert result.blocked is True
        # dispatch should NOT have been called since policy pre-check blocks it.
        dispatcher.dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_pipeline_succeeds_with_zero_confidence(self):
        orch = _make_orchestrator()
        result = await orch.run_pipeline(PipelineConfig(steps=[]))
        # No steps → scorer returns 0.0 → below notify_human → block
        assert result.success is False
        assert result.blocked is True

    @pytest.mark.asyncio
    async def test_critical_finding_blocks_deployment(self):
        """A step result with CRITICAL finding should block via confidence scorer."""
        critical_result = AgentResult(
            completed=True,
            confidence=0.0,
            summary="vuln found",
            action_needed="none",  # not escalate — let scorer handle it
            findings=[
                Finding(severity=FindingSeverity.CRITICAL, message="RCE vulnerability")
            ],
        )
        orch = _make_orchestrator(critical_result)
        pipeline = _simple_pipeline("security")
        result = await orch.run_pipeline(pipeline)
        assert result.success is False


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retries_on_retry_action(self):
        """If a step returns action_needed='retry', dispatch is called again."""
        engine = _make_policy()
        call_count = 0

        async def fake_dispatch(agent, prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _fail_result()
            return _good_result(confidence=0.9)

        dispatcher = AgentDispatcher()
        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=NullNotifier(),
            max_retries=1,
        )
        pipeline = _simple_pipeline("test")
        result = await orch.run_pipeline(pipeline)
        assert call_count == 2
        assert result.success is True

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_returns_last_result(self):
        """When max_retries exhausted, the last (failing) result is kept."""
        engine = _make_policy()
        call_count = 0

        async def fake_dispatch(agent, prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            return _fail_result(confidence=0.3)

        dispatcher = AgentDispatcher()
        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=NullNotifier(),
            max_retries=2,
        )
        pipeline = _simple_pipeline("test")
        result = await orch.run_pipeline(pipeline)
        # 1 initial + 2 retries = 3 calls
        assert call_count == 3
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_retry_when_action_is_none(self):
        """Steps with action_needed='none' must not trigger a retry."""
        engine = _make_policy()
        call_count = 0

        async def fake_dispatch(agent, prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            return _good_result(confidence=0.9)

        dispatcher = AgentDispatcher()
        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=NullNotifier(),
            max_retries=3,
        )
        pipeline = _simple_pipeline("analyze")
        await orch.run_pipeline(pipeline)
        assert call_count == 1  # No retry.


# ---------------------------------------------------------------------------
# Parallel group execution
# ---------------------------------------------------------------------------


class TestParallelGroupExecution:
    @pytest.mark.asyncio
    async def test_parallel_steps_all_executed(self):
        """Steps in the same parallel_group must all be dispatched."""
        engine = _make_policy()
        executed_steps: list[str] = []

        async def fake_dispatch(agent, prompt, **kwargs):
            # Extract step name from the prompt line.
            for line in prompt.splitlines():
                if "performing the '" in line:
                    step_name = line.split("'")[1]
                    executed_steps.append(step_name)
                    break
            return _good_result(confidence=0.88)

        dispatcher = AgentDispatcher()
        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        pipeline = PipelineConfig(
            steps=[
                StepConfig(name="lint", skill="lint", parallel_group="checks"),
                StepConfig(name="typecheck", skill="typecheck", parallel_group="checks"),
                StepConfig(name="test", skill="test"),  # sequential
            ]
        )
        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=NullNotifier(),
        )
        result = await orch.run_pipeline(pipeline)
        assert result.success is True
        assert "lint" in result.step_results
        assert "typecheck" in result.step_results
        assert "test" in result.step_results

    @pytest.mark.asyncio
    async def test_parallel_group_escalation_stops_pipeline(self):
        """If any step in a parallel group escalates, the pipeline is blocked."""
        engine = _make_policy()
        call_results = {
            "lint": _good_result(confidence=0.9),
            "security": _escalate_result(),
        }

        async def fake_dispatch(agent, prompt, **kwargs):
            for line in prompt.splitlines():
                if "performing the '" in line:
                    step_name = line.split("'")[1]
                    return call_results.get(step_name, _good_result())
            return _good_result()

        dispatcher = AgentDispatcher()
        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        pipeline = PipelineConfig(
            steps=[
                StepConfig(name="lint", skill="lint", parallel_group="g1"),
                StepConfig(name="security", skill="security", parallel_group="g1"),
            ]
        )
        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=NullNotifier(),
        )
        result = await orch.run_pipeline(pipeline)
        assert result.blocked is True


# ---------------------------------------------------------------------------
# Notifier integration
# ---------------------------------------------------------------------------


class TestNotifier:
    def test_null_notifier_does_not_raise(self):
        n = NullNotifier()
        n.notify("deploy", "deployed!")
        n.notify("block", "blocked", context={"reason": "policy"})

    def test_stdout_notifier_prints(self, capsys):
        n = StdoutNotifier()
        n.notify("deploy", "all good", context={"confidence": 0.9})
        out = capsys.readouterr().out
        assert "DEPLOY" in out
        assert "all good" in out

    def test_stdout_notifier_without_context(self, capsys):
        n = StdoutNotifier()
        n.notify("block", "blocked")
        out = capsys.readouterr().out
        assert "BLOCK" in out

    @pytest.mark.asyncio
    async def test_orchestrator_calls_notifier_on_deploy(self):
        notifier = MagicMock(spec=NullNotifier)
        engine = _make_policy()
        dispatcher = _make_dispatcher(_good_result(confidence=0.9))
        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=notifier,
        )
        pipeline = _simple_pipeline("analyze")
        await orch.run_pipeline(pipeline)
        notifier.notify.assert_called()
        # Last call should be deploy.
        last_event = notifier.notify.call_args_list[-1][0][0]
        assert last_event == "deploy"

    @pytest.mark.asyncio
    async def test_orchestrator_calls_notifier_on_block(self):
        notifier = MagicMock(spec=NullNotifier)
        engine = _make_policy()
        dispatcher = _make_dispatcher(_escalate_result())
        orch = Orchestrator(
            policy_engine=engine,
            dispatcher=dispatcher,
            notifier=notifier,
        )
        pipeline = _simple_pipeline("security")
        await orch.run_pipeline(pipeline)
        events = [call[0][0] for call in notifier.notify.call_args_list]
        assert "block" in events
