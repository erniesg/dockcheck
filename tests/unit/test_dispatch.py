"""Tests for AgentDispatcher — subprocess dispatch, CLI arg verification,
JSON parsing, timeout handling, and parallel dispatch."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dockcheck.agents.dispatch import (
    AgentDispatcher,
    DispatchError,
    _parse_claude_output,
    _parse_codex_output,
)
from dockcheck.agents.schemas import AgentResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    timeout: bool = False,
) -> MagicMock:
    """Build a mock asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    if timeout:
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _good_result_bytes(**kwargs) -> bytes:
    data = {
        "completed": True,
        "confidence": 0.9,
        "turns_used": 3,
        "summary": "All good.",
        "findings": [],
        "action_needed": "none",
        **kwargs,
    }
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# Unit tests for parser helpers
# ---------------------------------------------------------------------------


class TestParseClaudeOutput:
    def test_empty_string_returns_incomplete(self):
        result = _parse_claude_output("")
        assert result.completed is False
        assert result.confidence == 0.0
        assert "Empty" in result.summary

    def test_direct_agent_result_json(self):
        """Flat JSON that IS an AgentResult should be parsed directly."""
        payload = _good_result_bytes(summary="direct")
        result = _parse_claude_output(payload.decode())
        assert result.completed is True
        assert result.confidence == 0.9
        assert result.summary == "direct"

    def test_claude_envelope_with_result_field(self):
        """Claude SDK wraps the model text in {'type':'result','result':'...'}."""
        inner = json.dumps({
            "completed": True,
            "confidence": 0.85,
            "turns_used": 2,
            "summary": "enveloped",
            "findings": [],
            "action_needed": "none",
        })
        envelope = json.dumps({"type": "result", "result": inner})
        result = _parse_claude_output(envelope)
        assert result.completed is True
        assert result.confidence == 0.85
        assert result.summary == "enveloped"

    def test_plain_text_falls_back_gracefully(self):
        result = _parse_claude_output("No JSON here, just prose.")
        assert result.completed is True
        assert result.confidence == 0.5
        assert "No JSON here" in result.summary

    def test_markdown_code_fence_stripped(self):
        inner = json.dumps({
            "completed": True,
            "confidence": 0.7,
            "turns_used": 1,
            "summary": "fenced",
            "findings": [],
            "action_needed": "none",
        })
        fenced = f"```json\n{inner}\n```"
        envelope = json.dumps({"type": "result", "result": fenced})
        result = _parse_claude_output(envelope)
        assert result.summary == "fenced"
        assert result.confidence == 0.7

    def test_invalid_json_falls_back_to_plain(self):
        result = _parse_claude_output("{not valid json")
        assert result.completed is True
        assert result.confidence == 0.5

    def test_findings_parsed_correctly(self):
        payload = {
            "completed": True,
            "confidence": 0.6,
            "turns_used": 4,
            "summary": "has findings",
            "findings": [
                {"severity": "warning", "message": "slow query", "file_path": "db.py", "line": 10}
            ],
            "action_needed": "none",
        }
        result = _parse_claude_output(json.dumps(payload))
        assert len(result.findings) == 1
        assert result.findings[0].severity.value == "warning"
        assert result.findings[0].message == "slow query"

    def test_stderr_included_in_empty_summary(self):
        result = _parse_claude_output("", stderr="something went wrong")
        assert "something went wrong" in result.summary


class TestParseCodexOutput:
    def test_empty_string_returns_incomplete(self):
        result = _parse_codex_output("")
        assert result.completed is False
        assert result.confidence == 0.0

    def test_valid_json_parsed(self):
        payload = _good_result_bytes(summary="codex done")
        result = _parse_codex_output(payload.decode())
        assert result.completed is True
        assert result.summary == "codex done"

    def test_plain_text_fallback(self):
        result = _parse_codex_output("Task completed successfully.")
        assert result.completed is True
        assert result.confidence == 0.5

    def test_stderr_in_empty_summary(self):
        result = _parse_codex_output("", stderr="codex error")
        assert "codex error" in result.summary


# ---------------------------------------------------------------------------
# AgentDispatcher — subprocess interaction (mocked)
# ---------------------------------------------------------------------------


class TestAgentDispatcherClaude:
    @pytest.fixture()
    def dispatcher(self):
        return AgentDispatcher()

    @pytest.mark.asyncio
    async def test_dispatch_claude_success(self, dispatcher):
        stdout = _good_result_bytes(summary="claude ran")
        proc = _make_proc(returncode=0, stdout=stdout)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await dispatcher.dispatch_claude("do the thing", max_turns=5)

        assert result.completed is True
        assert result.summary == "claude ran"

        # Verify CLI arguments.
        call_args = list(mock_exec.call_args[0])
        assert call_args[0] == "claude"
        assert "--print" in call_args
        assert "--output-format" in call_args
        idx = call_args.index("--output-format")
        assert call_args[idx + 1] == "json"
        assert "--max-turns" in call_args
        idx = call_args.index("--max-turns")
        assert call_args[idx + 1] == "5"

    @pytest.mark.asyncio
    async def test_dispatch_claude_with_system_prompt(self, dispatcher):
        stdout = _good_result_bytes()
        proc = _make_proc(returncode=0, stdout=stdout)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await dispatcher.dispatch_claude("prompt", system_prompt="be strict")

        call_args = list(mock_exec.call_args[0])
        assert "--system-prompt" in call_args
        idx = call_args.index("--system-prompt")
        assert call_args[idx + 1] == "be strict"

    @pytest.mark.asyncio
    async def test_dispatch_claude_timeout(self, dispatcher):
        # Patch wait_for to raise TimeoutError.
        # proc.communicate is called AFTER kill() in the except block —
        # it must return cleanly to allow the DispatchError to propagate.
        proc = MagicMock()
        proc.returncode = -9
        proc.kill = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                with pytest.raises(DispatchError, match="timed out"):
                    await dispatcher.dispatch_claude("run", timeout=1)

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_claude_nonzero_exit_no_output(self, dispatcher):
        proc = _make_proc(returncode=1, stdout=b"", stderr=b"error occurred")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(DispatchError, match="exited with code 1"):
                await dispatcher.dispatch_claude("run")

    @pytest.mark.asyncio
    async def test_dispatch_claude_nonzero_exit_with_output(self, dispatcher):
        """Non-zero exit is tolerated if stdout has parseable content."""
        stdout = _good_result_bytes(summary="partial result")
        proc = _make_proc(returncode=1, stdout=stdout)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await dispatcher.dispatch_claude("run")

        assert result.summary == "partial result"

    @pytest.mark.asyncio
    async def test_dispatch_claude_not_found(self, dispatcher):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("claude not found"),
        ):
            with pytest.raises(DispatchError, match="not found"):
                await dispatcher.dispatch_claude("run")


class TestAgentDispatcherCodex:
    @pytest.fixture()
    def dispatcher(self):
        return AgentDispatcher()

    @pytest.mark.asyncio
    async def test_dispatch_codex_success(self, dispatcher):
        stdout = _good_result_bytes(summary="codex finished")
        proc = _make_proc(returncode=0, stdout=stdout)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await dispatcher.dispatch_codex("fix the bug")

        assert result.completed is True
        assert result.summary == "codex finished"

        call_args = list(mock_exec.call_args[0])
        assert call_args[0] == "codex"
        assert "--quiet" in call_args
        assert "--approval-mode" in call_args
        idx = call_args.index("--approval-mode")
        assert call_args[idx + 1] == "full-auto"

    @pytest.mark.asyncio
    async def test_dispatch_codex_approval_mode_forwarded(self, dispatcher):
        stdout = _good_result_bytes()
        proc = _make_proc(returncode=0, stdout=stdout)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await dispatcher.dispatch_codex("task", approval_mode="suggest")

        call_args = list(mock_exec.call_args[0])
        idx = call_args.index("--approval-mode")
        assert call_args[idx + 1] == "suggest"

    @pytest.mark.asyncio
    async def test_dispatch_codex_timeout(self, dispatcher):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = _make_proc(returncode=0)
            proc.kill = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = proc

            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                with pytest.raises(DispatchError, match="timed out"):
                    await dispatcher.dispatch_codex("run", timeout=1)

        proc.kill.assert_called_once()


class TestAgentDispatcherUnified:
    @pytest.fixture()
    def dispatcher(self):
        return AgentDispatcher()

    @pytest.mark.asyncio
    async def test_dispatch_routes_claude(self, dispatcher):
        dispatcher.dispatch_claude = AsyncMock(return_value=AgentResult(
            completed=True, confidence=0.8, summary="claude"
        ))
        result = await dispatcher.dispatch("claude", "prompt")
        dispatcher.dispatch_claude.assert_awaited_once_with("prompt")
        assert result.summary == "claude"

    @pytest.mark.asyncio
    async def test_dispatch_routes_codex(self, dispatcher):
        dispatcher.dispatch_codex = AsyncMock(return_value=AgentResult(
            completed=True, confidence=0.7, summary="codex"
        ))
        result = await dispatcher.dispatch("codex", "prompt")
        dispatcher.dispatch_codex.assert_awaited_once_with("prompt")
        assert result.summary == "codex"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_agent_raises(self, dispatcher):
        with pytest.raises(DispatchError, match="Unknown agent"):
            await dispatcher.dispatch("gpt5", "prompt")


class TestAgentDispatcherParallel:
    @pytest.fixture()
    def dispatcher(self):
        return AgentDispatcher()

    @pytest.mark.asyncio
    async def test_dispatch_parallel_runs_all(self, dispatcher):
        results = [
            AgentResult(completed=True, confidence=0.9, summary="a"),
            AgentResult(completed=True, confidence=0.8, summary="b"),
            AgentResult(completed=True, confidence=0.7, summary="c"),
        ]
        call_count = 0

        async def fake_dispatch(agent, prompt, **kwargs):
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            return r

        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        tasks = [
            {"agent": "claude", "prompt": "task A"},
            {"agent": "claude", "prompt": "task B"},
            {"agent": "codex", "prompt": "task C"},
        ]
        parallel_results = await dispatcher.dispatch_parallel(tasks)

        assert len(parallel_results) == 3
        summaries = [r.summary for r in parallel_results]
        assert "a" in summaries
        assert "b" in summaries
        assert "c" in summaries

    @pytest.mark.asyncio
    async def test_dispatch_parallel_preserves_order(self, dispatcher):
        """Results must be returned in the same order as input tasks."""

        async def fake_dispatch(agent, prompt, **kwargs):
            # Simulate variable latency based on task letter.
            delay = 0.01 if prompt == "slow" else 0.001
            await asyncio.sleep(delay)
            return AgentResult(completed=True, confidence=0.5, summary=prompt)

        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        tasks = [
            {"agent": "claude", "prompt": "slow"},
            {"agent": "claude", "prompt": "fast"},
        ]
        results = await dispatcher.dispatch_parallel(tasks)
        assert results[0].summary == "slow"
        assert results[1].summary == "fast"

    @pytest.mark.asyncio
    async def test_dispatch_parallel_empty_list(self, dispatcher):
        results = await dispatcher.dispatch_parallel([])
        assert results == []

    @pytest.mark.asyncio
    async def test_dispatch_parallel_uses_default_agent(self, dispatcher):
        """Tasks missing 'agent' key default to 'claude'."""
        calls = []

        async def fake_dispatch(agent, prompt, **kwargs):
            calls.append(agent)
            return AgentResult(completed=True, confidence=0.8, summary=prompt)

        dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]

        await dispatcher.dispatch_parallel([{"prompt": "no agent specified"}])
        assert calls == ["claude"]
