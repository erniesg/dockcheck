"""Confidence scoring — aggregate agent results into a 0-1 score."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ActionNeeded(str, Enum):
    NONE = "none"
    RETRY = "retry"
    ESCALATE = "escalate"


class Finding(BaseModel):
    severity: str  # "info", "warning", "error", "critical"
    message: str
    file_path: str | None = None
    line: int | None = None


class AgentStepResult(BaseModel):
    """Result from a single agent step (analyze, test, verify, etc.)."""

    step: str
    completed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    turns_used: int = 0
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    action_needed: ActionNeeded = ActionNeeded.NONE


class ConfidenceScore(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    step_scores: dict[str, float] = Field(default_factory=dict)
    has_critical: bool = False
    has_errors: bool = False
    incomplete_steps: list[str] = Field(default_factory=list)


# Default weights for each step type
DEFAULT_WEIGHTS: dict[str, float] = {
    "analyze": 0.25,
    "test": 0.35,
    "security": 0.20,
    "verify": 0.20,
}


class ConfidenceScorer:
    """Aggregates results from multiple agent steps into a single confidence score."""

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or DEFAULT_WEIGHTS

    def score(self, results: list[AgentStepResult]) -> ConfidenceScore:
        if not results:
            return ConfidenceScore(
                score=0.0,
                reason="No agent results to score.",
            )

        step_scores: dict[str, float] = {}
        incomplete_steps: list[str] = []
        has_critical = False
        has_errors = False

        for result in results:
            step_scores[result.step] = result.confidence

            if not result.completed:
                incomplete_steps.append(result.step)

            for finding in result.findings:
                if finding.severity == "critical":
                    has_critical = True
                if finding.severity == "error":
                    has_errors = True

        # Critical findings force score to 0
        if has_critical:
            return ConfidenceScore(
                score=0.0,
                reason="Critical finding detected — deployment blocked.",
                step_scores=step_scores,
                has_critical=True,
                has_errors=has_errors,
                incomplete_steps=incomplete_steps,
            )

        # Compute weighted average
        total_weight = 0.0
        weighted_sum = 0.0

        for step, score_val in step_scores.items():
            weight = self.weights.get(step, 0.1)  # default weight for unknown steps
            weighted_sum += score_val * weight
            total_weight += weight

        if total_weight == 0:
            raw_score = 0.0
        else:
            raw_score = weighted_sum / total_weight

        # Penalize for incomplete steps
        if incomplete_steps:
            penalty = 0.1 * len(incomplete_steps)
            raw_score = max(0.0, raw_score - penalty)

        # Penalize for errors (but don't zero out)
        if has_errors:
            raw_score *= 0.8

        final_score = round(min(1.0, max(0.0, raw_score)), 4)

        # Build reason
        reasons: list[str] = []
        if incomplete_steps:
            reasons.append(f"Incomplete steps: {', '.join(incomplete_steps)}")
        if has_errors:
            reasons.append("Non-critical errors detected (20% penalty)")
        if not reasons:
            reasons.append(f"All steps completed. Weighted score: {final_score}")

        return ConfidenceScore(
            score=final_score,
            reason="; ".join(reasons),
            step_scores=step_scores,
            has_critical=has_critical,
            has_errors=has_errors,
            incomplete_steps=incomplete_steps,
        )
