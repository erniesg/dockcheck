"""Preflight checks — validates a project is ready to deploy."""

from __future__ import annotations

import shutil

from pydantic import BaseModel, Field

from dockcheck.init.detect import RepoContext


class PreflightItem(BaseModel):
    """One item in the preflight checklist."""

    name: str
    passed: bool
    message: str
    required: bool = True
    fix_hint: str = ""


class PreflightResult(BaseModel):
    """Aggregated preflight result."""

    items: list[PreflightItem] = Field(default_factory=list)
    ready: bool = False
    provider_name: str | None = None
    needs_init: bool = False
    needs_auth: bool = False
    missing_secrets: list[str] = Field(default_factory=list)
    missing_cli: str | None = None
    install_hint: str = ""

    @property
    def blocking(self) -> list[PreflightItem]:
        return [i for i in self.items if not i.passed and i.required]


class PreflightChecker:
    """Runs preflight checks to determine if a project can deploy."""

    def check(self, path: str = ".", ctx: RepoContext | None = None) -> PreflightResult:
        """Run all preflight checks and return the result."""
        from pathlib import Path

        from dockcheck.init.auth import AuthBootstrapper
        from dockcheck.init.detect import RepoDetector
        from dockcheck.init.providers import ProviderRegistry

        target = Path(path).resolve()
        items: list[PreflightItem] = []

        # 1. Detect repo context
        if ctx is None:
            detector = RepoDetector()
            ctx = detector.detect(str(target))

        items.append(PreflightItem(
            name="language",
            passed=ctx.language is not None,
            message=f"Detected: {ctx.language}" if ctx.language else "No language detected",
            required=False,
            fix_hint="Add a package.json, pyproject.toml, go.mod, or Cargo.toml",
        ))

        # 2. Detect deploy provider
        registry = ProviderRegistry()
        detected = registry.detect(ctx)
        provider = detected[0] if detected else None
        provider_name = provider.name if provider else None

        items.append(PreflightItem(
            name="provider",
            passed=provider is not None,
            message=(
                f"Detected: {provider.display_name}" if provider
                else "No deploy target detected"
            ),
            required=True,
            fix_hint=(
                "Add a config file: wrangler.toml (Cloudflare), vercel.json (Vercel), "
                "fly.toml (Fly.io), netlify.toml (Netlify), Dockerfile (Docker), "
                "template.yaml (AWS SAM), cloudbuild.yaml (Cloud Run), "
                "railway.json (Railway), render.yaml (Render)"
            ),
        ))

        if not provider:
            return PreflightResult(
                items=items,
                ready=False,
                provider_name=None,
            )

        # 3. Check CLI tool is installed (delegate to DeployProvider when available)
        from dockcheck.tools.deploy import DeployProviderFactory

        try:
            dp = DeployProviderFactory.get(provider.name)
            cli_available = dp.is_available()
        except KeyError:
            cli_available = shutil.which(provider.cli_tool) is not None

        if provider.name == "render":
            cli_message = (
                "RENDER_DEPLOY_HOOK_URL set" if cli_available
                else "RENDER_DEPLOY_HOOK_URL not set"
            )
        else:
            cli_message = (
                f"{provider.cli_tool} found on PATH" if cli_available
                else f"{provider.cli_tool} not found on PATH"
            )

        cli_fix_hint = provider.install_hint or f"Install {provider.cli_tool}"

        items.append(PreflightItem(
            name="cli_tool",
            passed=cli_available,
            message=cli_message,
            required=True,
            fix_hint=cli_fix_hint,
        ))

        # 4. Check .dockcheck/ exists
        has_dockcheck = (target / ".dockcheck" / "policy.yaml").exists()
        needs_init = not has_dockcheck

        items.append(PreflightItem(
            name="init",
            passed=has_dockcheck,
            message=(
                ".dockcheck/ configured" if has_dockcheck
                else ".dockcheck/ not found — will auto-initialize"
            ),
            required=False,  # We can auto-fix this
        ))

        # 5. Check auth / secrets
        auth = AuthBootstrapper(env_file=str(target / ".env"))
        auth_status = auth.check(provider)
        missing_required = [
            s.name for s in auth_status.secrets
            if not s.available_local and s.required
        ]
        missing_optional = [
            s.name for s in auth_status.secrets
            if not s.available_local and not s.required
        ]

        auth_message_parts: list[str] = []
        if auth_status.all_ready and not missing_optional:
            auth_message_parts.append("All secrets available")
        elif auth_status.all_ready and missing_optional:
            auth_message_parts.append(
                f"Required secrets available. Optional: {', '.join(missing_optional)}"
            )
        else:
            auth_message_parts.append(f"Missing: {', '.join(missing_required)}")
            if missing_optional:
                auth_message_parts.append(f"Optional: {', '.join(missing_optional)}")

        items.append(PreflightItem(
            name="auth",
            passed=auth_status.all_ready,
            message=". ".join(auth_message_parts),
            required=True,
            fix_hint="\n".join(
                f"  {s.name} — get at: {s.setup_url}"
                for s in auth_status.secrets
                if not s.available_local and s.required
            ),
        ))

        # 6. Check .gitignore covers secrets
        items.append(PreflightItem(
            name="gitignore",
            passed=ctx.gitignore_covers_env,
            message=(
                ".gitignore covers .env" if ctx.gitignore_covers_env
                else ".gitignore missing .env pattern — will auto-fix"
            ),
            required=False,  # We can auto-fix this
        ))

        # 7. Lint/test detection (advisory)
        if ctx.lint_command:
            items.append(PreflightItem(
                name="lint",
                passed=True,
                message=f"Lint: {ctx.lint_command}",
                required=False,
            ))

        if ctx.test_command:
            items.append(PreflightItem(
                name="test",
                passed=True,
                message=f"Test: {ctx.test_command}",
                required=False,
            ))

        blocking = [i for i in items if not i.passed and i.required]
        ready = len(blocking) == 0

        return PreflightResult(
            items=items,
            ready=ready,
            provider_name=provider_name,
            needs_init=needs_init,
            needs_auth=not auth_status.all_ready,
            missing_secrets=missing_required,
            missing_cli=provider.cli_tool if not cli_available else None,
            install_hint=cli_fix_hint if not cli_available else "",
        )
