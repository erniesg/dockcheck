"""GitHub Action workflow YAML generation."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class WorkflowConfig(BaseModel):
    """Configuration for generating GitHub Action workflow."""

    python_version: str = "3.11"
    dockcheck_version: str = "latest"
    trigger_on_pr: bool = True
    trigger_on_push: bool = False
    push_branches: list[str] = Field(default_factory=lambda: ["main"])
    runs_on: str = "ubuntu-latest"
    timeout_minutes: int = 30
    post_pr_comment: bool = True
    env_secrets: list[str] = Field(
        default_factory=lambda: ["ANTHROPIC_API_KEY"]
    )


def generate_workflow(config: Optional[WorkflowConfig] = None) -> str:
    """Generate a GitHub Actions workflow YAML string."""
    cfg = config or WorkflowConfig()

    trigger_block = _build_trigger_block(cfg)
    env_block = _build_env_block(cfg)
    steps_block = _build_steps_block(cfg)

    return f"""name: dockcheck CI/CD

{trigger_block}

{env_block}

jobs:
  dockcheck:
    runs-on: {cfg.runs_on}
    timeout-minutes: {cfg.timeout_minutes}
    permissions:
      contents: read
      pull-requests: write
      issues: write

    steps:
{steps_block}
"""


def _build_trigger_block(cfg: WorkflowConfig) -> str:
    lines = ["on:"]
    if cfg.trigger_on_pr:
        lines.append("  pull_request:")
        lines.append("    types: [opened, synchronize, reopened]")
    if cfg.trigger_on_push:
        branches = ", ".join(cfg.push_branches)
        lines.append("  push:")
        lines.append(f"    branches: [{branches}]")
    return "\n".join(lines)


def _build_env_block(cfg: WorkflowConfig) -> str:
    if not cfg.env_secrets:
        return ""
    lines = ["env:"]
    for secret in cfg.env_secrets:
        lines.append(f"  {secret}: ${{{{ secrets.{secret} }}}}")
    return "\n".join(lines)


def _build_steps_block(cfg: WorkflowConfig) -> str:
    steps = []

    # Checkout
    steps.append(
        "      - name: Checkout code\n"
        "        uses: actions/checkout@v4\n"
        "        with:\n"
        "          fetch-depth: 0"
    )

    # Setup Python
    steps.append(
        f"      - name: Set up Python\n"
        f"        uses: actions/setup-python@v5\n"
        f"        with:\n"
        f"          python-version: '{cfg.python_version}'"
    )

    # Install dockcheck
    if cfg.dockcheck_version == "latest":
        install_cmd = "pip install dockcheck"
    else:
        install_cmd = f"pip install dockcheck=={cfg.dockcheck_version}"

    steps.append(
        f"      - name: Install dockcheck\n"
        f"        run: {install_cmd}"
    )

    # Run dockcheck check
    steps.append(
        "      - name: Run dockcheck policy check\n"
        "        run: |\n"
        "          git diff origin/main...HEAD > /tmp/pr.diff\n"
        "          dockcheck check --diff /tmp/pr.diff --json-output > /tmp/check-result.json\n"
        "        continue-on-error: true"
    )

    # Run dockcheck pipeline
    steps.append(
        "      - name: Run dockcheck pipeline\n"
        "        run: dockcheck run\n"
        "        continue-on-error: true"
    )

    # Post PR comment
    if cfg.post_pr_comment:
        steps.append(
            "      - name: Post results to PR\n"
            "        if: github.event_name == 'pull_request'\n"
            "        uses: actions/github-script@v7\n"
            "        with:\n"
            "          script: |\n"
            "            const fs = require('fs');\n"
            "            let body = '## dockcheck Results\\n\\n';\n"
            "            try {\n"
            "              const result = JSON.parse(fs.readFileSync('/tmp/check-result.json', 'utf8'));\n"
            "              body += `**Verdict:** ${result.verdict}\\n\\n`;\n"
            "              if (result.reasons && result.reasons.length > 0) {\n"
            "                body += '**Reasons:**\\n';\n"
            "                result.reasons.forEach(r => body += `- ${r}\\n`);\n"
            "              }\n"
            "            } catch (e) {\n"
            "              body += 'Policy check results not available.\\n';\n"
            "            }\n"
            "            github.rest.issues.createComment({\n"
            "              owner: context.repo.owner,\n"
            "              repo: context.repo.repo,\n"
            "              issue_number: context.issue.number,\n"
            "              body: body\n"
            "            });"
        )

    return "\n\n".join(steps)


def write_workflow(
    target_dir: str = ".",
    config: Optional[WorkflowConfig] = None,
) -> Path:
    """Generate and write the workflow YAML to .github/workflows/."""
    target = Path(target_dir) / ".github" / "workflows"
    target.mkdir(parents=True, exist_ok=True)

    workflow_path = target / "dockcheck.yml"
    workflow_path.write_text(generate_workflow(config))
    return workflow_path
