"""CLI entrypoint — dockcheck init, check, run, validate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from dockcheck.core.confidence import ConfidenceScorer
from dockcheck.core.policy import EvaluationResult, Policy, PolicyEngine, Verdict
from dockcheck.tools.hardstop import CriticalPathChecker, DiffAnalyzer, HardStopChecker


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
    default="hackathon",
    help="Template to scaffold from.",
)
@click.option(
    "--dir",
    "target_dir",
    type=click.Path(),
    default=".",
    help="Target directory for scaffolding.",
)
def init(template: str, target_dir: str) -> None:
    """Scaffold a .dockcheck/ directory with policy, skills, and config."""
    target = Path(target_dir)
    dockcheck_dir = target / ".dockcheck"

    if dockcheck_dir.exists():
        click.echo(f".dockcheck/ already exists at {dockcheck_dir}. Use --dir to specify another location.")
        return

    dockcheck_dir.mkdir(parents=True)
    (dockcheck_dir / "skills").mkdir()

    # Write default policy
    default_policy = _default_policy(template)
    (dockcheck_dir / "policy.yaml").write_text(default_policy)

    # Write default project config
    default_config = _default_config()
    (target / "dockcheck.yml").write_text(default_config)

    click.echo(f"Initialized .dockcheck/ with template '{template}'")
    click.echo(f"  - {dockcheck_dir / 'policy.yaml'}")
    click.echo(f"  - {target / 'dockcheck.yml'}")
    click.echo("\nNext steps:")
    click.echo("  1. Edit .dockcheck/policy.yaml to customize safety rules")
    click.echo("  2. Run `dockcheck check` to validate your policy")
    click.echo("  3. Run `dockcheck run` to execute the pipeline")


@cli.command()
@click.option("--policy", "policy_path", type=click.Path(), default=None, help="Path to policy.yaml")
@click.option("--diff", "diff_source", type=click.Path(), default=None, help="Path to diff file (or - for stdin)")
@click.option("--commands", multiple=True, help="Commands to check against hard stops")
@click.option("--files", multiple=True, help="File paths to check against critical paths")
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
        click.echo(f"Auto-deploy staging threshold: {engine.policy.confidence_thresholds.auto_deploy_staging}")
        click.echo(f"Auto-promote prod threshold: {engine.policy.confidence_thresholds.auto_promote_prod}")
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
