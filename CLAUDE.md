# CLAUDE.md — dockcheck

## Project Overview

Agentic CI/CD runtime for safe, automated deployment with AI coding agents. A hackathon team runs `dockcheck ship` to go from zero to fully-deployed multi-service project.

## Tech Stack

- **Python 3.10+** (pyenv lewagon env), hatchling build system
- **Pydantic v2** for all models, **Click** for CLI
- **pytest** with `asyncio_mode = "auto"`, ~700+ tests, runs in ~1.5s

## Conventions

- `from __future__ import annotations` in **every** file
- Never use `Self` from typing — use string literal `"ClassName"` instead
- Line length: 100 (ruff)
- Ruff lint: `select = ["E", "F", "I", "N", "W", "UP"]`

## Module Map

```
src/dockcheck/
├── cli.py                 # Click CLI: init, check, run, deploy, ship, validate
├── core/
│   ├── policy.py          # PolicyEngine, Verdict, EvaluationResult
│   ├── confidence.py      # ConfidenceScorer, AgentStepResult
│   └── orchestrator.py    # Pipeline executor (Kahn's DAG sort, retries, decisions)
├── agents/
│   ├── schemas.py         # AgentResult, StepConfig, PipelineConfig, PipelineResult
│   ├── dispatch.py        # AgentDispatcher (Claude CLI / Codex subprocess)
│   └── parallel.py        # Parallel dispatch helpers
├── tools/
│   ├── deploy.py          # DeployProviderFactory (CF, Vercel, Fly, etc.)
│   ├── secrets.py         # MaskedSecret wrapper
│   ├── docker.py          # Docker tool
│   ├── hardstop.py        # DiffAnalyzer
│   ├── notify.py          # Notification tool
│   └── terraform.py       # TerraformTool (destroy always blocked)
├── skills/
│   └── loader.py          # SkillLoader: discover + parse SKILL.md files
├── init/
│   ├── detect.py          # RepoDetector → RepoContext
│   ├── providers.py       # ProviderRegistry (9 providers)
│   ├── auth.py            # AuthBootstrapper (check, prompt, store)
│   ├── preflight.py       # PreflightChecker (7 checks)
│   ├── workspace.py       # WorkspaceConfig, TargetConfig, WorkspaceResolver
│   └── secret_scanner.py  # SecretScanner (env var detection in source)
├── github/
│   └── action.py          # GitHub Actions workflow YAML generation
└── templates/
    └── ...                # Scaffold templates
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `dockcheck init` | Scaffold .dockcheck/ (smart detection or --template) |
| `dockcheck check` | Evaluate policy only |
| `dockcheck run` | Pipeline: lint → test → check (optionally deploy, --agent for AI) |
| `dockcheck deploy` | Thin wrapper, calls detected/specified provider |
| `dockcheck ship` | Magic: preflight → auth → init → lint → test → check → deploy |
| `dockcheck validate` | Validate policy.yaml syntax |

## Test Patterns

```python
# Mock subprocess (deploy providers)
_MOCK_SUBPROCESS_EMPTY = MagicMock(stdout="", stderr="", returncode=0)

# CLI tests use Click's CliRunner
runner = CliRunner()
result = runner.invoke(cli, ["ship", "--dry-run", "--dir", str(tmp_path)])

# Stacked patches for deploy tests
with patch("subprocess.run", return_value=mock_result):
    with patch("os.environ.get", side_effect=lambda k, d=None: env.get(k, d)):
        ...

# Async tests: asyncio_mode = "auto" (no @pytest.mark.asyncio needed in theory,
# but we still add it for clarity)
```

## Key Patterns

- `MaskedSecret`: wraps values, `str()`/`repr()` return `***`, `.reveal()` gets raw value
- `TerraformTool.destroy()` is **always** blocked
- Orchestrator uses Kahn's algorithm for DAG resolution
- `_detect_deploy_provider()` type hint uses `object` to avoid circular import
- `fnmatch` doesn't handle `**/` — custom `_matches_glob()` helper needed
- YAML `on:` key parses as boolean True — access via `parsed.get(True)` in tests
