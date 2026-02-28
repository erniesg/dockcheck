"""Subprocess dispatch for Claude Code headless and Codex CLI agents."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from dockcheck.agents.schemas import AgentResult, Finding, FindingSeverity

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    """Raised when agent dispatch fails with a non-recoverable error."""


def _parse_claude_output(raw: str, stderr: str = "") -> AgentResult:
    """Parse the JSON output from ``claude -p --output-format json``.

    Claude Code's ``--output-format json`` wraps the model's response in an
    envelope like::

        {"type": "result", "result": "<model text>", ...}

    We attempt to JSON-parse the inner ``result`` value first.  If that fails
    we fall back to treating the full output as a plain-text summary and
    synthesising a minimal ``AgentResult`` with a best-effort confidence.
    """
    raw = raw.strip()
    if not raw:
        return AgentResult(
            completed=False,
            confidence=0.0,
            summary=f"Empty response from agent. stderr={stderr[:200]}",
        )

    # --- Try to parse outer envelope -----------------------------------------
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        # Raw output is not JSON at all — treat as plain summary text.
        return AgentResult(
            completed=True,
            confidence=0.5,
            summary=raw[:500],
        )

    # If the envelope *is* an AgentResult-shaped dict, use it directly.
    if isinstance(envelope, dict) and "completed" in envelope:
        try:
            return AgentResult.model_validate(envelope)
        except Exception:
            pass

    # Extract the inner result text from the Claude envelope.
    inner_text: str = ""
    if isinstance(envelope, dict):
        inner_text = str(envelope.get("result", envelope.get("content", "")))
    else:
        inner_text = raw

    # Try to parse inner_text as JSON (agent was instructed to reply in JSON).
    inner_text = inner_text.strip()
    # Strip markdown code fences if present.
    if inner_text.startswith("```"):
        lines = inner_text.splitlines()
        # Remove opening fence line and any trailing fence.
        inner_lines = []
        in_fence = False
        for line in lines:
            if line.startswith("```") and not in_fence:
                in_fence = True
                continue
            if line.startswith("```") and in_fence:
                break
            if in_fence:
                inner_lines.append(line)
        inner_text = "\n".join(inner_lines).strip()

    try:
        parsed = json.loads(inner_text)
        if isinstance(parsed, dict) and "completed" in parsed:
            return AgentResult.model_validate(parsed)
    except (json.JSONDecodeError, Exception):
        pass

    # Last-resort: wrap inner_text as a plain summary.
    return AgentResult(
        completed=True,
        confidence=0.5,
        summary=inner_text[:500] or raw[:500],
    )


def _parse_codex_output(raw: str, stderr: str = "") -> AgentResult:
    """Parse output from ``codex --quiet``.

    Codex prints its response to stdout; with ``--quiet`` it suppresses
    interactive UI.  We attempt JSON parsing first (if the agent was prompted
    to reply in JSON), then fall back to a plain-text summary.
    """
    raw = raw.strip()
    if not raw:
        return AgentResult(
            completed=False,
            confidence=0.0,
            summary=f"Empty response from codex. stderr={stderr[:200]}",
        )

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "completed" in parsed:
            return AgentResult.model_validate(parsed)
    except (json.JSONDecodeError, Exception):
        pass

    return AgentResult(
        completed=True,
        confidence=0.5,
        summary=raw[:500],
    )


class AgentDispatcher:
    """Dispatches agent calls via Claude Code CLI or Codex CLI subprocesses."""

    # ------------------------------------------------------------------
    # Claude
    # ------------------------------------------------------------------

    async def dispatch_claude(
        self,
        prompt: str,
        system_prompt: str = "",
        max_turns: int = 10,
        timeout: int = 300,
        output_format: str = "json",
    ) -> AgentResult:
        """Run ``claude -p <prompt> --output-format json --max-turns N``.

        Args:
            prompt: The user prompt to pass to Claude Code.
            system_prompt: Optional system-level instructions prepended via
                ``--system-prompt``.
            max_turns: Maximum agentic loop turns (``--max-turns``).
            timeout: Wall-clock timeout in seconds before the subprocess is
                killed and a ``DispatchError`` is raised.
            output_format: Passed to ``--output-format`` (default ``json``).

        Returns:
            An :class:`AgentResult` parsed from the subprocess output.

        Raises:
            DispatchError: If the subprocess times out or exits with a
                non-zero exit code that we cannot recover from.
        """
        cmd = [
            "claude",
            "--print",
            "--output-format",
            output_format,
            "--max-turns",
            str(max_turns),
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        cmd.append(prompt)

        logger.debug("Dispatching Claude: %s", " ".join(cmd[:6]) + " ...")
        return await self._run_subprocess(
            cmd,
            timeout=timeout,
            parser=_parse_claude_output,
            agent_name="claude",
        )

    # ------------------------------------------------------------------
    # Codex
    # ------------------------------------------------------------------

    async def dispatch_codex(
        self,
        prompt: str,
        approval_mode: str = "full-auto",
        timeout: int = 300,
    ) -> AgentResult:
        """Run ``codex --quiet --approval-mode <mode> <prompt>``.

        Args:
            prompt: The task description / prompt for Codex.
            approval_mode: Approval level — ``"full-auto"``, ``"auto-edit"``,
                or ``"suggest"``.
            timeout: Wall-clock timeout in seconds.

        Returns:
            An :class:`AgentResult` parsed from the subprocess output.

        Raises:
            DispatchError: On timeout or unrecoverable subprocess failure.
        """
        cmd = [
            "codex",
            "--quiet",
            "--approval-mode",
            approval_mode,
            prompt,
        ]

        logger.debug("Dispatching Codex: %s", " ".join(cmd[:4]) + " ...")
        return await self._run_subprocess(
            cmd,
            timeout=timeout,
            parser=_parse_codex_output,
            agent_name="codex",
        )

    # ------------------------------------------------------------------
    # Unified entry point
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        agent: str,
        prompt: str,
        **kwargs: Any,
    ) -> AgentResult:
        """Dispatch to the appropriate agent by name.

        Args:
            agent: Either ``"claude"`` or ``"codex"``.
            prompt: The prompt to pass to the agent.
            **kwargs: Forwarded to the agent-specific dispatch method.

        Raises:
            DispatchError: If ``agent`` is not a known agent name.
        """
        if agent == "claude":
            return await self.dispatch_claude(prompt, **kwargs)
        if agent == "codex":
            return await self.dispatch_codex(prompt, **kwargs)
        raise DispatchError(
            f"Unknown agent: '{agent}'. Valid agents are 'claude' and 'codex'."
        )

    # ------------------------------------------------------------------
    # Parallel dispatch
    # ------------------------------------------------------------------

    async def dispatch_parallel(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[AgentResult]:
        """Run multiple agent calls in parallel using :func:`asyncio.gather`.

        Each element of ``tasks`` must be a dict with at minimum an ``"agent"``
        key and a ``"prompt"`` key.  All other keys are forwarded as keyword
        arguments to :meth:`dispatch`.

        Args:
            tasks: List of task dicts, each describing one agent call.

        Returns:
            List of :class:`AgentResult` in the same order as ``tasks``.
        """
        coroutines = []
        for task in tasks:
            task = dict(task)  # defensive copy
            agent = task.pop("agent", "claude")
            prompt = task.pop("prompt", "")
            coroutines.append(self.dispatch(agent, prompt, **task))

        results: list[AgentResult] = await asyncio.gather(
            *coroutines, return_exceptions=False
        )
        return list(results)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_subprocess(
        self,
        cmd: list[str],
        timeout: int,
        parser: Any,
        agent_name: str,
    ) -> AgentResult:
        """Create and await a subprocess, applying timeout and error handling.

        Args:
            cmd: The full command + args list.
            timeout: Seconds before the process is killed.
            parser: Callable ``(stdout: str, stderr: str) -> AgentResult``.
            agent_name: Human-readable name used in error messages.

        Returns:
            Parsed :class:`AgentResult`.

        Raises:
            DispatchError: On timeout or non-zero exit code with empty output.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(timeout),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise DispatchError(
                    f"{agent_name} timed out after {timeout}s. "
                    f"Command: {' '.join(cmd[:3])}..."
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if proc.returncode != 0 and not stdout.strip():
                raise DispatchError(
                    f"{agent_name} exited with code {proc.returncode}. "
                    f"stderr={stderr[:300]}"
                )

            logger.debug(
                "%s returncode=%s stdout_len=%d",
                agent_name,
                proc.returncode,
                len(stdout),
            )
            return parser(stdout, stderr)

        except DispatchError:
            raise
        except FileNotFoundError:
            raise DispatchError(
                f"{agent_name} CLI not found. "
                f"Ensure '{cmd[0]}' is installed and on PATH."
            )
        except Exception as exc:
            raise DispatchError(
                f"Unexpected error dispatching {agent_name}: {exc}"
            ) from exc
