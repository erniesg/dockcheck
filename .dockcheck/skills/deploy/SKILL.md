# Deploy

## Purpose
Build the project artifact (Docker image, binary, or package) and deploy it to the target environment.

## Inputs
- Build command (e.g., docker build -t app .)
- Dockerfile path
- Target environment (staging or production)
- Registry URL and credentials
- Repository context (language, framework)

## Expected Output
```json
{
  "completed": true,
  "confidence": 0.0,
  "summary": "Description of deployment outcome",
  "findings": [{"severity": "info|warning|error|critical", "message": "...", "file_path": "...", "line": null}]
}
```

## Instructions
1. Run the build command and capture output; fail fast on build errors
2. Tag the artifact with the git commit SHA and environment label
3. Push the artifact to the configured registry
4. Deploy to the target environment using the configured deployment method
5. Record the deployment manifest (image digest, timestamp, commit SHA)
6. Never deploy to production directly — always require staging first unless policy explicitly allows it
7. Set confidence to 1.0 on successful deployment, 0.0 on build or push failure
8. Emit critical findings for any command that matches hard-stop patterns
