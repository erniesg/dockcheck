"""Multi-agent parallel dispatch and fan-out/fan-in patterns."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from dockcheck.core.confidence import AgentStepResult, Finding


class ParallelTask(BaseModel):
    """A task to dispatch in parallel."""

    task_id: str
    agent: str = "claude"  # "claude" or "codex"
    prompt: str
    system_prompt: str = ""
    max_turns: int = 10
    timeout: int = 300


class ParallelResult(BaseModel):
    """Result from a parallel dispatch batch."""

    task_id: str
    result: Optional[AgentStepResult] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


class FanOutResult(BaseModel):
    """Aggregated results from a fan-out/fan-in operation."""

    results: list[ParallelResult] = Field(default_factory=list)
    total_elapsed: float = 0.0
    all_completed: bool = False
    failed_tasks: list[str] = Field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.result and r.result.completed)

    @property
    def failure_count(self) -> int:
        return len(self.results) - self.success_count


class TurnTracker:
    """Tracks turn usage per skill for adaptive max_turns tuning."""

    def __init__(self) -> None:
        self._history: dict[str, list[int]] = {}

    def record(self, skill: str, turns_used: int) -> None:
        if skill not in self._history:
            self._history[skill] = []
        self._history[skill].append(turns_used)

    def suggested_max_turns(self, skill: str, default: int = 10) -> int:
        """Suggest max_turns based on historical data for this skill."""
        history = self._history.get(skill, [])
        if len(history) < 3:
            return default
        avg = sum(history) / len(history)
        # Add 50% headroom above historical average
        suggested = int(avg * 1.5)
        return max(3, min(suggested, 50))  # clamp to [3, 50]

    def get_history(self, skill: str) -> list[int]:
        return list(self._history.get(skill, []))

    def get_stats(self) -> dict[str, dict[str, Any]]:
        stats = {}
        for skill, history in self._history.items():
            if history:
                stats[skill] = {
                    "count": len(history),
                    "avg_turns": round(sum(history) / len(history), 1),
                    "min_turns": min(history),
                    "max_turns": max(history),
                    "suggested_max": self.suggested_max_turns(skill),
                }
        return stats


class MetricsCollector:
    """Collects pipeline execution metrics."""

    def __init__(self) -> None:
        self._runs: list[RunMetrics] = []
        self._turn_tracker = TurnTracker()

    @property
    def turn_tracker(self) -> TurnTracker:
        return self._turn_tracker

    def record_run(self, metrics: "RunMetrics") -> None:
        self._runs.append(metrics)

    def get_summary(self) -> dict[str, Any]:
        if not self._runs:
            return {"total_runs": 0}

        confidences = [r.confidence for r in self._runs]
        deploy_count = sum(1 for r in self._runs if r.deployed)
        block_count = sum(1 for r in self._runs if r.blocked)

        return {
            "total_runs": len(self._runs),
            "avg_confidence": round(sum(confidences) / len(confidences), 3),
            "deploy_count": deploy_count,
            "block_count": block_count,
            "deploy_rate": round(deploy_count / len(self._runs), 3),
            "turn_stats": self._turn_tracker.get_stats(),
        }


class RunMetrics(BaseModel):
    """Metrics from a single pipeline run."""

    run_id: str
    timestamp: float = Field(default_factory=time.time)
    confidence: float = 0.0
    deployed: bool = False
    blocked: bool = False
    total_turns: int = 0
    step_count: int = 0
    elapsed_seconds: float = 0.0
    steps: dict[str, float] = Field(default_factory=dict)  # step -> confidence


class ParallelDispatcher:
    """Manages parallel agent dispatch with fan-out/fan-in pattern."""

    def __init__(self, dispatcher: Any = None) -> None:
        """Initialize with an AgentDispatcher instance."""
        self._dispatcher = dispatcher
        self._turn_tracker = TurnTracker()

    async def fan_out(
        self,
        tasks: list[ParallelTask],
        max_concurrent: int = 5,
    ) -> FanOutResult:
        """Dispatch multiple tasks in parallel and collect results."""
        start = time.time()
        semaphore = asyncio.Semaphore(max_concurrent)
        results: list[ParallelResult] = []

        async def _run_task(task: ParallelTask) -> ParallelResult:
            async with semaphore:
                task_start = time.time()
                try:
                    if self._dispatcher is None:
                        raise RuntimeError("No dispatcher configured")

                    result = await self._dispatcher.dispatch(
                        agent=task.agent,
                        prompt=task.prompt,
                        system_prompt=task.system_prompt,
                        max_turns=task.max_turns,
                        timeout=task.timeout,
                    )
                    elapsed = time.time() - task_start

                    # Record turns for adaptive tuning
                    self._turn_tracker.record(task.task_id, result.turns_used)

                    return ParallelResult(
                        task_id=task.task_id,
                        result=AgentStepResult(
                            step=task.task_id,
                            completed=result.completed,
                            confidence=result.confidence,
                            turns_used=result.turns_used,
                            summary=result.summary,
                            findings=[
                                Finding(severity=f.severity, message=f.message)
                                for f in result.findings
                            ],
                        ),
                        elapsed_seconds=elapsed,
                    )
                except Exception as e:
                    elapsed = time.time() - task_start
                    return ParallelResult(
                        task_id=task.task_id,
                        error=str(e),
                        elapsed_seconds=elapsed,
                    )

        gathered = await asyncio.gather(
            *[_run_task(t) for t in tasks],
            return_exceptions=False,
        )
        results = list(gathered)

        total_elapsed = time.time() - start
        failed = [r.task_id for r in results if r.error is not None]
        all_completed = all(
            r.result is not None and r.result.completed
            for r in results
            if r.error is None
        )

        return FanOutResult(
            results=results,
            total_elapsed=total_elapsed,
            all_completed=all_completed and len(failed) == 0,
            failed_tasks=failed,
        )

    async def fan_out_services(
        self,
        services: list[dict[str, str]],
        prompt_template: str,
        agent: str = "codex",
        max_turns: int = 10,
    ) -> FanOutResult:
        """Fan out the same operation across multiple services/repos."""
        tasks = []
        for svc in services:
            prompt = prompt_template.format(**svc)
            tasks.append(
                ParallelTask(
                    task_id=svc.get("name", svc.get("path", "unknown")),
                    agent=agent,
                    prompt=prompt,
                    max_turns=max_turns,
                )
            )
        return await self.fan_out(tasks)
