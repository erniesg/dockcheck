"""GitHub Action workflow YAML generation."""

from __future__ import annotations

from pathlib import Path

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
    deploy_provider: str | None = None
    deploy_secrets: dict[str, str] = Field(default_factory=dict)
    # Detected project commands
    language: str | None = None
    install_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None
    test_command: str | None = None
    build_command: str | None = None


def generate_workflow(config: WorkflowConfig | None = None) -> str:
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

    # Setup language runtime
    if cfg.language in ("javascript", "typescript"):
        steps.append(
            "      - name: Set up Node.js\n"
            "        uses: actions/setup-node@v4\n"
            "        with:\n"
            "          node-version: '20'"
        )
    else:
        steps.append(
            f"      - name: Set up Python\n"
            f"        uses: actions/setup-python@v5\n"
            f"        with:\n"
            f"          python-version: '{cfg.python_version}'"
        )

    # Install project dependencies
    install_cmd = cfg.install_command
    if not install_cmd:
        if cfg.language in ("javascript", "typescript"):
            install_cmd = "npm ci"
        else:
            install_cmd = None

    if install_cmd:
        steps.append(
            f"      - name: Install dependencies\n"
            f"        run: {install_cmd}"
        )

    # Lint step
    if cfg.lint_command:
        steps.append(
            f"      - name: Lint\n"
            f"        run: {cfg.lint_command}"
        )

    # Format check step
    if cfg.format_command:
        steps.append(
            f"      - name: Format check\n"
            f"        run: {cfg.format_command}"
        )

    # Test step
    if cfg.test_command:
        steps.append(
            f"      - name: Test\n"
            f"        run: {cfg.test_command}"
        )

    # Build step
    if cfg.build_command:
        steps.append(
            f"      - name: Build\n"
            f"        run: {cfg.build_command}"
        )

    # Install dockcheck
    if cfg.dockcheck_version == "latest":
        dockcheck_install = "pip install dockcheck"
    else:
        dockcheck_install = f"pip install dockcheck=={cfg.dockcheck_version}"

    # Only add Python setup for JS projects that need dockcheck
    if cfg.language in ("javascript", "typescript"):
        steps.append(
            f"      - name: Set up Python (for dockcheck)\n"
            f"        uses: actions/setup-python@v5\n"
            f"        with:\n"
            f"          python-version: '{cfg.python_version}'"
        )

    steps.append(
        f"      - name: Install dockcheck\n"
        f"        run: {dockcheck_install}"
    )

    # Run dockcheck check
    steps.append(
        "      - name: Run dockcheck policy check\n"
        "        run: |\n"
        "          git diff origin/main...HEAD > /tmp/pr.diff\n"
        "          dockcheck check --diff /tmp/pr.diff"
        " --json-output > /tmp/check-result.json\n"
        "        continue-on-error: true"
    )

    # Provider-specific deploy step
    deploy_step = _build_deploy_step(cfg)
    if deploy_step:
        steps.append(deploy_step)

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
            "              const result = JSON.parse("
            "fs.readFileSync("
            "'/tmp/check-result.json', 'utf8'));\n"
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


def _build_deploy_step(cfg: WorkflowConfig) -> str | None:
    """Build a provider-specific deploy step, or None if no provider set."""
    if not cfg.deploy_provider:
        return None

    if cfg.deploy_provider == "cloudflare":
        with_block = "\n".join(
            f"          {k}: ${{{{ secrets.{v} }}}}"
            for k, v in cfg.deploy_secrets.items()
        )
        return (
            "      - name: Deploy to Cloudflare Workers\n"
            "        uses: cloudflare/wrangler-action@v3\n"
            "        with:\n"
            f"{with_block}"
        )

    if cfg.deploy_provider == "vercel":
        return (
            "      - name: Deploy to Vercel\n"
            "        run: vercel deploy --prod --yes\n"
            "        env:\n"
            "          VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}"
        )

    if cfg.deploy_provider == "fly":
        return (
            "      - name: Setup Fly.io CLI\n"
            "        uses: superfly/flyctl-actions/setup-flyctl@master\n\n"
            "      - name: Deploy to Fly.io\n"
            "        run: fly deploy\n"
            "        env:\n"
            "          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}"
        )

    if cfg.deploy_provider == "netlify":
        with_block = "\n".join(
            f"          {k}: ${{{{ secrets.{v} }}}}"
            for k, v in cfg.deploy_secrets.items()
        )
        return (
            "      - name: Deploy to Netlify\n"
            "        uses: nwtgck/actions-netlify@v3\n"
            "        with:\n"
            "          publish-dir: './build'\n"
            "          production-deploy: true\n"
            f"{with_block}"
        )

    if cfg.deploy_provider == "docker-registry":
        return (
            "      - name: Build and push Docker image\n"
            "        uses: docker/build-push-action@v5\n"
            "        with:\n"
            "          push: true\n"
            "          tags: ${{ secrets.DOCKER_USERNAME }}/${{ github.event.repository.name }}:latest"
        )

    if cfg.deploy_provider == "aws-lambda":
        with_block = "\n".join(
            f"          {k}: ${{{{ secrets.{v} }}}}"
            for k, v in cfg.deploy_secrets.items()
        )
        return (
            "      - name: Configure AWS credentials\n"
            "        uses: aws-actions/configure-aws-credentials@v4\n"
            "        with:\n"
            f"{with_block}\n\n"
            "      - name: Deploy to AWS Lambda (SAM)\n"
            "        run: sam deploy --no-confirm-changeset"
        )

    if cfg.deploy_provider == "gcp-cloudrun":
        with_block = "\n".join(
            f"          {k}: ${{{{ secrets.{v} }}}}"
            for k, v in cfg.deploy_secrets.items()
        )
        return (
            "      - name: Authenticate to Google Cloud\n"
            "        uses: google-github-actions/auth@v2\n"
            "        with:\n"
            f"{with_block}\n\n"
            "      - name: Deploy to Cloud Run\n"
            "        run: gcloud run deploy --source ."
        )

    if cfg.deploy_provider == "railway":
        return (
            "      - name: Deploy to Railway\n"
            "        run: |\n"
            "          npm install -g @railway/cli\n"
            "          railway up\n"
            "        env:\n"
            "          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}"
        )

    if cfg.deploy_provider == "render":
        return (
            "      - name: Deploy to Render\n"
            "        run: curl -X POST ${{ secrets.RENDER_DEPLOY_HOOK_URL }}\n"
            "        env:\n"
            "          RENDER_DEPLOY_HOOK_URL: ${{ secrets.RENDER_DEPLOY_HOOK_URL }}"
        )

    # Unknown provider — skip deploy step
    return None


def write_workflow(
    target_dir: str = ".",
    config: WorkflowConfig | None = None,
) -> Path:
    """Generate and write the workflow YAML to .github/workflows/."""
    target = Path(target_dir) / ".github" / "workflows"
    target.mkdir(parents=True, exist_ok=True)

    workflow_path = target / "dockcheck.yml"
    workflow_path.write_text(generate_workflow(config))
    return workflow_path
