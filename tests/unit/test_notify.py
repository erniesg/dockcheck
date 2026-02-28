"""Tests for Notifier — channel routing, message formatting, failure handling."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from dockcheck.core.policy import NotificationChannel
from dockcheck.tools.notify import (
    NotificationMessage,
    Notifier,
    SendResult,
    _build_slack_payload,
    _format_github_body,
    _format_stdout,
    _SEVERITY_PREFIX,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(
    title: str = "Test Title",
    body: str = "Test body.",
    severity: str = "info",
    metadata: dict | None = None,
) -> NotificationMessage:
    return NotificationMessage(
        title=title,
        body=body,
        severity=severity,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# NotificationMessage schema
# ---------------------------------------------------------------------------

class TestNotificationMessage:
    def test_default_severity_is_info(self):
        msg = NotificationMessage(title="t", body="b")
        assert msg.severity == "info"

    def test_valid_severities(self):
        for sev in ("info", "warning", "error", "critical"):
            msg = NotificationMessage(title="t", body="b", severity=sev)
            assert msg.severity == sev

    def test_invalid_severity_raises(self):
        with pytest.raises(Exception):
            NotificationMessage(title="t", body="b", severity="debug")

    def test_metadata_defaults_to_empty_dict(self):
        msg = NotificationMessage(title="t", body="b")
        assert msg.metadata == {}

    def test_metadata_stored(self):
        msg = NotificationMessage(title="t", body="b", metadata={"key": "value"})
        assert msg.metadata == {"key": "value"}


# ---------------------------------------------------------------------------
# Notifier defaults
# ---------------------------------------------------------------------------

class TestNotifierInit:
    def test_default_channel_is_stdout(self):
        notifier = Notifier()
        assert len(notifier.channels) == 1
        assert notifier.channels[0].type == "stdout"

    def test_custom_channels_accepted(self):
        channels = [
            NotificationChannel(type="stdout"),
            NotificationChannel(type="slack", webhook_url="https://hooks.slack.com/abc"),
        ]
        notifier = Notifier(channels=channels)
        assert len(notifier.channels) == 2


# ---------------------------------------------------------------------------
# send_stdout
# ---------------------------------------------------------------------------

class TestSendStdout:
    def test_send_stdout_returns_success(self, capsys):
        notifier = Notifier()
        result = notifier.send_stdout(_msg())
        assert result.success is True
        assert result.channel == "stdout"

    def test_send_stdout_prints_title(self, capsys):
        notifier = Notifier()
        notifier.send_stdout(_msg(title="Deployment Successful"))
        captured = capsys.readouterr()
        assert "Deployment Successful" in captured.out

    def test_send_stdout_prints_body(self, capsys):
        notifier = Notifier()
        notifier.send_stdout(_msg(body="All tests passed."))
        captured = capsys.readouterr()
        assert "All tests passed." in captured.out

    def test_send_stdout_severity_prefix_info(self, capsys):
        notifier = Notifier()
        notifier.send_stdout(_msg(severity="info"))
        captured = capsys.readouterr()
        assert "[INFO]" in captured.out

    def test_send_stdout_severity_prefix_warning(self, capsys):
        notifier = Notifier()
        notifier.send_stdout(_msg(severity="warning"))
        captured = capsys.readouterr()
        assert "[WARNING]" in captured.out

    def test_send_stdout_severity_prefix_error(self, capsys):
        notifier = Notifier()
        notifier.send_stdout(_msg(severity="error"))
        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out

    def test_send_stdout_severity_prefix_critical(self, capsys):
        notifier = Notifier()
        notifier.send_stdout(_msg(severity="critical"))
        captured = capsys.readouterr()
        assert "[CRITICAL]" in captured.out

    def test_send_stdout_includes_metadata(self, capsys):
        notifier = Notifier()
        notifier.send_stdout(_msg(metadata={"env": "staging", "version": "1.2.3"}))
        captured = capsys.readouterr()
        assert "env" in captured.out
        assert "staging" in captured.out

    def test_send_stdout_result_is_pydantic_model(self, capsys):
        notifier = Notifier()
        result = notifier.send_stdout(_msg())
        assert isinstance(result, SendResult)

    def test_send_stdout_failure_does_not_raise(self):
        notifier = Notifier()
        # Patch print to raise — should not propagate
        with patch("builtins.print", side_effect=OSError("disk full")):
            result = notifier.send_stdout(_msg())
        assert result.success is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# send_slack
# ---------------------------------------------------------------------------

class TestSendSlack:
    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        notifier = Notifier()
        result = notifier.send_slack("https://hooks.slack.com/abc", _msg())

        assert result.success is True
        assert result.channel == "slack"
        mock_post.assert_called_once()

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_posts_to_correct_url(self, mock_post):
        mock_post.return_value = MagicMock(raise_for_status=lambda: None)
        notifier = Notifier()
        webhook = "https://hooks.slack.com/T00/B00/secret"

        notifier.send_slack(webhook, _msg())

        call_args = mock_post.call_args
        assert call_args[0][0] == webhook

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_payload_contains_title(self, mock_post):
        mock_post.return_value = MagicMock(raise_for_status=lambda: None)
        notifier = Notifier()

        notifier.send_slack("https://hooks.slack.com/abc", _msg(title="Deploy Alert"))

        payload = mock_post.call_args[1]["json"]
        # Title should appear somewhere in text or blocks
        payload_str = str(payload)
        assert "Deploy Alert" in payload_str

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_http_error_returns_failure(self, mock_post):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_payload"
        mock_post.return_value = mock_response
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=mock_response
        )

        notifier = Notifier()
        result = notifier.send_slack("https://hooks.slack.com/abc", _msg())

        assert result.success is False
        assert "400" in result.error

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_request_error_returns_failure(self, mock_post):
        import httpx

        mock_post.side_effect = httpx.ConnectError("connection refused")

        notifier = Notifier()
        result = notifier.send_slack("https://hooks.slack.com/abc", _msg())

        assert result.success is False
        assert result.error is not None

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_unexpected_error_returns_failure(self, mock_post):
        mock_post.side_effect = RuntimeError("unexpected")

        notifier = Notifier()
        result = notifier.send_slack("https://hooks.slack.com/abc", _msg())

        assert result.success is False
        assert "unexpected" in result.error

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_result_is_pydantic_model(self, mock_post):
        mock_post.return_value = MagicMock(raise_for_status=lambda: None)
        notifier = Notifier()
        result = notifier.send_slack("https://hooks.slack.com/abc", _msg())
        assert isinstance(result, SendResult)

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_slack_timeout_is_set(self, mock_post):
        mock_post.return_value = MagicMock(raise_for_status=lambda: None)
        notifier = Notifier()
        notifier.send_slack("https://hooks.slack.com/abc", _msg())
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs.get("timeout") is not None


# ---------------------------------------------------------------------------
# send_github_comment
# ---------------------------------------------------------------------------

class TestSendGithubComment:
    @patch("dockcheck.tools.notify.subprocess.run")
    def test_send_github_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        notifier = Notifier()
        result = notifier.send_github_comment(_msg())

        assert result.success is True
        assert result.channel == "github"

    @patch("dockcheck.tools.notify.subprocess.run")
    def test_send_github_calls_gh_cli(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        notifier = Notifier()

        notifier.send_github_comment(_msg(title="Deploy complete"))

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gh"
        assert "pr" in cmd
        assert "comment" in cmd

    @patch("dockcheck.tools.notify.subprocess.run")
    def test_send_github_body_contains_title(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        notifier = Notifier()

        notifier.send_github_comment(_msg(title="Pipeline Status"))

        body_arg = mock_run.call_args[0][0]
        body_str = " ".join(str(a) for a in body_arg)
        assert "Pipeline Status" in body_str

    @patch("dockcheck.tools.notify.subprocess.run")
    def test_send_github_nonzero_rc_returns_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="no pull request found"
        )
        notifier = Notifier()
        result = notifier.send_github_comment(_msg())

        assert result.success is False
        assert "no pull request" in result.error

    @patch("dockcheck.tools.notify.subprocess.run", side_effect=FileNotFoundError)
    def test_send_github_gh_not_found_returns_failure(self, _mock_run):
        notifier = Notifier()
        result = notifier.send_github_comment(_msg())

        assert result.success is False
        assert "gh" in result.error.lower() or "not found" in result.error.lower()

    @patch(
        "dockcheck.tools.notify.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30),
    )
    def test_send_github_timeout_returns_failure(self, _mock_run):
        notifier = Notifier()
        result = notifier.send_github_comment(_msg())

        assert result.success is False
        assert "timed out" in result.error or "timeout" in result.error.lower()

    @patch("dockcheck.tools.notify.subprocess.run")
    def test_send_github_result_is_pydantic_model(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        notifier = Notifier()
        result = notifier.send_github_comment(_msg())
        assert isinstance(result, SendResult)


# ---------------------------------------------------------------------------
# send — channel routing
# ---------------------------------------------------------------------------

class TestNotifierSend:
    def test_send_to_stdout_only(self, capsys):
        notifier = Notifier(channels=[NotificationChannel(type="stdout")])
        results = notifier.send(_msg())
        assert len(results) == 1
        assert results[0].channel == "stdout"

    @patch("dockcheck.tools.notify.httpx.post")
    def test_send_to_multiple_channels(self, mock_post, capsys):
        mock_post.return_value = MagicMock(raise_for_status=lambda: None)
        notifier = Notifier(
            channels=[
                NotificationChannel(type="stdout"),
                NotificationChannel(type="slack", webhook_url="https://hooks.slack.com/abc"),
            ]
        )
        results = notifier.send(_msg())
        assert len(results) == 2
        channels = {r.channel for r in results}
        assert "stdout" in channels
        assert "slack" in channels

    def test_send_unknown_channel_returns_failure(self):
        notifier = Notifier(channels=[NotificationChannel(type="pager")])
        results = notifier.send(_msg())
        assert len(results) == 1
        assert results[0].success is False
        assert "pager" in results[0].error

    def test_send_slack_without_webhook_url_returns_failure(self):
        notifier = Notifier(channels=[NotificationChannel(type="slack")])
        results = notifier.send(_msg())
        assert len(results) == 1
        assert results[0].success is False
        assert "webhook_url" in results[0].error.lower() or results[0].error

    @patch("dockcheck.tools.notify.subprocess.run", side_effect=FileNotFoundError)
    def test_send_github_channel_failure_does_not_raise(self, _mock):
        notifier = Notifier(channels=[NotificationChannel(type="github")])
        results = notifier.send(_msg())
        assert len(results) == 1
        assert results[0].success is False

    def test_send_returns_list_of_send_results(self, capsys):
        notifier = Notifier()
        results = notifier.send(_msg())
        assert isinstance(results, list)
        assert all(isinstance(r, SendResult) for r in results)

    @patch("dockcheck.tools.notify.subprocess.run", side_effect=FileNotFoundError)
    @patch("dockcheck.tools.notify.httpx.post", side_effect=RuntimeError("network down"))
    def test_send_all_channels_fail_does_not_raise(self, _mock_http, _mock_sub, capsys):
        with patch("builtins.print", side_effect=OSError("disk full")):
            notifier = Notifier(
                channels=[
                    NotificationChannel(type="stdout"),
                    NotificationChannel(type="slack", webhook_url="https://hooks.slack.com/abc"),
                    NotificationChannel(type="github"),
                ]
            )
            results = notifier.send(_msg())
        assert len(results) == 3
        assert all(not r.success for r in results)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestFormatHelpers:
    def test_format_stdout_contains_prefix(self):
        msg = _msg(severity="error")
        output = _format_stdout(msg, "[ERROR]")
        assert "[ERROR]" in output

    def test_format_stdout_contains_title_and_body(self):
        msg = _msg(title="My Title", body="My body text")
        output = _format_stdout(msg, "[INFO]")
        assert "My Title" in output
        assert "My body text" in output

    def test_format_stdout_includes_metadata(self):
        msg = _msg(metadata={"service": "api", "region": "us-east-1"})
        output = _format_stdout(msg, "[INFO]")
        assert "service" in output
        assert "api" in output

    def test_build_slack_payload_has_blocks(self):
        msg = _msg(title="Deploy", body="Success", severity="info")
        payload = _build_slack_payload(msg)
        assert "blocks" in payload
        assert isinstance(payload["blocks"], list)

    def test_build_slack_payload_fallback_text(self):
        msg = _msg(severity="critical", title="OUTAGE")
        payload = _build_slack_payload(msg)
        assert "text" in payload
        assert "CRITICAL" in payload["text"] or "OUTAGE" in payload["text"]

    def test_build_slack_payload_metadata_as_fields(self):
        msg = _msg(metadata={"region": "eu-west-1"})
        payload = _build_slack_payload(msg)
        payload_str = str(payload)
        assert "region" in payload_str

    def test_format_github_body_contains_title(self):
        msg = _msg(title="Deploy Result")
        body = _format_github_body(msg)
        assert "Deploy Result" in body

    def test_format_github_body_contains_message_body(self):
        msg = _msg(body="All checks passed.")
        body = _format_github_body(msg)
        assert "All checks passed." in body

    def test_format_github_body_contains_metadata(self):
        msg = _msg(metadata={"commit": "abc123"})
        body = _format_github_body(msg)
        assert "commit" in body
        assert "abc123" in body

    def test_severity_prefix_coverage(self):
        expected = {"info", "warning", "error", "critical"}
        assert set(_SEVERITY_PREFIX.keys()) == expected
