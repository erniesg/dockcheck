"""Tests for workflow generation with lint/test/format steps."""

from __future__ import annotations

import yaml

from dockcheck.github.action import WorkflowConfig, generate_workflow


class TestWorkflowWithLintSteps:
    def test_lint_step_included(self):
        cfg = WorkflowConfig(lint_command="ruff check .")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Lint" in step_names

    def test_format_step_included(self):
        cfg = WorkflowConfig(format_command="ruff format --check .")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Format check" in step_names

    def test_test_step_included(self):
        cfg = WorkflowConfig(test_command="pytest")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Test" in step_names

    def test_build_step_included(self):
        cfg = WorkflowConfig(build_command="npm run build")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Build" in step_names

    def test_no_steps_when_none(self):
        cfg = WorkflowConfig()
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Lint" not in step_names
        assert "Format check" not in step_names
        assert "Test" not in step_names
        assert "Build" not in step_names

    def test_js_project_uses_node_setup(self):
        cfg = WorkflowConfig(language="javascript")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Set up Node.js" in step_names

    def test_js_project_includes_npm_ci(self):
        cfg = WorkflowConfig(language="javascript")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        install_steps = [s for s in steps if s.get("name") == "Install dependencies"]
        assert len(install_steps) == 1
        assert "npm ci" in install_steps[0]["run"]

    def test_python_project_uses_python_setup(self):
        cfg = WorkflowConfig(language="python")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Set up Python" in step_names
        assert "Set up Node.js" not in step_names

    def test_js_project_with_all_steps(self):
        cfg = WorkflowConfig(
            language="javascript",
            lint_command="npm run lint",
            format_command="npx prettier --check .",
            test_command="npm test",
            build_command="npm run build",
            deploy_provider="cloudflare",
            deploy_secrets={"apiToken": "CLOUDFLARE_API_TOKEN"},
        )
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]

        # Verify order: setup → deps → lint → format → test → build → dockcheck → deploy
        assert "Set up Node.js" in step_names
        assert "Install dependencies" in step_names
        assert "Lint" in step_names
        assert "Format check" in step_names
        assert "Test" in step_names
        assert "Build" in step_names
        assert "Deploy to Cloudflare Workers" in step_names

        # Lint comes before test
        lint_idx = step_names.index("Lint")
        test_idx = step_names.index("Test")
        assert lint_idx < test_idx

    def test_custom_install_command(self):
        cfg = WorkflowConfig(
            language="javascript",
            install_command="pnpm install --frozen-lockfile",
        )
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        install_steps = [s for s in steps if s.get("name") == "Install dependencies"]
        assert len(install_steps) == 1
        assert "pnpm install" in install_steps[0]["run"]

    def test_js_project_adds_python_for_dockcheck(self):
        """JS projects should also set up Python for dockcheck itself."""
        cfg = WorkflowConfig(language="javascript")
        wf = generate_workflow(cfg)
        parsed = yaml.safe_load(wf)
        steps = parsed["jobs"]["dockcheck"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert "Set up Python (for dockcheck)" in step_names
