# Analyze

## Purpose
Analyze the git diff to determine blast radius, affected files, and potential risks.

## Inputs
- Git diff (unified format)
- List of changed files
- Repository context (language, framework)

## Expected Output
```json
{
  "completed": true,
  "confidence": 0.0,
  "summary": "Description of changes",
  "findings": [{"severity": "info|warning|error|critical", "message": "...", "file_path": "...", "line": null}]
}
```

## Instructions
1. Read the provided diff carefully
2. Identify all modified, added, and deleted files
3. Assess blast radius: how many systems/modules are affected
4. Check for risky patterns: database migrations, config changes, dependency updates
5. Flag any files matching critical paths (production configs, secrets, env files)
6. Rate confidence based on how well you understand the changes
