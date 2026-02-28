"""Pipeline orchestrator — step execution, dependency resolution, and decisions."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from dockcheck.agents.dispatch import AgentDispatcher
from dockcheck.agents.schemas import (
    AgentResult,
    Finding,
    FindingSeverity,
    PipelineConfig,
    PipelineResult,
    StepConfig,
)
from dockcheck.core.confidence import (
    AgentStepResult,
    ConfidenceScorer,
    Finding as CoreFinding,
)
from dockcheck.core.policy import PolicyEngine, Verdict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notifier abstraction (thin — real implementations live in future phases)
# ---------------------------------------------------------------------------


class Notifier(ABC):
    """Abstract notifier — send messages about pipeline events."""

    @abstractmethod
    def notify(self, event: str, message: str, context: Optional[dict] = None) -> None:
        """Send a notification.

        Args:
            event: One of ``"deploy"``, ``"block"``, ``"rollback"``, ``"notify"``.
            message: Human-readable summary of the event.
            context: Optional extra context (step name, confidence, etc.).
        """
        ...


class StdoutNotifier(Notifier):
    """Simple notifier that prints events to stdout."""

    def notify(self, event: str, message: str, context: Optional[dict] = None) -> None:
        ctx_str = f" | {context}" if context else ""
        print(f"[dockcheck/{event.upper()}] {message}{ctx_str}")


class NullNotifier(Notifier):
    """No-op notifier for testing and silent operation."""

    def notify(self, event: str, message: str, context: Optional[dict] = None) -> None:
        pass  # intentionally silent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_result_to_step_result(step_name: str, result: AgentResult) -> AgentStepResult:
    """Convert an :class:`AgentResult` into a :class:`AgentStepResult`.

    The two models are deliberately separate: ``AgentResult`` is the raw
    agent output (from dispatch) while ``AgentStepResult`` is the
    confidence-scorer's view of a pipeline step.
    """
    core_findings: list[CoreFinding] = []
    for f in result.findings:
        core_findings.append(
            CoreFinding(
                severity=f.severity.value,
                message=f.message,
                file_path=f.file_path,
                line=f.line,
            )
        )

    from dockcheck.core.confidence import ActionNeeded

    action_map = {
        "none": ActionNeeded.NONE,
        "retry": ActionNeeded.RETRY,
        "escalate": ActionNeeded.ESCALATE,
        None: ActionNeeded.NONE,
    }
    action_needed = action_map.get(result.action_needed, ActionNeeded.NONE)

    return AgentStepResult(
        step=step_name,
        completed=result.completed,
        confidence=result.confidence,
        turns_used=result.turns_used,
        summary=result.summary,
        findings=core_findings,
        action_needed=action_needed,
    )


# ---------------------------------------------------------------------------
# Module-level helpers (also exposed for direct testing)
# ---------------------------------------------------------------------------


def _group_by_parallel(
    layer: list[StepConfig],
) -> list[list[StepConfig]]:
    """Within a single execution layer, group steps by ``parallel_group``.

    Steps with the same non-None ``parallel_group`` value are placed in the
    same sub-group and run concurrently.  Steps with ``parallel_group=None``
    each form their own singleton sub-group.

    This function is also called by :meth:`Orchestrator._group_by_parallel` to
    allow direct unit testing.

    Args:
        layer: A single execution layer from dependency resolution.

    Returns:
        List of sub-groups (each is a list of steps to run concurrently).
    """
    from collections import OrderedDict

    groups: OrderedDict[str, list[StepConfig]] = OrderedDict()
    for step in layer:
        key = step.parallel_group  # may be None
        if key is None:
            # Each ungrouped step gets its own unique bucket.
            unique_key = f"__solo_{step.name}"
            groups[unique_key] = [step]
        else:
            if key not in groups:
                groups[key] = []
            groups[key].append(step)

    return list(groups.values())


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Executes pipeline steps, aggregates results, and makes deploy decisions.

    The orchestrator is the main coordinator:

    1. Resolves step dependencies with a topological sort.
    2. Executes independent steps in parallel (via :func:`asyncio.gather`).
    3. Checks policy hard stops before each step.
    4. Handles per-step retries on ``action_needed == "retry"``.
    5. Aggregates confidence via :class:`~dockcheck.core.confidence.ConfidenceScorer`.
    6. Calls :meth:`_make_decision` to produce a final ``"deploy"``,
       ``"notify"``, or ``"block"`` verdict.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        dispatcher: Optional[AgentDispatcher] = None,
        scorer: Optional[ConfidenceScorer] = None,
        notifier: Optional[Notifier] = None,
        max_retries: int = 1,
    ) -> None:
        self._policy = policy_engine
        self._dispatcher = dispatcher or AgentDispatcher()
        self._scorer = scorer or ConfidenceScorer()
        self._notifier = notifier or StdoutNotifier()
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_pipeline(
        self,
        pipeline: PipelineConfig,
        context: Optional[dict] = None,
    ) -> PipelineResult:
        """Execute the full pipeline defined by *pipeline*.

        Steps are executed in dependency order.  Steps that share the same
        ``parallel_group`` value (or that have no dependencies between them
        within a layer) run concurrently via :func:`asyncio.gather`.

        Args:
            pipeline: The pipeline definition (steps + config).
            context: Optional key/value context passed into each step's prompt
                builder (reserved for future use).

        Returns:
            :class:`PipelineResult` with per-step results and overall
            confidence + decision.
        """
        ctx = context or {}
        step_results: dict[str, AgentResult] = {}
        block_reasons: list[str] = []

        # Resolve execution layers (topological sort).
        layers = self._resolve_dependencies(pipeline.steps)
        logger.info(
            "Pipeline has %d step(s) across %d execution layer(s).",
            len(pipeline.steps),
            len(layers),
        )

        for layer_idx, layer in enumerate(layers):
            logger.debug(
                "Executing layer %d: %s",
                layer_idx,
                [s.name for s in layer],
            )

            # Group steps within the layer by parallel_group or run as
            # individual tasks.
            groups = self._group_by_parallel(layer)

            for group in groups:
                if len(group) == 1:
                    step = group[0]
                    result = await self._execute_step_with_retry(step, ctx)
                    step_results[step.name] = result

                    if result.action_needed == "escalate":
                        block_reasons.append(
                            f"Step '{step.name}' escalated: {result.summary}"
                        )
                        # Escalation blocks the pipeline immediately.
                        score = self._scorer.score(
                            [
                                _agent_result_to_step_result(name, r)
                                for name, r in step_results.items()
                            ]
                        )
                        self._notifier.notify(
                            "block",
                            f"Pipeline blocked at step '{step.name}'",
                            {"confidence": score.score},
                        )
                        return PipelineResult(
                            success=False,
                            confidence=score.score,
                            step_results=step_results,
                            blocked=True,
                            block_reasons=block_reasons,
                        )
                else:
                    # Parallel group: run all steps concurrently.
                    group_results = await asyncio.gather(
                        *[self._execute_step_with_retry(s, ctx) for s in group],
                        return_exceptions=False,
                    )
                    for step, result in zip(group, group_results):
                        step_results[step.name] = result

                        if result.action_needed == "escalate":
                            block_reasons.append(
                                f"Step '{step.name}' escalated: {result.summary}"
                            )

            # After each layer, check if any escalation occurred.
            if block_reasons:
                score = self._scorer.score(
                    [
                        _agent_result_to_step_result(name, r)
                        for name, r in step_results.items()
                    ]
                )
                self._notifier.notify(
                    "block",
                    f"Pipeline blocked after layer {layer_idx}",
                    {"confidence": score.score},
                )
                return PipelineResult(
                    success=False,
                    confidence=score.score,
                    step_results=step_results,
                    blocked=True,
                    block_reasons=block_reasons,
                )

        # Aggregate confidence across all steps.
        step_result_list = [
            _agent_result_to_step_result(name, r) for name, r in step_results.items()
        ]
        confidence_score = self._scorer.score(step_result_list)
        final_confidence = confidence_score.score

        # Apply hard-stop: critical findings from the scorer zero out confidence.
        if confidence_score.has_critical:
            block_reasons.append("Critical finding detected by confidence scorer.")

        decision = self._make_decision(
            PipelineResult(
                success=not bool(block_reasons),
                confidence=final_confidence,
                step_results=step_results,
                blocked=bool(block_reasons),
                block_reasons=block_reasons,
            )
        )

        if decision == "block":
            self._notifier.notify(
                "block",
                "Pipeline blocked — confidence below threshold or critical finding.",
                {"confidence": final_confidence},
            )
            return PipelineResult(
                success=False,
                confidence=final_confidence,
                step_results=step_results,
                blocked=True,
                block_reasons=block_reasons
                or [f"Confidence {final_confidence:.2f} below deploy threshold."],
            )

        if decision == "notify":
            self._notifier.notify(
                "notify",
                "Pipeline requires human review before deployment.",
                {"confidence": final_confidence},
            )
            return PipelineResult(
                success=False,
                confidence=final_confidence,
                step_results=step_results,
                blocked=False,
                block_reasons=[
                    f"Confidence {final_confidence:.2f} requires human review."
                ],
            )

        # decision == "deploy"
        self._notifier.notify(
            "deploy",
            "Pipeline passed — ready for deployment.",
            {"confidence": final_confidence},
        )
        return PipelineResult(
            success=True,
            confidence=final_confidence,
            step_results=step_results,
            blocked=False,
            block_reasons=[],
        )

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    def _resolve_dependencies(
        self, steps: list[StepConfig]
    ) -> list[list[StepConfig]]:
        """Topological sort — group steps into sequential execution layers.

        Steps with no dependencies form layer 0.  Steps whose dependencies
        are all satisfied in earlier layers form subsequent layers.

        Each returned layer contains steps that *can* run concurrently
        (no intra-layer dependencies).

        Args:
            steps: Flat list of step configs from the pipeline definition.

        Returns:
            List of layers, where each layer is a list of :class:`StepConfig`.

        Raises:
            ValueError: If a step references an unknown dependency name or a
                cyclic dependency is detected.
        """
        name_to_step = {s.name: s for s in steps}

        # Validate all dependency names up-front.
        for step in steps:
            for dep in step.depends_on:
                if dep not in name_to_step:
                    raise ValueError(
                        f"Step '{step.name}' depends on unknown step '{dep}'."
                    )

        completed: set[str] = set()
        remaining = list(steps)
        layers: list[list[StepConfig]] = []

        while remaining:
            # Find all steps whose dependencies are already satisfied.
            ready = [
                s for s in remaining if all(d in completed for d in s.depends_on)
            ]
            if not ready:
                cycle_names = [s.name for s in remaining]
                raise ValueError(
                    f"Cyclic dependency detected among steps: {cycle_names}"
                )

            layers.append(ready)
            for s in ready:
                completed.add(s.name)
                remaining.remove(s)

        return layers

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    async def _execute_step_with_retry(
        self,
        step: StepConfig,
        context: dict,
    ) -> AgentResult:
        """Execute a step with up to ``self._max_retries`` retry attempts.

        A retry is triggered when ``result.action_needed == "retry"``.

        Args:
            step: The step configuration.
            context: Pipeline-level context dict.

        Returns:
            The final :class:`AgentResult` after retries.
        """
        result = await self._execute_step(step, context)
        attempts = 1

        while result.action_needed == "retry" and attempts <= self._max_retries:
            logger.info(
                "Step '%s' requested retry (attempt %d/%d).",
                step.name,
                attempts,
                self._max_retries,
            )
            result = await self._execute_step(step, context)
            attempts += 1

        return result

    async def _execute_step(
        self,
        step: StepConfig,
        context: dict,
    ) -> AgentResult:
        """Execute a single pipeline step.

        Before dispatching the agent, the policy engine is consulted.  If the
        policy engine returns ``Verdict.BLOCK`` (e.g. because of critical-path
        matches in the context), an :class:`AgentResult` with
        ``action_needed="escalate"`` is returned immediately without spawning
        a subprocess.

        Args:
            step: The step to execute.
            context: Shared pipeline context (file paths, commands, etc.).

        Returns:
            :class:`AgentResult` from the agent or from policy pre-check.
        """
        # Pre-check: run policy engine against any commands/files in context.
        commands = context.get("commands", [])
        file_paths = context.get("file_paths", [])

        if commands or file_paths:
            eval_result = self._policy.evaluate(
                commands=commands or None,
                file_paths=file_paths or None,
            )
            if eval_result.verdict == Verdict.BLOCK:
                logger.warning(
                    "Step '%s' blocked by policy: %s",
                    step.name,
                    eval_result.reasons,
                )
                return AgentResult(
                    completed=False,
                    confidence=0.0,
                    summary=f"Blocked by policy: {'; '.join(eval_result.reasons)}",
                    action_needed="escalate",
                    findings=[
                        Finding(
                            severity=FindingSeverity.CRITICAL,
                            message=reason,
                        )
                        for reason in eval_result.reasons
                    ],
                )

        # Build the prompt for the agent.
        prompt = self._build_prompt(step, context)

        logger.info("Executing step '%s' via agent '%s'.", step.name, step.agent)
        try:
            result = await self._dispatcher.dispatch(
                agent=step.agent,
                prompt=prompt,
                max_turns=step.max_turns,
                timeout=step.timeout,
            )
        except Exception as exc:
            logger.error("Step '%s' dispatch failed: %s", step.name, exc)
            return AgentResult(
                completed=False,
                confidence=0.0,
                summary=f"Dispatch error: {exc}",
                action_needed="escalate",
                findings=[
                    Finding(
                        severity=FindingSeverity.ERROR,
                        message=str(exc),
                    )
                ],
            )

        logger.info(
            "Step '%s' completed=%s confidence=%.2f",
            step.name,
            result.completed,
            result.confidence,
        )
        return result

    # ------------------------------------------------------------------
    # Decision logic
    # ------------------------------------------------------------------

    def _make_decision(self, pipeline_result: PipelineResult) -> str:
        """Translate confidence + policy thresholds into a deployment decision.

        Args:
            pipeline_result: The aggregated pipeline result so far.

        Returns:
            One of ``"deploy"``, ``"notify"``, or ``"block"``.

        Decision rules (in priority order):

        1. ``"block"`` — if already blocked (escalation) **or** confidence is
           below the ``notify_human`` threshold.
        2. ``"notify"`` — if confidence is at/above ``notify_human`` but below
           ``auto_deploy_staging``.
        3. ``"deploy"`` — if confidence meets or exceeds ``auto_deploy_staging``.
        """
        if pipeline_result.blocked:
            return "block"

        confidence = pipeline_result.confidence
        thresholds = self._policy.policy.confidence_thresholds

        if confidence < thresholds.notify_human:
            return "block"

        if confidence < thresholds.auto_deploy_staging:
            return "notify"

        return "deploy"

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(step: StepConfig, context: dict) -> str:
        """Build the agent prompt for a given step.

        The prompt includes the skill name and any relevant context so the
        agent knows what to do.  In a full implementation this would load the
        skill's prompt template from disk; here we produce a structured prompt
        that is sufficient for testing.

        Args:
            step: The step configuration.
            context: Pipeline context dict.

        Returns:
            Formatted prompt string.
        """
        lines = [
            f"You are performing the '{step.name}' step of a CI/CD pipeline.",
            f"Skill: {step.skill}",
        ]

        if context.get("diff"):
            lines.append(f"\nDiff:\n{context['diff']}")

        if context.get("file_paths"):
            lines.append(f"\nChanged files: {', '.join(context['file_paths'])}")

        lines.append(
            "\nRespond with a JSON object matching the AgentResult schema: "
            "{ completed, confidence, turns_used, summary, findings, action_needed }"
        )
        return "\n".join(lines)

    @staticmethod
    def _group_by_parallel(
        layer: list[StepConfig],
    ) -> list[list[StepConfig]]:
        """Within a single execution layer, group steps by ``parallel_group``.

        Steps with the same non-None ``parallel_group`` value are placed in the
        same sub-group and run concurrently.  Steps with ``parallel_group=None``
        each form their own singleton sub-group.

        Args:
            layer: A single execution layer from :meth:`_resolve_dependencies`.

        Returns:
            List of sub-groups (each is a list of steps to run concurrently).
        """
        return _group_by_parallel(layer)
