"""Tests for multi-agent parallel dispatch and metrics."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from dockcheck.agents.parallel import (
    FanOutResult,
    MetricsCollector,
    ParallelDispatcher,
    ParallelResult,
    ParallelTask,
    RunMetrics,
    TurnTracker,
)
from dockcheck.core.confidence import AgentStepResult, Finding


class TestTurnTracker:
    def test_record_and_retrieve(self):
        tracker = TurnTracker()
        tracker.record("analyze", 5)
        tracker.record("analyze", 7)
        assert tracker.get_history("analyze") == [5, 7]

    def test_empty_history(self):
        tracker = TurnTracker()
        assert tracker.get_history("unknown") == []

    def test_suggested_max_turns_default(self):
        tracker = TurnTracker()
        # Not enough data, returns default
        tracker.record("analyze", 5)
        assert tracker.suggested_max_turns("analyze") == 10

    def test_suggested_max_turns_with_history(self):
        tracker = TurnTracker()
        for val in [4, 6, 8]:
            tracker.record("test", val)
        # avg = 6, 1.5x = 9
        suggested = tracker.suggested_max_turns("test")
        assert suggested == 9

    def test_suggested_max_turns_clamped(self):
        tracker = TurnTracker()
        for _ in range(5):
            tracker.record("slow_step", 40)
        # avg = 40, 1.5x = 60, clamped to 50
        assert tracker.suggested_max_turns("slow_step") == 50

    def test_suggested_max_turns_min_clamp(self):
        tracker = TurnTracker()
        for _ in range(5):
            tracker.record("fast_step", 1)
        # avg = 1, 1.5x = 1.5 -> 1, clamped to min 3
        assert tracker.suggested_max_turns("fast_step") == 3

    def test_get_stats(self):
        tracker = TurnTracker()
        for val in [3, 5, 7]:
            tracker.record("analyze", val)
        stats = tracker.get_stats()
        assert "analyze" in stats
        assert stats["analyze"]["count"] == 3
        assert stats["analyze"]["avg_turns"] == 5.0
        assert stats["analyze"]["min_turns"] == 3
        assert stats["analyze"]["max_turns"] == 7

    def test_get_stats_empty(self):
        tracker = TurnTracker()
        assert tracker.get_stats() == {}


class TestMetricsCollector:
    def test_empty_summary(self):
        collector = MetricsCollector()
        summary = collector.get_summary()
        assert summary["total_runs"] == 0

    def test_record_and_summary(self):
        collector = MetricsCollector()
        collector.record_run(RunMetrics(
            run_id="run-1",
            confidence=0.92,
            deployed=True,
            total_turns=15,
            step_count=3,
            elapsed_seconds=120.0,
        ))
        collector.record_run(RunMetrics(
            run_id="run-2",
            confidence=0.45,
            deployed=False,
            blocked=True,
            total_turns=8,
            step_count=2,
            elapsed_seconds=60.0,
        ))
        summary = collector.get_summary()
        assert summary["total_runs"] == 2
        assert summary["deploy_count"] == 1
        assert summary["block_count"] == 1
        assert summary["deploy_rate"] == 0.5

    def test_turn_tracker_accessible(self):
        collector = MetricsCollector()
        collector.turn_tracker.record("analyze", 5)
        assert collector.turn_tracker.get_history("analyze") == [5]


class TestFanOutResult:
    def test_success_count(self):
        result = FanOutResult(
            results=[
                ParallelResult(
                    task_id="t1",
                    result=AgentStepResult(step="t1", completed=True, confidence=0.9),
                ),
                ParallelResult(
                    task_id="t2",
                    result=AgentStepResult(step="t2", completed=True, confidence=0.8),
                ),
                ParallelResult(task_id="t3", error="timeout"),
            ],
            total_elapsed=10.0,
        )
        assert result.success_count == 2
        assert result.failure_count == 1


class TestParallelDispatcher:
    @pytest.fixture()
    def mock_dispatcher(self):
        dispatcher = MagicMock()

        async def mock_dispatch(**kwargs):
            return MagicMock(
                completed=True,
                confidence=0.9,
                turns_used=5,
                summary="Done",
                findings=[],
            )

        dispatcher.dispatch = mock_dispatch
        return dispatcher

    @pytest.mark.asyncio
    async def test_fan_out_all_succeed(self, mock_dispatcher):
        parallel = ParallelDispatcher(dispatcher=mock_dispatcher)
        tasks = [
            ParallelTask(task_id="security", agent="codex", prompt="scan"),
            ParallelTask(task_id="test", agent="codex", prompt="test"),
        ]
        result = await parallel.fan_out(tasks)
        assert result.all_completed is True
        assert len(result.results) == 2
        assert result.failed_tasks == []

    @pytest.mark.asyncio
    async def test_fan_out_with_failure(self):
        dispatcher = MagicMock()

        call_count = 0

        async def failing_dispatch(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Connection failed")
            return MagicMock(
                completed=True,
                confidence=0.8,
                turns_used=3,
                summary="OK",
                findings=[],
            )

        dispatcher.dispatch = failing_dispatch
        parallel = ParallelDispatcher(dispatcher=dispatcher)
        tasks = [
            ParallelTask(task_id="fail-task", agent="claude", prompt="fail"),
            ParallelTask(task_id="ok-task", agent="claude", prompt="ok"),
        ]
        result = await parallel.fan_out(tasks)
        assert result.all_completed is False
        assert "fail-task" in result.failed_tasks

    @pytest.mark.asyncio
    async def test_fan_out_no_dispatcher(self):
        parallel = ParallelDispatcher(dispatcher=None)
        tasks = [ParallelTask(task_id="t1", agent="claude", prompt="test")]
        result = await parallel.fan_out(tasks)
        assert result.all_completed is False
        assert "t1" in result.failed_tasks

    @pytest.mark.asyncio
    async def test_fan_out_concurrency_limit(self, mock_dispatcher):
        parallel = ParallelDispatcher(dispatcher=mock_dispatcher)
        tasks = [
            ParallelTask(task_id=f"task-{i}", agent="codex", prompt=f"task {i}")
            for i in range(10)
        ]
        result = await parallel.fan_out(tasks, max_concurrent=3)
        assert len(result.results) == 10
        assert result.all_completed is True

    @pytest.mark.asyncio
    async def test_fan_out_services(self, mock_dispatcher):
        parallel = ParallelDispatcher(dispatcher=mock_dispatcher)
        services = [
            {"name": "auth-service", "path": "./services/auth"},
            {"name": "api-gateway", "path": "./services/gateway"},
        ]
        result = await parallel.fan_out_services(
            services,
            prompt_template="Run tests in {path} for {name}",
        )
        assert len(result.results) == 2

    @pytest.mark.asyncio
    async def test_turn_tracking(self, mock_dispatcher):
        parallel = ParallelDispatcher(dispatcher=mock_dispatcher)
        tasks = [
            ParallelTask(task_id="analyze", agent="claude", prompt="analyze"),
        ]
        await parallel.fan_out(tasks)
        history = parallel._turn_tracker.get_history("analyze")
        assert len(history) == 1
        assert history[0] == 5  # from mock
