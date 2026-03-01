"""Tests for GitHub Action workflow generation and hook generation."""

import tempfile
from pathlib import Path

import pytest
import yaml

from dockcheck.github.action import (
    WorkflowConfig,
    generate_workflow,
    write_workflow,
)
from dockcheck.github.hooks import (
    HookConfig,
    generate_lefthook_yaml,
    generate_pre_commit_script,
    generate_pre_commit_yaml,
    install_hook,
)


class TestWorkflowGeneration:
    def test_default_workflow_is_valid_yaml(self):
        output = generate_workflow()
        parsed = yaml.safe_load(output)
        assert parsed["name"] == "dockcheck CI/CD"
        assert "jobs" in parsed
        assert "dockcheck" in parsed["jobs"]

    def test_default_triggers_on_pr(self):
        output = generate_workflow()
        parsed = yaml.safe_load(output)
        # YAML parses bare `on:` as True key — access via True
        triggers = parsed.get("on") or parsed.get(True)
        assert "pull_request" in triggers

    def test_push_trigger(self):
        config = WorkflowConfig(trigger_on_push=True)
        output = generate_workflow(config)
        parsed = yaml.safe_load(output)
        triggers = parsed.get("on") or parsed.get(True)
        assert "push" in triggers

    def test_env_secrets(self):
        config = WorkflowConfig(env_secrets=["ANTHROPIC_API_KEY", "OPENAI_API_KEY"])
        output = generate_workflow(config)
        parsed = yaml.safe_load(output)
        assert "ANTHROPIC_API_KEY" in parsed["env"]
        assert "OPENAI_API_KEY" in parsed["env"]

    def test_custom_python_version(self):
        config = WorkflowConfig(python_version="3.12")
        output = generate_workflow(config)
        assert "3.12" in output

    def test_custom_dockcheck_version(self):
        config = WorkflowConfig(dockcheck_version="0.1.0")
        output = generate_workflow(config)
        assert "dockcheck==0.1.0" in output

    def test_timeout_minutes(self):
        config = WorkflowConfig(timeout_minutes=60)
        output = generate_workflow(config)
        parsed = yaml.safe_load(output)
        assert parsed["jobs"]["dockcheck"]["timeout-minutes"] == 60

    def test_permissions(self):
        output = generate_workflow()
        parsed = yaml.safe_load(output)
        perms = parsed["jobs"]["dockcheck"]["permissions"]
        assert perms["contents"] == "read"
        assert perms["pull-requests"] == "write"

    def test_no_pr_comment(self):
        config = WorkflowConfig(post_pr_comment=False)
        output = generate_workflow(config)
        assert "Post results to PR" not in output

    def test_write_workflow_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_workflow(tmpdir)
            assert path.exists()
            assert path.name == "dockcheck.yml"
            assert ".github/workflows" in str(path)
            content = path.read_text()
            parsed = yaml.safe_load(content)
            assert "jobs" in parsed

    def test_steps_include_checkout(self):
        output = generate_workflow()
        assert "actions/checkout@v4" in output

    def test_steps_include_python_setup(self):
        output = generate_workflow()
        assert "actions/setup-python@v5" in output

    def test_steps_include_dockcheck_install(self):
        output = generate_workflow()
        assert "pip install dockcheck" in output

    def test_steps_include_policy_check(self):
        output = generate_workflow()
        assert "dockcheck check" in output


class TestDeployStepGeneration:
    """Tests for provider-specific deploy step generation."""

    def test_fly_deploy_step(self):
        config = WorkflowConfig(
            deploy_provider="fly",
            deploy_secrets={"FLY_API_TOKEN": "FLY_API_TOKEN"},
        )
        output = generate_workflow(config)
        assert "Deploy to Fly.io" in output
        assert "superfly/flyctl-actions/setup-flyctl@master" in output
        assert "fly deploy" in output

    def test_netlify_deploy_step(self):
        config = WorkflowConfig(
            deploy_provider="netlify",
            deploy_secrets={"netlify-auth-token": "NETLIFY_AUTH_TOKEN"},
        )
        output = generate_workflow(config)
        assert "Deploy to Netlify" in output
        assert "nwtgck/actions-netlify@v3" in output

    def test_docker_registry_deploy_step(self):
        config = WorkflowConfig(
            deploy_provider="docker-registry",
            deploy_secrets={"username": "DOCKER_USERNAME", "password": "DOCKER_PASSWORD"},
        )
        output = generate_workflow(config)
        assert "Docker" in output
        assert "docker/login-action@v3" in output
        assert "docker/build-push-action@v5" in output

    def test_aws_lambda_deploy_step(self):
        config = WorkflowConfig(
            deploy_provider="aws-lambda",
            deploy_secrets={
                "aws-access-key-id": "AWS_ACCESS_KEY_ID",
                "aws-secret-access-key": "AWS_SECRET_ACCESS_KEY",
                "aws-region": "AWS_REGION",
            },
        )
        output = generate_workflow(config)
        assert "AWS" in output
        assert "aws-actions/configure-aws-credentials@v4" in output
        assert "sam build" in output
        assert "sam deploy" in output

    def test_gcp_cloudrun_deploy_step(self):
        config = WorkflowConfig(
            deploy_provider="gcp-cloudrun",
            deploy_secrets={"credentials_json": "GCP_SERVICE_ACCOUNT_KEY"},
        )
        output = generate_workflow(config)
        assert "Cloud Run" in output
        assert "google-github-actions/auth@v2" in output
        assert "gcloud run deploy" in output

    def test_railway_deploy_step(self):
        config = WorkflowConfig(deploy_provider="railway")
        output = generate_workflow(config)
        assert "Railway" in output
        assert "railway up" in output

    def test_render_deploy_step(self):
        config = WorkflowConfig(deploy_provider="render")
        output = generate_workflow(config)
        assert "Render" in output
        assert "RENDER_DEPLOY_HOOK_URL" in output

    def test_no_deploy_step_when_no_provider(self):
        config = WorkflowConfig(deploy_provider=None)
        output = generate_workflow(config)
        assert "Deploy to" not in output

    def test_cloudflare_deploy_step_still_works(self):
        config = WorkflowConfig(
            deploy_provider="cloudflare",
            deploy_secrets={"apiToken": "CLOUDFLARE_API_TOKEN", "accountId": "CLOUDFLARE_ACCOUNT_ID"},
        )
        output = generate_workflow(config)
        assert "Deploy to Cloudflare" in output
        assert "cloudflare/wrangler-action@v3" in output


class TestHookGeneration:
    def test_pre_commit_script_is_shell(self):
        script = generate_pre_commit_script()
        assert script.startswith("#!/bin/sh")

    def test_pre_commit_script_runs_dockcheck(self):
        script = generate_pre_commit_script()
        assert "dockcheck check" in script

    def test_pre_commit_script_handles_exit_codes(self):
        script = generate_pre_commit_script()
        assert "EXIT_CODE" in script
        assert "BLOCKED" in script

    def test_pre_commit_yaml(self):
        output = generate_pre_commit_yaml()
        parsed = yaml.safe_load(output)
        assert "repos" in parsed
        hooks = parsed["repos"][0]["hooks"]
        assert hooks[0]["id"] == "dockcheck"

    def test_lefthook_yaml(self):
        output = generate_lefthook_yaml()
        parsed = yaml.safe_load(output)
        assert "pre-commit" in parsed
        assert "dockcheck" in parsed["pre-commit"]["commands"]

    def test_install_script_hook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake .git dir
            (Path(tmpdir) / ".git").mkdir()
            path = install_hook(tmpdir, HookConfig(framework="script"))
            assert path.exists()
            assert path.name == "pre-commit"
            # Check executable
            assert path.stat().st_mode & 0o111

    def test_install_pre_commit_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".git").mkdir()
            path = install_hook(tmpdir, HookConfig(framework="pre-commit"))
            assert path.exists()
            assert path.name == ".pre-commit-config.yaml"

    def test_install_lefthook_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".git").mkdir()
            path = install_hook(tmpdir, HookConfig(framework="lefthook"))
            assert path.exists()
            assert path.name == "lefthook.yml"

    def test_install_hook_not_git_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                install_hook(tmpdir)

    def test_install_hook_invalid_framework(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".git").mkdir()
            with pytest.raises(ValueError):
                install_hook(tmpdir, HookConfig(framework="invalid"))


class TestActionYaml:
    def test_action_yml_exists(self):
        action_path = Path(__file__).parent.parent.parent / "action" / "action.yml"
        assert action_path.exists()

    def test_action_yml_valid(self):
        action_path = Path(__file__).parent.parent.parent / "action" / "action.yml"
        parsed = yaml.safe_load(action_path.read_text())
        assert parsed["name"] == "dockcheck"
        assert "inputs" in parsed
        assert "outputs" in parsed
        assert "runs" in parsed
        assert parsed["runs"]["using"] == "composite"
