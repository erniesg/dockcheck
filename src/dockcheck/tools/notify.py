"""Notification dispatch — stdout, Slack webhook, GitHub PR comment."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any, Dict, List, Literal, Optional

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

Severity = Literal["info", "warning", "error", "critical"]

# Severity → emoji-free prefix for stdout formatting
_SEVERITY_PREFIX: Dict[str, str] = {
    "info": "[INFO]",
    "warning": "[WARNING]",
    "error": "[ERROR]",
    "critical": "[CRITICAL]",
}


class NotificationChannel(BaseModel):
    type: str  # "stdout" | "slack" | "github"
    webhook_url: Optional[str] = None


class NotificationMessage(BaseModel):
    title: str
    body: str
    severity: Severity = "info"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SendResult(BaseModel):
    channel: str
    success: bool
    error: Optional[str] = None


class Notifier:
    """
    Dispatches ``NotificationMessage`` objects across configured channels.

    Failures in individual channels are logged but never raise exceptions
    (fire-and-forget pattern).  ``send()`` always returns a result list.

    Supported channels:
    - ``stdout`` — always available
    - ``slack`` — requires ``webhook_url`` in the channel config
    - ``github`` — uses ``gh pr comment`` CLI; requires ``gh`` on PATH
    """

    def __init__(self, channels: Optional[List[NotificationChannel]] = None) -> None:
        if channels is None:
            channels = [NotificationChannel(type="stdout")]
        self.channels = channels

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    def send(self, message: NotificationMessage) -> List[SendResult]:
        """Send *message* to all configured channels. Never raises."""
        results: List[SendResult] = []
        for channel in self.channels:
            if channel.type == "stdout":
                results.append(self.send_stdout(message))
            elif channel.type == "slack":
                if channel.webhook_url:
                    results.append(self.send_slack(channel.webhook_url, message))
                else:
                    results.append(
                        SendResult(
                            channel="slack",
                            success=False,
                            error="No webhook_url configured for slack channel.",
                        )
                    )
            elif channel.type == "github":
                results.append(self.send_github_comment(message))
            else:
                logger.warning("Unknown notification channel type: %s", channel.type)
                results.append(
                    SendResult(
                        channel=channel.type,
                        success=False,
                        error=f"Unknown channel type: {channel.type}",
                    )
                )
        return results

    # ------------------------------------------------------------------
    # Channel implementations
    # ------------------------------------------------------------------

    def send_stdout(self, message: NotificationMessage) -> SendResult:
        """Format and write message to stdout. Never raises."""
        try:
            prefix = _SEVERITY_PREFIX.get(message.severity, "[INFO]")
            formatted = _format_stdout(message, prefix)
            print(formatted, file=sys.stdout, flush=True)
            logger.debug("Notification sent to stdout: title=%r", message.title)
            return SendResult(channel="stdout", success=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("stdout notification failed: %s", exc)
            return SendResult(channel="stdout", success=False, error=str(exc))

    def send_slack(self, webhook_url: str, message: NotificationMessage) -> SendResult:
        """
        POST a Slack message via incoming webhook.

        Uses httpx for the HTTP call. Failures are caught and logged.
        """
        payload = _build_slack_payload(message)
        try:
            response = httpx.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Slack notification sent: title=%r status=%s", message.title, response.status_code)
            return SendResult(channel="slack", success=True)
        except httpx.HTTPStatusError as exc:
            error_msg = f"Slack HTTP error {exc.response.status_code}: {exc.response.text}"
            logger.error("Slack notification failed: %s", error_msg)
            return SendResult(channel="slack", success=False, error=error_msg)
        except httpx.RequestError as exc:
            error_msg = f"Slack request error: {exc}"
            logger.error("Slack notification failed: %s", error_msg)
            return SendResult(channel="slack", success=False, error=error_msg)
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            logger.error("Slack notification unexpected error: %s", error_msg)
            return SendResult(channel="slack", success=False, error=error_msg)

    def send_github_comment(self, message: NotificationMessage) -> SendResult:
        """
        Post a comment on the current GitHub PR using ``gh pr comment``.

        Falls back gracefully when ``gh`` is not on PATH or no PR is open.
        """
        body = _format_github_body(message)
        try:
            result = subprocess.run(
                ["gh", "pr", "comment", "--body", body],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                error_msg = (result.stderr or result.stdout).strip()
                logger.warning("gh pr comment failed (rc=%d): %s", result.returncode, error_msg)
                return SendResult(channel="github", success=False, error=error_msg)
            logger.info("GitHub PR comment posted: title=%r", message.title)
            return SendResult(channel="github", success=True)
        except FileNotFoundError:
            error_msg = "'gh' CLI not found on PATH — GitHub notification skipped."
            logger.warning(error_msg)
            return SendResult(channel="github", success=False, error=error_msg)
        except subprocess.TimeoutExpired:
            error_msg = "gh pr comment timed out after 30 seconds."
            logger.error(error_msg)
            return SendResult(channel="github", success=False, error=error_msg)
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            logger.error("GitHub notification unexpected error: %s", error_msg)
            return SendResult(channel="github", success=False, error=error_msg)


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------

def _format_stdout(message: NotificationMessage, prefix: str) -> str:
    lines = [f"{prefix} {message.title}", message.body]
    if message.metadata:
        for key, value in message.metadata.items():
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _build_slack_payload(message: NotificationMessage) -> Dict[str, Any]:
    """Build a Slack API-compatible payload with Block Kit formatting."""
    severity_icons = {
        "info": ":information_source:",
        "warning": ":warning:",
        "error": ":x:",
        "critical": ":rotating_light:",
    }
    icon = severity_icons.get(message.severity, ":information_source:")
    header = f"{icon} *{message.title}*"
    blocks: List[Dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": message.body}},
    ]
    if message.metadata:
        fields = [
            {"type": "mrkdwn", "text": f"*{k}*: {v}"}
            for k, v in message.metadata.items()
        ]
        blocks.append({"type": "section", "fields": fields})
    return {"blocks": blocks, "text": f"{message.severity.upper()}: {message.title}"}


def _format_github_body(message: NotificationMessage) -> str:
    """Format a message as a Markdown GitHub PR comment body."""
    severity_headers = {
        "info": "### ℹ️",
        "warning": "### ⚠️",
        "error": "### ❌",
        "critical": "### 🚨",
    }
    header = severity_headers.get(message.severity, "###")
    lines = [f"{header} {message.title}", "", message.body]
    if message.metadata:
        lines.append("")
        lines.append("**Details:**")
        for key, value in message.metadata.items():
            lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)
