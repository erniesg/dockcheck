# Notify

## Purpose
Send structured notifications to configured channels about pipeline events (deploy, block, rollback, human escalation).

## Inputs
- Event type (deploy, block, rollback, escalate)
- Pipeline summary (confidence score, verdict, step results)
- Notification channels configuration (stdout, slack, github-comment)
- Repository context (repo name, branch, commit SHA, PR URL)

## Expected Output
```json
{
  "completed": true,
  "confidence": 0.0,
  "summary": "Description of notifications sent",
  "findings": [{"severity": "info|warning|error|critical", "message": "...", "file_path": "...", "line": null}]
}
```

## Instructions
1. Compose a concise notification message including: event type, confidence score, verdict, and top findings
2. Include the commit SHA, branch, and PR URL if available
3. Send to stdout channel first (always enabled)
4. For Slack channel: POST to webhook_url with JSON payload; retry once on failure
5. For github-comment channel: post a formatted markdown comment to the PR
6. Never include secrets, credentials, or environment variable values in notification messages
7. Set confidence to 1.0 if all channels received the message, lower if any channel failed
8. Emit warning finding for each channel that failed to deliver
