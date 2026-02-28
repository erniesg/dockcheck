"""Pydantic models for structured agent output and pipeline configuration."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Finding(BaseModel):
    severity: FindingSeverity
    message: str
    file_path: str | None = None
    line: int | None = None


class AgentResult(BaseModel):
    """Result returned by a single agent invocation."""

    completed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    turns_used: int = 0
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    action_needed: Literal["none", "retry", "escalate"] | None = "none"


class StepConfig(BaseModel):
    """Configuration for a single pipeline step."""

    name: str
    skill: str  # skill directory name
    agent: str = "claude"  # "claude" or "codex"
    max_turns: int = 10
    timeout: int = 300
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None  # steps in same group run in parallel


class PipelineConfig(BaseModel):
    """Top-level pipeline definition — ordered list of steps."""

    steps: list[StepConfig]


class PipelineResult(BaseModel):
    """Aggregated result from a full pipeline run."""

    success: bool
    confidence: float = 0.0
    step_results: dict[str, AgentResult] = Field(default_factory=dict)
    blocked: bool = False
    block_reasons: list[str] = Field(default_factory=list)
