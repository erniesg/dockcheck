"""Policy engine — parse policy.yaml and evaluate rules."""

from __future__ import annotations

import fnmatch
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCK = "block"


class CommandPattern(BaseModel):
    pattern: str


class CircuitBreakers(BaseModel):
    max_containers: int = 5
    max_cost_per_run_usd: float = 10.0
    max_deploys_per_hour: int = 3
    max_file_deletes_per_turn: int = 10


class HardStops(BaseModel):
    commands: list[CommandPattern] = Field(default_factory=list)
    critical_paths: list[str] = Field(default_factory=list)
    circuit_breakers: CircuitBreakers = Field(default_factory=CircuitBreakers)


class ConfidenceThresholds(BaseModel):
    auto_deploy_staging: float = 0.8
    auto_promote_prod: float = 0.9
    notify_human: float = 0.6


class NotificationChannel(BaseModel):
    type: str
    webhook_url: str | None = None


class Notifications(BaseModel):
    on_deploy: bool = True
    on_block: bool = True
    on_rollback: bool = True
    channels: list[NotificationChannel] = Field(default_factory=lambda: [
        NotificationChannel(type="stdout"),
    ])


class Policy(BaseModel):
    version: str = "1"
    hard_stops: HardStops = Field(default_factory=HardStops)
    confidence_thresholds: ConfidenceThresholds = Field(
        default_factory=ConfidenceThresholds
    )
    notifications: Notifications = Field(default_factory=Notifications)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Policy:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict) -> Policy:
        return cls.model_validate(data)


class EvaluationResult(BaseModel):
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    blocked_commands: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    breaker_violations: list[str] = Field(default_factory=list)


class PolicyEngine:
    """Evaluates diffs, commands, and file paths against a loaded policy."""

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    @classmethod
    def from_yaml(cls, path: str | Path) -> PolicyEngine:
        return cls(Policy.from_yaml(path))

    def evaluate(
        self,
        commands: list[str] | None = None,
        file_paths: list[str] | None = None,
        container_count: int = 0,
        cost_usd: float = 0.0,
        deploys_this_hour: int = 0,
        file_deletes: int = 0,
    ) -> EvaluationResult:
        reasons: list[str] = []
        blocked_commands: list[str] = []
        blocked_paths: list[str] = []
        breaker_violations: list[str] = []

        # Check commands against hard stop patterns
        if commands:
            for cmd in commands:
                for pattern in self.policy.hard_stops.commands:
                    if pattern.pattern in cmd:
                        blocked_commands.append(cmd)
                        reasons.append(
                            f"Hard stop: command '{cmd}' matches "
                            f"blocked pattern '{pattern.pattern}'"
                        )

        # Check file paths against critical paths
        if file_paths:
            for fpath in file_paths:
                for glob_pattern in self.policy.hard_stops.critical_paths:
                    if self._matches_glob(fpath, glob_pattern):
                        blocked_paths.append(fpath)
                        reasons.append(
                            f"Hard stop: path '{fpath}' matches critical pattern '{glob_pattern}'"
                        )

        # Check circuit breakers
        breakers = self.policy.hard_stops.circuit_breakers
        if container_count > breakers.max_containers:
            breaker_violations.append(
                f"Container count {container_count} exceeds max {breakers.max_containers}"
            )
            reasons.append(breaker_violations[-1])

        if cost_usd > breakers.max_cost_per_run_usd:
            breaker_violations.append(
                f"Cost ${cost_usd:.2f} exceeds max ${breakers.max_cost_per_run_usd:.2f}"
            )
            reasons.append(breaker_violations[-1])

        if deploys_this_hour > breakers.max_deploys_per_hour:
            breaker_violations.append(
                f"Deploys this hour ({deploys_this_hour}) "
                f"exceeds max {breakers.max_deploys_per_hour}"
            )
            reasons.append(breaker_violations[-1])

        if file_deletes > breakers.max_file_deletes_per_turn:
            breaker_violations.append(
                f"File deletes ({file_deletes}) exceeds max {breakers.max_file_deletes_per_turn}"
            )
            reasons.append(breaker_violations[-1])

        # Determine verdict
        if blocked_commands or blocked_paths:
            verdict = Verdict.BLOCK
        elif breaker_violations:
            verdict = Verdict.FAIL
        else:
            verdict = Verdict.PASS

        return EvaluationResult(
            verdict=verdict,
            reasons=reasons,
            blocked_commands=blocked_commands,
            blocked_paths=blocked_paths,
            breaker_violations=breaker_violations,
        )

    @staticmethod
    def _matches_glob(file_path: str, pattern: str) -> bool:
        """Match a file path against a glob pattern supporting ** notation."""
        if pattern.startswith("**/"):
            suffix = pattern[3:]
            if fnmatch.fnmatch(file_path, suffix):
                return True
            if fnmatch.fnmatch(file_path, pattern):
                return True
            parts = file_path.replace("\\", "/").split("/")
            for i in range(len(parts)):
                subpath = "/".join(parts[i:])
                if fnmatch.fnmatch(subpath, suffix):
                    return True
            return False
        return fnmatch.fnmatch(file_path, pattern)

    def should_auto_deploy_staging(self, confidence: float) -> bool:
        return confidence >= self.policy.confidence_thresholds.auto_deploy_staging

    def should_auto_promote_prod(self, confidence: float) -> bool:
        return confidence >= self.policy.confidence_thresholds.auto_promote_prod

    def should_notify_human(self, confidence: float) -> bool:
        return confidence < self.policy.confidence_thresholds.notify_human
