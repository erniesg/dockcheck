"""Model-agnostic adapter interface for agent CLIs."""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from typing import Optional

from dockcheck.agents.dispatch import AgentDispatcher
from dockcheck.agents.schemas import AgentResult


class AgentAdapter(ABC):
    """Abstract base class for agent adapters.

    Each concrete adapter wraps a specific agent CLI (e.g. ``claude``,
    ``codex``) and provides a uniform :meth:`run` interface plus an
    :meth:`is_available` availability check.
    """

    @abstractmethod
    async def run(
        self,
        prompt: str,
        system_prompt: str = "",
        max_turns: int = 10,
        timeout: int = 300,
    ) -> AgentResult:
        """Execute the agent with the given prompt and return its result.

        Args:
            prompt: The task description / user prompt.
            system_prompt: Optional system-level instructions for the agent.
            max_turns: Maximum number of agentic loop iterations.
            timeout: Wall-clock timeout in seconds before aborting.

        Returns:
            :class:`~dockcheck.agents.schemas.AgentResult` with completion
            status, confidence score, and any findings.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return ``True`` if the agent CLI is installed and on PATH."""
        ...

    def __repr__(self) -> str:
        available = "available" if self.is_available() else "unavailable"
        return f"<{self.__class__.__name__} [{available}]>"


class ClaudeAdapter(AgentAdapter):
    """Adapter for the Claude Code CLI (``claude`` command).

    Wraps :class:`~dockcheck.agents.dispatch.AgentDispatcher` and delegates
    all subprocess concerns to it.
    """

    def __init__(self, dispatcher: Optional[AgentDispatcher] = None) -> None:
        self._dispatcher = dispatcher or AgentDispatcher()

    def is_available(self) -> bool:
        """Return ``True`` if the ``claude`` binary is on PATH."""
        return shutil.which("claude") is not None

    async def run(
        self,
        prompt: str,
        system_prompt: str = "",
        max_turns: int = 10,
        timeout: int = 300,
    ) -> AgentResult:
        """Run Claude Code CLI headlessly and return its parsed result.

        Args:
            prompt: The user prompt / task description.
            system_prompt: Optional system-level instructions.
            max_turns: Maximum agentic turns (``--max-turns``).
            timeout: Seconds before the process is killed.

        Returns:
            Parsed :class:`~dockcheck.agents.schemas.AgentResult`.

        Raises:
            :class:`~dockcheck.agents.dispatch.DispatchError`: On CLI failure.
        """
        return await self._dispatcher.dispatch_claude(
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout=timeout,
        )


class CodexAdapter(AgentAdapter):
    """Adapter for the OpenAI Codex CLI (``codex`` command).

    Wraps :class:`~dockcheck.agents.dispatch.AgentDispatcher` and delegates
    all subprocess concerns to it.
    """

    def __init__(
        self,
        approval_mode: str = "full-auto",
        dispatcher: Optional[AgentDispatcher] = None,
    ) -> None:
        self._approval_mode = approval_mode
        self._dispatcher = dispatcher or AgentDispatcher()

    def is_available(self) -> bool:
        """Return ``True`` if the ``codex`` binary is on PATH."""
        return shutil.which("codex") is not None

    async def run(
        self,
        prompt: str,
        system_prompt: str = "",
        max_turns: int = 10,
        timeout: int = 300,
    ) -> AgentResult:
        """Run Codex CLI in quiet mode and return its parsed result.

        Note: Codex CLI does not accept a ``--max-turns`` flag; the argument
        is accepted for interface compatibility but has no effect.

        Args:
            prompt: The user prompt / task description.
            system_prompt: Ignored — Codex CLI does not support system prompts
                via a CLI flag.
            max_turns: Accepted for API compatibility; not forwarded to Codex.
            timeout: Seconds before the process is killed.

        Returns:
            Parsed :class:`~dockcheck.agents.schemas.AgentResult`.

        Raises:
            :class:`~dockcheck.agents.dispatch.DispatchError`: On CLI failure.
        """
        return await self._dispatcher.dispatch_codex(
            prompt=prompt,
            approval_mode=self._approval_mode,
            timeout=timeout,
        )


def get_adapter(agent: str, **kwargs: object) -> AgentAdapter:
    """Factory function — return the appropriate :class:`AgentAdapter`.

    Args:
        agent: Either ``"claude"`` or ``"codex"``.
        **kwargs: Forwarded to the adapter constructor.

    Returns:
        Concrete :class:`AgentAdapter` instance.

    Raises:
        ValueError: If ``agent`` is not a recognised agent name.
    """
    if agent == "claude":
        return ClaudeAdapter(**kwargs)  # type: ignore[arg-type]
    if agent == "codex":
        return CodexAdapter(**kwargs)  # type: ignore[arg-type]
    raise ValueError(
        f"Unknown agent '{agent}'. Valid choices are 'claude' and 'codex'."
    )
