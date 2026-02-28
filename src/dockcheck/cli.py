"""CLI entrypoint — dockcheck init, check, run, validate."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from dockcheck.core.policy import EvaluationResult, Policy, PolicyEngine, Verdict
from dockcheck.tools.hardstop import DiffAnalyzer


def _find_policy(path: str | None = None) -> Path:
    """Locate policy.yaml, searching .dockcheck/ then cwd."""
    if path:
        p = Path(path)
        if p.exists():
            return p
        click.echo(f"Error: policy file not found at {path}", err=True)
        sys.exit(1)

    candidates = [
        Path(".dockcheck/policy.yaml"),
        Path("policy.yaml"),
    ]
    for c in candidates:
        if c.exists():
            return c

    click.echo(
        "Error: no policy.yaml found. Run `dockcheck init` first.",
        err=True,
    )
    sys.exit(1)


@click.group()
@click.version_option(package_name="dockcheck")
def cli() -> None:
    """dockcheck — Agentic CI/CD runtime for safe, automated deployment."""


@cli.command()
@click.option(
    "--template",
    type=click.Choice(["hackathon", "trading-bot", "fastapi-app", "react-app"]),
    default=None,
    help="Template to scaffold from (skips detection).",
)
@click.option(
    "--provider",
    type=click.Choice(["cloudflare", "vercel", "fly", "netlify", "docker-registry"]),
    default=None,
    help="Deploy provider (skips detection).",
)
@click.option(
    "--dir",
    "target_dir",
    type=click.Path(),
    default=".",
    help="Target directory for scaffolding.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help="Skip interactive prompts (use defaults/env vars).",
)
def init(
    template: str | None,
    provider: str | None,
    target_dir: str,
    non_interactive: bool,
) -> None:
    """Scaffold a .dockcheck/ directory with policy, skills, and config.

    Without --template, scans the repo to detect language, framework,
    and deploy target, then bootstraps auth and generates workflows.
    """
    target = Path(target_dir)
    dockcheck_dir = target / ".dockcheck"

    if dockcheck_dir.exists():
        click.echo(
            f".dockcheck/ already exists at {dockcheck_dir}. "
            "Use --dir to specify another location."
        )
        return

    # Legacy template-only path
    if template is not None:
        _init_from_template(template, target, dockcheck_dir)
        return

    # Smart detection path
    _init_smart(target, dockcheck_dir, provider, non_interactive)


def _init_from_template(template: str, target: Path, dockcheck_dir: Path) -> None:
    """Legacy init: scaffold from a named template."""
    dockcheck_dir.mkdir(parents=True)
    (dockcheck_dir / "skills").mkdir()

    default_policy = _default_policy(template)
    (dockcheck_dir / "policy.yaml").write_text(default_policy)

    default_config = _default_config()
    (target / "dockcheck.yml").write_text(default_config)

    click.echo(f"Initialized .dockcheck/ with template '{template}'")
    click.echo(f"  - {dockcheck_dir / 'policy.yaml'}")
    click.echo(f"  - {target / 'dockcheck.yml'}")
    click.echo("\nNext steps:")
    click.echo("  1. Edit .dockcheck/policy.yaml to customize safety rules")
    click.echo("  2. Run `dockcheck check` to validate your policy")
    click.echo("  3. Run `dockcheck run` to execute the pipeline")


def _init_smart(
    target: Path,
    dockcheck_dir: Path,
    provider_name: str | None,
    non_interactive: bool,
) -> None:
    """Smart init: detect repo, bootstrap auth, generate workflows."""
    from dockcheck.github.action import WorkflowConfig, write_workflow
    from dockcheck.init.auth import AuthBootstrapper
    from dockcheck.init.detect import RepoDetector
    from dockcheck.init.providers import ProviderRegistry

    detector = RepoDetector()
    registry = ProviderRegistry()
    auth = AuthBootstrapper(env_file=str(target / ".env"))

    # 1. Scan repository
    click.echo("Scanning repository...")
    ctx = detector.detect(str(target))

    lang_display = ctx.language or "unknown"
    parts = [f"Language: {lang_display}"]
    if ctx.framework:
        parts.append(f"Framework: {ctx.framework}")
    if ctx.has_wrangler_config:
        parts.append("Config: wrangler.toml")
    elif ctx.has_vercel_config:
        parts.append("Config: vercel.json")
    elif ctx.has_fly_config:
        parts.append("Config: fly.toml")
    elif ctx.has_netlify_config:
        parts.append("Config: netlify.toml")
    if ctx.git_remote:
        parts.append(f"Remote: {ctx.git_remote}")
    click.echo(f"  {' | '.join(parts)}")

    # 2. Determine deploy provider
    if provider_name:
        selected = registry.get(provider_name)
    else:
        detected = registry.detect(ctx)
        if not detected:
            click.echo("\nNo deploy target detected. Using default policy.")
            _init_from_template("hackathon", target, dockcheck_dir)
            return

        selected = detected[0]
        if not non_interactive:
            confirm = click.confirm(
                f"\nDeploy target: {selected.display_name} "
                f"(detected from config). Confirm?",
                default=True,
            )
            if not confirm:
                click.echo("Aborted.")
                return

    click.echo(f"\nDeploy target: {selected.display_name}")

    # 3. Check and bootstrap auth
    click.echo("\nChecking auth...")
    status = auth.check(selected)

    secrets_to_store = {}
    if not status.all_ready:
        missing = [s for s in status.secrets if not s.available_local]
        if non_interactive:
            click.echo("  Missing secrets (set in env or .env):")
            for s in missing:
                click.echo(f"    {s.name} — {s.setup_url}")
        else:
            for s in missing:
                click.echo(f"  Missing: {s.name}")
            collected = auth.prompt_missing(status)
            secrets_to_store = collected
    else:
        click.echo("  All secrets available.")

    # 4. Store secrets
    if secrets_to_store:
        auth.store_local(secrets_to_store, env_file=str(target / ".env"))
        click.echo("\nStored to .env")

        gitignore_updated = auth.ensure_gitignore(str(target))
        if gitignore_updated:
            click.echo(".gitignore updated")

        if not non_interactive:
            set_gh = click.confirm(
                "Set as GitHub Secrets?", default=True
            )
            if set_gh:
                ok = auth.store_github(secrets_to_store)
                click.echo("  Done" if ok else "  Partial (check warnings)")
    else:
        auth.ensure_gitignore(str(target))

    # 5. Generate policy + workflow
    dockcheck_dir.mkdir(parents=True)
    (dockcheck_dir / "skills").mkdir()

    default_policy = _default_policy("hackathon")
    (dockcheck_dir / "policy.yaml").write_text(default_policy)

    # Generate provider-aware workflow
    env_secrets = [s.name for s in selected.required_secrets]
    wf_config = WorkflowConfig(
        trigger_on_push=True,
        env_secrets=env_secrets,
        deploy_provider=selected.name,
        deploy_secrets=selected.github_action_secrets,
    )
    wf_path = write_workflow(str(target), wf_config)

    click.echo(f"\nGenerated: {dockcheck_dir / 'policy.yaml'}")
    click.echo(f"Generated: {wf_path}")
    click.echo("\nReady! Push to deploy.")


@cli.command()
@click.option(
    "--policy", "policy_path", type=click.Path(),
    default=None, help="Path to policy.yaml",
)
@click.option(
    "--diff", "diff_source", type=click.Path(),
    default=None, help="Diff file (or - for stdin)",
)
@click.option(
    "--commands", multiple=True,
    help="Commands to check against hard stops",
)
@click.option(
    "--files", multiple=True,
    help="File paths to check against critical paths",
)
@click.option("--json-output", "json_out", is_flag=True, help="Output as JSON")
def check(
    policy_path: str | None,
    diff_source: str | None,
    commands: tuple[str, ...],
    files: tuple[str, ...],
    json_out: bool,
) -> None:
    """Evaluate commands/files/diff against policy rules."""
    policy_file = _find_policy(policy_path)
    engine = PolicyEngine.from_yaml(policy_file)

    all_commands = list(commands)
    all_files = list(files)

    # Parse diff if provided
    if diff_source:
        if diff_source == "-":
            diff_text = sys.stdin.read()
        else:
            diff_text = Path(diff_source).read_text()

        all_files.extend(DiffAnalyzer.extract_file_paths(diff_text))
        file_deletes = DiffAnalyzer.count_file_deletes(diff_text)
    else:
        file_deletes = 0

    result: EvaluationResult = engine.evaluate(
        commands=all_commands or None,
        file_paths=all_files or None,
        file_deletes=file_deletes,
    )

    if json_out:
        click.echo(result.model_dump_json(indent=2))
    else:
        _print_result(result)

    if result.verdict == Verdict.BLOCK:
        sys.exit(2)
    elif result.verdict == Verdict.FAIL:
        sys.exit(1)


@cli.command()
@click.option("--policy", "policy_path", type=click.Path(), default=None)
@click.option("--dry-run", is_flag=True, help="Show pipeline plan without executing")
@click.pass_context
def run(ctx: click.Context, policy_path: str | None, dry_run: bool) -> None:
    """Execute the full CI/CD pipeline."""
    policy_file = _find_policy(policy_path)
    engine = PolicyEngine.from_yaml(policy_file)

    if dry_run:
        click.echo("Pipeline plan (dry run):")
        click.echo("  1. ANALYZE — Diff analysis + blast radius")
        click.echo("  2. TEST    — Run tests + coverage check")
        click.echo("  3. ASSESS  — Compute confidence score")
        click.echo("  4. DEPLOY  — Build + deploy to staging")
        click.echo("  5. VERIFY  — Post-deploy smoke tests")
        click.echo("  6. PROMOTE — Promote to production (if policy allows)")
        click.echo(f"\nPolicy: {policy_file}")
        thresholds = engine.policy.confidence_thresholds
        click.echo(f"Auto-deploy staging threshold: {thresholds.auto_deploy_staging}")
        click.echo(f"Auto-promote prod threshold: {thresholds.auto_promote_prod}")
        return

    click.echo("Pipeline execution requires agent dispatch (Phase 3).")
    click.echo("Run `dockcheck run --dry-run` to see the pipeline plan.")


@cli.command()
@click.option("--policy", "policy_path", type=click.Path(), default=None)
def validate(policy_path: str | None) -> None:
    """Validate policy.yaml syntax and rules."""
    policy_file = _find_policy(policy_path)
    try:
        policy = Policy.from_yaml(policy_file)
        click.echo(f"Policy valid: {policy_file}")
        click.echo(f"  Version: {policy.version}")
        click.echo(f"  Hard stop commands: {len(policy.hard_stops.commands)}")
        click.echo(f"  Critical paths: {len(policy.hard_stops.critical_paths)}")
        click.echo(f"  Notification channels: {len(policy.notifications.channels)}")
    except Exception as e:
        click.echo(f"Policy invalid: {e}", err=True)
        sys.exit(1)


def _print_result(result: EvaluationResult) -> None:
    icon = {"pass": "PASS", "fail": "FAIL", "block": "BLOCK"}[result.verdict.value]
    click.echo(f"\n[{icon}] Policy evaluation: {result.verdict.value.upper()}")

    if result.reasons:
        click.echo("\nReasons:")
        for r in result.reasons:
            click.echo(f"  - {r}")

    if result.blocked_commands:
        click.echo(f"\nBlocked commands: {len(result.blocked_commands)}")
    if result.blocked_paths:
        click.echo(f"\nBlocked paths: {len(result.blocked_paths)}")
    if result.breaker_violations:
        click.echo(f"\nCircuit breaker violations: {len(result.breaker_violations)}")

    click.echo()


def _default_policy(template: str) -> str:
    """Generate default policy YAML for a given template."""
    if template == "trading-bot":
        thresholds = {
            "auto_deploy_staging": 0.95,
            "auto_promote_prod": 0.99,
            "notify_human": 0.8,
        }
        extra_commands = [
            '    - pattern: "modify_position"',
            '    - pattern: "place_order"',
            '    - pattern: "cancel_all"',
        ]
    elif template == "hackathon":
        thresholds = {
            "auto_deploy_staging": 0.6,
            "auto_promote_prod": 0.7,
            "notify_human": 0.3,
        }
        extra_commands = []
    else:
        thresholds = {
            "auto_deploy_staging": 0.8,
            "auto_promote_prod": 0.9,
            "notify_human": 0.6,
        }
        extra_commands = []

    extra_cmd_block = "\n".join(extra_commands)
    if extra_cmd_block:
        extra_cmd_block = "\n" + extra_cmd_block

    return f"""version: "1"

hard_stops:
  commands:
    - pattern: "rm -rf"
    - pattern: "rm -r /"
    - pattern: "DROP TABLE"
    - pattern: "DROP DATABASE"
    - pattern: "git push --force"
    - pattern: "git reset --hard"
    - pattern: "terraform destroy"
    - pattern: "kubectl delete namespace"{extra_cmd_block}
  critical_paths:
    - "**/production/**"
    - "**/.env*"
    - "**/secrets/**"
  circuit_breakers:
    max_containers: 5
    max_cost_per_run_usd: 10
    max_deploys_per_hour: 3
    max_file_deletes_per_turn: 10

confidence_thresholds:
  auto_deploy_staging: {thresholds['auto_deploy_staging']}
  auto_promote_prod: {thresholds['auto_promote_prod']}
  notify_human: {thresholds['notify_human']}

notifications:
  on_deploy: true
  on_block: true
  on_rollback: true
  channels:
    - type: stdout
    - type: github-comment
"""


def _default_config() -> str:
    return """project:
  name: "my-app"
  test_command: "pytest"
  build_command: "docker build -t my-app ."
  dockerfile: "./Dockerfile"

deploy:
  staging:
    target: docker
    registry: "ghcr.io/org/app"
  production:
    target: docker
    registry: "ghcr.io/org/app"
    requires_staging: true
"""
