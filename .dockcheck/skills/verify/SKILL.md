# Verify

## Purpose
Run post-deployment smoke tests and health checks to confirm the deployed service is functioning correctly.

## Inputs
- Deployed service URL or container name
- Target environment (staging or production)
- Health check endpoints to probe
- Expected response codes and payloads

## Expected Output
```json
{
  "completed": true,
  "confidence": 0.0,
  "summary": "Description of verification results",
  "findings": [{"severity": "info|warning|error|critical", "message": "...", "file_path": "...", "line": null}]
}
```

## Instructions
1. Wait for the service to become healthy (poll health endpoint with backoff)
2. Run smoke tests: check all critical API endpoints return expected status codes
3. Verify key business flows work end-to-end (e.g., login, data write, data read)
4. Compare response latency against baseline (warn if p99 > 2x baseline)
5. Check logs for unexpected errors (ERROR or CRITICAL level) in the first 60 seconds
6. Set confidence to 1.0 if all health checks pass, 0.5 if degraded, 0.0 if service is down
7. Emit critical findings if the service fails to start or health endpoint returns non-200
