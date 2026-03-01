"""CLI entrypoint — dockcheck init, check, run, deploy, ship, validate."""

from __future__ import annotations

import subprocess
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
    type=click.Choice(["cloudflare", "vercel", "fly", "netlify", "docker-registry", "aws-lambda", "gcp-cloudrun", "railway", "render"]),
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
    elif ctx.has_sam_config:
        parts.append("Config: template.yaml")
    elif ctx.has_cloudrun_config:
        parts.append("Config: cloudbuild.yaml")
    elif ctx.has_railway_config:
        parts.append("Config: railway.json")
    elif ctx.has_render_config:
        parts.append("Config: render.yaml")
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

    # Generate provider-aware workflow with lint/test/build commands
    env_secrets = [s.name for s in selected.required_secrets]
    wf_config = WorkflowConfig(
        trigger_on_push=True,
        env_secrets=env_secrets,
        deploy_provider=selected.name,
        deploy_secrets=selected.github_action_secrets,
        language=ctx.language,
        lint_command=ctx.lint_command,
        format_command=ctx.format_command,
        test_command=ctx.test_command,
        build_command=ctx.build_command,
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
@click.option(
    "--dir", "target_dir", type=click.Path(), default=".",
    help="Project directory.",
)
@click.option("--dry-run", is_flag=True, help="Show pipeline plan without executing")
@click.option("--skip-lint", is_flag=True, help="Skip lint step")
@click.option("--skip-test", is_flag=True, help="Skip test step")
@click.option("--skip-deploy", is_flag=True, help="Skip deploy step")
def run(
    policy_path: str | None,
    target_dir: str,
    dry_run: bool,
    skip_lint: bool,
    skip_test: bool,
    skip_deploy: bool,
) -> None:
    """Execute the full CI/CD pipeline: lint -> test -> check -> deploy."""
    from dockcheck.init.detect import RepoDetector

    target = Path(target_dir).resolve()

    # Detect project context
    detector = RepoDetector()
    ctx = detector.detect(str(target))

    # Build pipeline steps
    steps: list[tuple[str, str | None]] = []

    if not skip_lint and ctx.lint_command:
        steps.append(("LINT", ctx.lint_command))
    if not skip_lint and ctx.format_command:
        steps.append(("FORMAT", ctx.format_command))
    if not skip_test and ctx.test_command:
        steps.append(("TEST", ctx.test_command))

    # Policy check
    steps.append(("CHECK", "dockcheck check"))

    # Deploy
    deploy_provider_name = _detect_deploy_provider(target, ctx)
    if not skip_deploy and deploy_provider_name:
        steps.append(("DEPLOY", f"deploy:{deploy_provider_name}"))

    if dry_run:
        click.echo("Pipeline plan (dry run):")
        for i, (name, cmd) in enumerate(steps, 1):
            click.echo(f"  {i}. {name:<8} — {cmd}")
        click.echo(f"\nProject: {target}")
        click.echo(f"Language: {ctx.language or 'unknown'}")
        if deploy_provider_name:
            click.echo(f"Deploy target: {deploy_provider_name}")
        # Show policy info if available
        try:
            policy_file = _find_policy_quiet(policy_path, target)
            if policy_file:
                engine = PolicyEngine.from_yaml(policy_file)
                thresholds = engine.policy.confidence_thresholds
                click.echo(f"Auto-deploy threshold: {thresholds.auto_deploy_staging}")
        except Exception:
            pass
        return

    # Execute pipeline (run = no deploy by default)
    _run_pipeline(
        target=target,
        provider_name=deploy_provider_name if not skip_deploy else None,
        skip_lint=skip_lint,
        skip_test=skip_test,
        include_deploy=not skip_deploy and deploy_provider_name is not None,
    )


@cli.command()
@click.option(
    "--provider", type=click.Choice(["cloudflare", "vercel", "fly", "netlify", "docker-registry", "aws-lambda", "gcp-cloudrun", "railway", "render"]),
    default=None, help="Deploy provider (auto-detected if not set).",
)
@click.option(
    "--dir", "target_dir", type=click.Path(), default=".",
    help="Project directory.",
)
def deploy(provider: str | None, target_dir: str) -> None:
    """Deploy the project using the detected or specified provider.

    Assumes the project is already initialized and checks have passed.
    For the full workflow, use `dockcheck ship` instead.
    """
    from dockcheck.init.detect import RepoDetector

    target = Path(target_dir).resolve()

    if not provider:
        detector = RepoDetector()
        ctx = detector.detect(str(target))
        provider = _detect_deploy_provider(target, ctx)

    if not provider:
        click.echo("Error: no deploy provider detected.", err=True)
        click.echo(
            "Use --provider to specify one, or `dockcheck ship` for the full workflow.",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Deploying via {provider}...")
    ok = _run_deploy(provider, str(target))
    if not ok:
        sys.exit(1)


@cli.command()
@click.option(
    "--provider", type=click.Choice(["cloudflare", "vercel", "fly", "netlify", "docker-registry", "aws-lambda", "gcp-cloudrun", "railway", "render"]),
    default=None, help="Deploy provider (auto-detected if not set).",
)
@click.option(
    "--dir", "target_dir", type=click.Path(), default=".",
    help="Project directory.",
)
@click.option("--skip-lint", is_flag=True, help="Skip lint step")
@click.option("--skip-test", is_flag=True, help="Skip test step")
@click.option("--dry-run", is_flag=True, help="Run preflight only, don't deploy")
@click.option(
    "--non-interactive", is_flag=True,
    help="Skip interactive prompts (fail if secrets missing).",
)
def ship(
    provider: str | None,
    target_dir: str,
    skip_lint: bool,
    skip_test: bool,
    dry_run: bool,
    non_interactive: bool,
) -> None:
    """Ship it: preflight -> init -> auth -> lint -> test -> check -> deploy.

    The magic command. Auto-detects everything, initializes if needed,
    bootstraps auth, runs quality checks, and deploys to production.

    \b
    Examples:
        dockcheck ship                    # full flow, interactive
        dockcheck ship --non-interactive  # CI mode, fail on missing secrets
        dockcheck ship --dry-run          # preflight only
        dockcheck ship --skip-test        # skip tests, ship fast
    """
    from dockcheck.init.preflight import PreflightChecker

    target = Path(target_dir).resolve()

    # --- Preflight -----------------------------------------------------------
    click.echo("Preflight checks...\n")
    checker = PreflightChecker()
    preflight = checker.check(str(target))

    # Override provider if explicitly set
    if provider:
        preflight.provider_name = provider

    # Display checklist
    for item in preflight.items:
        icon = "ok" if item.passed else ("--" if not item.required else "FAIL")
        click.echo(f"  [{icon:>4}] {item.name}: {item.message}")

    click.echo()

    # Handle blockers
    if preflight.missing_cli:
        click.echo(
            f"Error: {preflight.missing_cli} CLI not found on PATH.", err=True
        )
        if preflight.install_hint:
            click.echo(
                f"Install it first: {preflight.install_hint}",
                err=True,
            )
        sys.exit(1)

    if not preflight.provider_name:
        click.echo("Error: no deploy target detected.", err=True)
        click.echo(
            "Add a config file (wrangler.toml, vercel.json, fly.toml, netlify.toml, "
            "Dockerfile, template.yaml, cloudbuild.yaml, railway.json, render.yaml) "
            "or use --provider.",
            err=True,
        )
        sys.exit(1)

    # --- Auth bootstrap (if needed) ------------------------------------------
    if preflight.needs_auth:
        from dockcheck.init.auth import AuthBootstrapper
        from dockcheck.init.providers import ProviderRegistry

        registry = ProviderRegistry()
        prov_spec = registry.get(preflight.provider_name)
        auth = AuthBootstrapper(env_file=str(target / ".env"))

        if non_interactive:
            click.echo("Missing secrets (set in env or .env):")
            for name in preflight.missing_secrets:
                matching = [
                    s for s in prov_spec.required_secrets if s.name == name
                ]
                url = matching[0].setup_url if matching else ""
                click.echo(f"  {name} — {url}")
            sys.exit(1)

        click.echo("Auth setup required:")
        status = auth.check(prov_spec)
        collected = auth.prompt_missing(status)

        if collected:
            auth.store_local(collected, env_file=str(target / ".env"))
            click.echo("Stored to .env")
            auth.ensure_gitignore(str(target))

            set_gh = click.confirm("Set as GitHub Secrets?", default=True)
            if set_gh:
                ok = auth.store_github(collected)
                click.echo("  Done" if ok else "  Partial (check warnings)")
        click.echo()

    # --- Auto-init (if needed) -----------------------------------------------
    if preflight.needs_init:
        click.echo("Initializing .dockcheck/...")
        dockcheck_dir = target / ".dockcheck"
        _auto_init(target, dockcheck_dir, preflight.provider_name)
        click.echo()

    if dry_run:
        click.echo("Preflight passed. Use without --dry-run to ship.")
        return

    # --- Run pipeline: lint -> test -> check -> deploy -----------------------
    _run_pipeline(
        target=target,
        provider_name=preflight.provider_name,
        skip_lint=skip_lint,
        skip_test=skip_test,
        include_deploy=True,
    )


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


def _auto_init(
    target: Path, dockcheck_dir: Path, provider_name: str
) -> None:
    """Lightweight auto-init: generate policy + workflow without prompts."""
    from dockcheck.github.action import WorkflowConfig, write_workflow
    from dockcheck.init.detect import RepoDetector
    from dockcheck.init.providers import ProviderRegistry

    detector = RepoDetector()
    ctx = detector.detect(str(target))
    registry = ProviderRegistry()
    prov_spec = registry.get(provider_name)

    dockcheck_dir.mkdir(parents=True, exist_ok=True)
    (dockcheck_dir / "skills").mkdir(exist_ok=True)

    policy = _default_policy("hackathon")
    (dockcheck_dir / "policy.yaml").write_text(policy)

    env_secrets = [s.name for s in prov_spec.required_secrets]
    wf_config = WorkflowConfig(
        trigger_on_push=True,
        env_secrets=env_secrets,
        deploy_provider=prov_spec.name,
        deploy_secrets=prov_spec.github_action_secrets,
        language=ctx.language,
        lint_command=ctx.lint_command,
        format_command=ctx.format_command,
        test_command=ctx.test_command,
        build_command=ctx.build_command,
    )
    wf_path = write_workflow(str(target), wf_config)

    click.echo(f"  Generated: {dockcheck_dir / 'policy.yaml'}")
    click.echo(f"  Generated: {wf_path}")


def _run_pipeline(
    target: Path,
    provider_name: str | None = None,
    skip_lint: bool = False,
    skip_test: bool = False,
    include_deploy: bool = False,
) -> None:
    """Execute the pipeline: lint -> format -> test -> check -> deploy.

    Exits with code 1 on the first step that fails.
    """
    from dockcheck.init.detect import RepoDetector

    detector = RepoDetector()
    ctx = detector.detect(str(target))

    # Build step list
    steps: list[tuple[str, str]] = []

    if not skip_lint and ctx.lint_command:
        steps.append(("LINT", ctx.lint_command))
    if not skip_lint and ctx.format_command:
        steps.append(("FORMAT", ctx.format_command))
    if not skip_test and ctx.test_command:
        steps.append(("TEST", ctx.test_command))

    steps.append(("CHECK", "dockcheck check"))

    if include_deploy and provider_name:
        steps.append(("DEPLOY", f"deploy:{provider_name}"))

    click.echo("Running pipeline...\n")
    for i, (name, cmd) in enumerate(steps, 1):
        click.echo(f"  [{i}/{len(steps)}] {name}: {cmd}")

        if name == "CHECK":
            policy_file = _find_policy_quiet(None, target)
            if policy_file:
                engine = PolicyEngine.from_yaml(policy_file)
                result = engine.evaluate()
                if result.verdict == Verdict.BLOCK:
                    click.echo(
                        "  FAILED: policy check blocked the deploy.", err=True
                    )
                    for reason in result.reasons:
                        click.echo(f"    - {reason}", err=True)
                    sys.exit(1)
                click.echo(f"  -> {result.verdict.value.upper()}")
            else:
                click.echo("  -> skipped (no policy.yaml found)")
            continue

        if name == "DEPLOY":
            ok = _run_deploy(provider_name, str(target))
            if not ok:
                click.echo(
                    f"\n  Deploy failed. Check that {provider_name} CLI is "
                    "installed and credentials are set.",
                    err=True,
                )
                sys.exit(1)
            continue

        # Lint/format/test — run as subprocess
        rc = _run_command(cmd, cwd=str(target))
        if rc != 0:
            click.echo(f"\n  {name} failed (exit code {rc}).", err=True)
            if name == "LINT":
                click.echo(
                    "  Hint: fix lint errors above, or use --skip-lint to skip.",
                    err=True,
                )
            elif name == "FORMAT":
                click.echo(
                    "  Hint: run the formatter to auto-fix, or use --skip-lint to skip.",
                    err=True,
                )
            elif name == "TEST":
                click.echo(
                    "  Hint: fix failing tests above, or use --skip-test to skip.",
                    err=True,
                )
            sys.exit(1)
        click.echo("  -> passed")

    click.echo("\nPipeline complete!")


def _find_policy_quiet(
    path: str | None, target: Path | None = None
) -> Path | None:
    """Like _find_policy but returns None instead of exiting."""
    if path:
        p = Path(path)
        return p if p.exists() else None

    bases = [target] if target else [Path(".")]
    for base in bases:
        candidates = [
            base / ".dockcheck" / "policy.yaml",
            base / "policy.yaml",
            Path(".dockcheck/policy.yaml"),
            Path("policy.yaml"),
        ]
        for c in candidates:
            if c.exists():
                return c
    return None


def _detect_deploy_provider(target: Path, ctx: object) -> str | None:
    """Detect the deploy provider from project config."""
    from dockcheck.init.providers import ProviderRegistry

    registry = ProviderRegistry()
    detected = registry.detect(ctx)
    if detected:
        return detected[0].name
    return None


def _run_command(cmd: str, cwd: str) -> int:
    """Run a shell command, streaming output. Returns exit code."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            timeout=300,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        click.echo("  Timed out after 300 seconds", err=True)
        return 124
    except Exception as exc:
        click.echo(f"  Error: {exc}", err=True)
        return 1


def _run_deploy(provider_name: str, workdir: str) -> bool:
    """Run deploy via provider. Returns True on success."""
    from dockcheck.tools.deploy import DeployProviderFactory

    try:
        provider = DeployProviderFactory.get(provider_name)
    except KeyError as exc:
        click.echo(f"  Error: {exc}", err=True)
        return False

    if not provider.is_available():
        click.echo(f"  Error: {provider_name} CLI not found on PATH.", err=True)
        click.echo("  Install it first, then retry.", err=True)
        return False

    # Load env from .env if it exists
    env = _load_env_file(workdir)

    result = provider.deploy(workdir=workdir, env=env)

    if result.success:
        click.echo("  Deployed successfully!")
        if result.url:
            click.echo(f"  Live URL: {result.url}")
        return True
    else:
        click.echo(f"  Deploy failed: {result.error or 'unknown error'}", err=True)
        if result.stderr:
            click.echo(f"  stderr: {result.stderr[:500]}", err=True)
        return False


def _load_env_file(workdir: str) -> dict[str, str]:
    """Read key=value pairs from .env file if it exists."""
    env_file = Path(workdir) / ".env"
    env: dict[str, str] = {}
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
        except OSError:
            pass
    return env


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
