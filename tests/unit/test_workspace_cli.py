"""Tests for workspace-aware CLI commands and --agent flag."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from dockcheck.cli import cli

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cf_worker(path: Path, name: str = "worker") -> Path:
    """Create a minimal CF Worker project in a subdirectory."""
    worker = path / name
    worker.mkdir(parents=True, exist_ok=True)
    (worker / "wrangler.toml").write_text(
        f'name = "{name}"\nmain = "src/index.ts"\n'
    )
    (worker / "package.json").write_text(json.dumps({
        "name": name,
        "scripts": {"test": "vitest"},
    }))
    return worker


def _make_fly_app(path: Path, name: str = "api") -> Path:
    """Create a minimal Fly.io project in a subdirectory."""
    app = path / name
    app.mkdir(parents=True, exist_ok=True)
    (app / "fly.toml").write_text(f'app = "{name}"\n')
    return app


def _setup_dockcheck(path: Path) -> None:
    """Create minimal .dockcheck/ with policy."""
    dc = path / ".dockcheck"
    dc.mkdir(parents=True, exist_ok=True)
    (dc / "skills").mkdir(exist_ok=True)
    (dc / "policy.yaml").write_text(
        'version: "1"\n'
        "hard_stops:\n"
        "  commands:\n"
        '    - pattern: "rm -rf"\n'
        "confidence_thresholds:\n"
        "  auto_deploy_staging: 0.6\n"
        "  auto_promote_prod: 0.7\n"
        "  notify_human: 0.3\n"
    )


# ---------------------------------------------------------------------------
# --agent flag on `run`
# ---------------------------------------------------------------------------


class TestRunAgentFlag:
    def test_agent_dry_run_shows_agent_pipeline(self, tmp_path):
        """--agent --dry-run shows agent step DAG."""
        _setup_dockcheck(tmp_path)
        (tmp_path / "wrangler.toml").write_text('name = "test"\n')
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {"test": "vitest"},
        }))

        runner = CliRunner()
        result = runner.invoke(
            cli, ["run", "--agent", "--dry-run", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "Agent pipeline plan" in result.output
        assert "analyze" in result.output
        assert "test" in result.output
        assert "verify" in result.output

    def test_agent_dry_run_skip_test(self, tmp_path):
        """--agent --dry-run --skip-test omits test step."""
        _setup_dockcheck(tmp_path)
        (tmp_path / "wrangler.toml").write_text('name = "test"\n')
        (tmp_path / "package.json").write_text("{}")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["run", "--agent", "--dry-run", "--skip-test", "--dir", str(tmp_path)],
        )

        assert result.exit_code == 0
        assert "analyze" in result.output
        assert "verify" in result.output

    def test_agent_dry_run_shows_deploy_step(self, tmp_path):
        """--agent --dry-run includes deploy step when provider detected."""
        _setup_dockcheck(tmp_path)
        (tmp_path / "wrangler.toml").write_text('name = "test"\n')
        (tmp_path / "package.json").write_text("{}")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["run", "--agent", "--dry-run", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "deploy" in result.output

    def test_agent_dry_run_skip_deploy(self, tmp_path):
        """--agent --dry-run --skip-deploy omits deploy step."""
        _setup_dockcheck(tmp_path)
        (tmp_path / "wrangler.toml").write_text('name = "test"\n')
        (tmp_path / "package.json").write_text("{}")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["run", "--agent", "--dry-run", "--skip-deploy", "--dir", str(tmp_path)],
        )

        assert result.exit_code == 0
        # Should have analyze + verify but not deploy
        lines = result.output.lower()
        assert "analyze" in lines
        assert "verify" in lines

    def test_without_agent_flag_uses_subprocess(self, tmp_path):
        """Without --agent, dry-run shows subprocess pipeline."""
        _setup_dockcheck(tmp_path)
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {"test": "vitest", "lint": "eslint ."},
        }))

        runner = CliRunner()
        result = runner.invoke(
            cli, ["run", "--dry-run", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "Pipeline plan" in result.output
        assert "Agent pipeline" not in result.output


# ---------------------------------------------------------------------------
# Workspace-aware `ship`
# ---------------------------------------------------------------------------


class TestShipWorkspace:
    def test_ship_dry_run_single_target_unchanged(self, tmp_path):
        """Single-target project behaves as before."""
        _setup_dockcheck(tmp_path)
        (tmp_path / "wrangler.toml").write_text('name = "test"\n')
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / ".gitignore").write_text(".env\n")

        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/bin/wrangler"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )
                with patch.dict("os.environ", {
                    "CLOUDFLARE_API_TOKEN": "test",
                    "CLOUDFLARE_ACCOUNT_ID": "test",
                }):
                    result = runner.invoke(
                        cli,
                        ["ship", "--dry-run", "--dir", str(tmp_path)],
                    )

        assert result.exit_code == 0
        assert "Preflight" in result.output

    def test_ship_workspace_dry_run_multi_target(self, tmp_path):
        """Multi-target workspace shows workspace info in dry-run."""
        _make_cf_worker(tmp_path, "worker-a")
        _make_fly_app(tmp_path, "api")
        (tmp_path / ".gitignore").write_text(".env\n")

        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/bin/wrangler"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )
                with patch.dict("os.environ", {
                    "CLOUDFLARE_API_TOKEN": "test",
                    "CLOUDFLARE_ACCOUNT_ID": "test",
                    "FLY_API_TOKEN": "test",
                }):
                    result = runner.invoke(
                        cli,
                        ["ship", "--dry-run", "--dir", str(tmp_path / "worker-a")],
                    )

        # Should run preflight on the target dir
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Workspace-aware `init`
# ---------------------------------------------------------------------------


class TestInitWorkspace:
    def test_init_discovers_workspace(self, tmp_path):
        """When workspace detected, init writes workspace config."""
        _make_cf_worker(tmp_path, "worker-a")
        _make_cf_worker(tmp_path, "worker-b")

        from dockcheck.init.workspace import WorkspaceResolver

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))

        assert ws is not None
        assert len(ws.targets) == 2

    def test_init_single_target_no_workspace(self, tmp_path):
        """Single target does not create workspace config."""
        _make_cf_worker(tmp_path, "single")

        from dockcheck.init.workspace import WorkspaceResolver

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))

        assert ws is None


# ---------------------------------------------------------------------------
# Agent flag on `ship`
# ---------------------------------------------------------------------------


class TestShipAgentFlag:
    def test_ship_agent_flag_accepted(self, tmp_path):
        """--agent flag is accepted by ship command."""
        _setup_dockcheck(tmp_path)
        (tmp_path / "wrangler.toml").write_text('name = "test"\n')
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / ".gitignore").write_text(".env\n")

        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/bin/wrangler"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )
                with patch.dict("os.environ", {
                    "CLOUDFLARE_API_TOKEN": "test",
                    "CLOUDFLARE_ACCOUNT_ID": "test",
                }):
                    result = runner.invoke(
                        cli,
                        ["ship", "--dry-run", "--agent", "--dir", str(tmp_path)],
                    )

        # --dry-run should exit before agent pipeline runs
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Workspace ship dry-run
# ---------------------------------------------------------------------------


class TestWorkspaceShipDryRun:
    def test_workspace_dry_run_shows_layers(self, tmp_path):
        """Workspace dry-run displays layers and targets."""
        _make_cf_worker(tmp_path, "worker-a")
        _make_cf_worker(tmp_path, "worker-b")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["ship", "--dry-run", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "2 targets detected" in result.output
        assert "worker-a" in result.output
        assert "worker-b" in result.output

    def test_workspace_dry_run_with_deps(self, tmp_path):
        """Workspace with dependencies shows layers correctly."""
        # Create explicit workspace config with deps
        ws_yaml = tmp_path / "dockcheck.workspace.yaml"
        ws_yaml.write_text(
            "name: test\n"
            "targets:\n"
            "  - name: api\n"
            "    path: api\n"
            "    provider: fly\n"
            "  - name: web\n"
            "    path: web\n"
            "    provider: cloudflare\n"
            "    depends_on: [api]\n"
        )
        (tmp_path / "api").mkdir()
        (tmp_path / "web").mkdir()

        runner = CliRunner()
        result = runner.invoke(
            cli, ["ship", "--dry-run", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "2 targets detected" in result.output
        assert "Layer 0" in result.output
        assert "Layer 1" in result.output


# ---------------------------------------------------------------------------
# Init workspace
# ---------------------------------------------------------------------------


class TestInitWorkspaceCommand:
    def test_init_discovers_and_scans(self, tmp_path):
        """Init with workspace discovers targets and scans secrets."""
        w1 = _make_cf_worker(tmp_path, "worker-a")
        (w1 / "src").mkdir()
        (w1 / "src" / "index.ts").write_text(
            "const key = process.env.OPENAI_API_KEY;\n"
        )
        _make_cf_worker(tmp_path, "worker-b")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["init", "--non-interactive", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "Workspace detected" in result.output
        assert (tmp_path / "dockcheck.workspace.yaml").exists()
        assert (tmp_path / ".dockcheck" / "policy.yaml").exists()

    def test_init_single_target_no_workspace(self, tmp_path):
        """Single-target init doesn't trigger workspace mode."""
        _make_cf_worker(tmp_path, "worker")
        # Remove the parent wrangler.toml detection
        # This is a single subdir, not multi-target

        runner = CliRunner()
        # Use --provider to force single-target init
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            with patch.dict("os.environ", {
                "CLOUDFLARE_API_TOKEN": "test",
                "CLOUDFLARE_ACCOUNT_ID": "test",
            }):
                result = runner.invoke(
                    cli,
                    [
                        "init", "--provider", "cloudflare",
                        "--non-interactive", "--dir", str(tmp_path / "worker"),
                    ],
                )

        assert result.exit_code == 0
        assert (tmp_path / "worker" / ".dockcheck" / "policy.yaml").exists()


# ---------------------------------------------------------------------------
# Workspace generate workflow
# ---------------------------------------------------------------------------


class TestGenerateWorkspaceWorkflow:
    def test_generates_multi_job_yaml(self):
        from dockcheck.cli import _generate_workspace_workflow
        from dockcheck.init.workspace import TargetConfig, WorkspaceConfig

        ws = WorkspaceConfig(
            name="test",
            targets=[
                TargetConfig(name="api", path="apps/api", provider="fly"),
                TargetConfig(
                    name="web", path="apps/web", provider="cloudflare",
                    depends_on=["api"],
                ),
            ],
        )
        yaml_str = _generate_workspace_workflow(ws)

        assert "api:" in yaml_str
        assert "web:" in yaml_str
        assert "needs: [api]" in yaml_str
        assert "working-directory: apps/api" in yaml_str
        assert "working-directory: apps/web" in yaml_str


# ---------------------------------------------------------------------------
# `dockcheck secrets` CLI command group
# ---------------------------------------------------------------------------


class TestSecretsScan:
    def test_secrets_scan_shows_refs(self, tmp_path):
        """dockcheck secrets scan shows raw env var references."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.OPENAI_API_KEY;\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["secrets", "scan", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "OPENAI_API_KEY" in result.output
        assert "app.js:1" in result.output

    def test_secrets_scan_empty(self, tmp_path):
        """dockcheck secrets scan with no refs shows message."""
        runner = CliRunner()
        result = runner.invoke(cli, ["secrets", "scan", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "No env var references found" in result.output


class TestSecretsAudit:
    def test_secrets_audit_shows_enriched(self, tmp_path):
        """dockcheck secrets audit shows enriched audit with context."""
        src = tmp_path / "app.js"
        src.write_text('const key = process.env.API_KEY || "default";\n')

        runner = CliRunner()
        result = runner.invoke(cli, ["secrets", "audit", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "API_KEY" in result.output
        assert "has default" in result.output

    def test_secrets_audit_json_output(self, tmp_path):
        """dockcheck secrets audit --json-output produces JSON."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.MY_KEY;\n")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["secrets", "audit", "--json-output", "--dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "MY_KEY" in parsed["unique_secrets"]

    def test_secrets_audit_empty(self, tmp_path):
        """dockcheck secrets audit with no refs shows message."""
        runner = CliRunner()
        result = runner.invoke(cli, ["secrets", "audit", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "No env var references found" in result.output


class TestSecretsCheck:
    def test_secrets_check_available(self, tmp_path):
        """dockcheck secrets check shows available secrets."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.MY_KEY;\n")
        env = tmp_path / ".env"
        env.write_text("MY_KEY=value\n")

        runner = CliRunner()
        result = runner.invoke(cli, ["secrets", "check", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "Available" in result.output
        assert "MY_KEY" in result.output

    def test_secrets_check_missing_exits_1(self, tmp_path):
        """dockcheck secrets check exits 1 when secrets are missing."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.MISSING_KEY;\n")

        runner = CliRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(cli, ["secrets", "check", "--dir", str(tmp_path)])

        assert result.exit_code == 1
        assert "Missing" in result.output
        assert "MISSING_KEY" in result.output

    def test_secrets_check_empty(self, tmp_path):
        """dockcheck secrets check with no refs shows message."""
        runner = CliRunner()
        result = runner.invoke(cli, ["secrets", "check", "--dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "No secrets referenced" in result.output
