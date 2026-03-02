# Secret Audit

## Purpose
Evaluate which environment secrets are truly required for deployment.
Not all env var references are blocking — some have defaults, some are
test-only, some are optional features.

## Inputs
- AuditResult JSON (enriched scan with code context)
- Target name and provider

## Expected Output
```json
{
  "completed": true,
  "confidence": 0.0,
  "summary": "Secret audit: X required, Y optional, Z test-only",
  "findings": [{"severity": "info|warning|error|critical", "message": "...", "file_path": "...", "line": null}]
}
```

## Instructions
1. Review each secret's code context (the surrounding lines of source code)
2. Classify each secret as one of:
   - **REQUIRED**: No default value, used in a production code path, and currently missing from environment
   - **OPTIONAL**: Has a default/fallback value, or is behind a feature flag
   - **TEST_ONLY**: Only referenced in test files (test_, .spec., etc.)
3. For REQUIRED secrets that are missing from the environment:
   - Create an `error` finding with the secret name and file location
   - Explain why the secret appears required (no fallback, production path)
4. For OPTIONAL secrets:
   - Create an `info` finding noting the default/fallback
5. For TEST_ONLY secrets:
   - Create an `info` finding — these do not block deployment
6. Rate confidence based on:
   - High (0.9+): All secrets clearly classifiable from context
   - Medium (0.7-0.9): Most secrets clear, some ambiguous
   - Low (<0.7): Many secrets with unclear usage patterns
7. If all required secrets are available, confidence should be high
8. If any required secret is missing, set action_needed to "escalate"
