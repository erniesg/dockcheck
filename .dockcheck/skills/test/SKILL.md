# Test

## Purpose
Execute the project test suite and assess overall test health, coverage, and pass rate.

## Inputs
- Test command (e.g., pytest, npm test, go test ./...)
- Repository context (language, framework, test runner)
- List of changed files to focus test scope

## Expected Output
```json
{
  "completed": true,
  "confidence": 0.0,
  "summary": "Description of test results",
  "findings": [{"severity": "info|warning|error|critical", "message": "...", "file_path": "...", "line": null}]
}
```

## Instructions
1. Run the configured test command from the project root
2. Parse test output to extract pass/fail counts and any failure details
3. Check test coverage if available (target >= 80%)
4. Identify flaky tests or timeout failures
5. Flag any test file that directly tests a changed source file
6. Set confidence based on pass rate: 1.0 for all pass, scale down proportionally for failures
7. Emit error-severity findings for test failures, warning for coverage gaps
