# AGENTS.md — AI Agent Subsystem

## Overview

dockcheck uses AI coding agents (Claude Code, Codex) as pipeline step executors. Each step maps to a **skill** that provides domain-specific instructions. The orchestrator coordinates step execution, retries, and deployment decisions.

## Skill Definitions

Skills live in `.dockcheck/skills/<name>/SKILL.md`. Each SKILL.md has:

- `# <Name>` — H1 display name
- `## Purpose` — what the skill does
- `## Inputs` — what context it receives
- `## Instructions` — step-by-step instructions for the AI agent

### Built-in Skills

| Skill | Purpose |
|-------|---------|
| `analyze` | Assess blast radius, affected files, and risks from a git diff |
| `test` | Run test suite, interpret results, suggest fixes |
| `test-writer` | Generate test cases for changed code |
| `verify` | Post-deploy verification (health checks, smoke tests) |
| `deploy` | Execute deployment via provider CLI |
| `notify` | Format and send deployment notifications |

## Orchestrator Pipeline

```
┌─────────┐   ┌─────────┐   ┌──────────┐   ┌────────┐
│ analyze │──▶│  test   │──▶│  verify  │──▶│ deploy │
└─────────┘   └─────────┘   └──────────┘   └────────┘
     │              │              │
     ▼              ▼              ▼
  Findings      Findings      Findings
     │              │              │
     └──────────────┴──────────────┘
                    │
              ConfidenceScorer
                    │
              ┌─────▼─────┐
              │  Decision  │
              │  Engine    │
              └────────────┘
              deploy / notify / block
```

## Agent Dispatch

`AgentDispatcher` runs agents as subprocesses:

- **Claude**: `claude --print --output-format json --max-turns N <prompt>`
- **Codex**: `codex --quiet --approval-mode full-auto <prompt>`

Output is parsed into `AgentResult`:
```python
class AgentResult(BaseModel):
    completed: bool
    confidence: float  # 0.0–1.0
    turns_used: int
    summary: str
    findings: list[Finding]
    action_needed: Literal["none", "retry", "escalate"] | None
```

## Confidence Scoring

`ConfidenceScorer` aggregates per-step results into an overall score:

- Critical findings → score capped at 0
- Weighted average of step confidences
- Thresholds from `policy.yaml`:
  - `auto_deploy_staging` (default 0.8) → auto-deploy
  - `notify_human` (default 0.6) → requires human review
  - Below `notify_human` → blocked

## Pipeline Execution

1. Steps sorted topologically (Kahn's algorithm)
2. Steps in same `parallel_group` run concurrently via `asyncio.gather`
3. Policy engine pre-checks each step (hard stops, critical paths)
4. Failed steps retry up to `max_retries` when `action_needed == "retry"`
5. `action_needed == "escalate"` immediately blocks the pipeline
6. Final decision: deploy / notify / block based on confidence thresholds

## CLI Integration

```bash
# Standard subprocess pipeline (default)
dockcheck run

# AI-powered agent pipeline
dockcheck run --agent

# Full magic command with agent mode
dockcheck ship --agent
```
