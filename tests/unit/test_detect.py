"""Tests for repo scanning and detection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from dockcheck.init.detect import RepoContext, RepoDetector


class TestRepoContext:
    def test_defaults(self):
        ctx = RepoContext()
        assert ctx.language is None
        assert ctx.framework is None
        assert ctx.has_dockerfile is False
        assert ctx.has_wrangler_config is False
        assert ctx.existing_env_keys == []

    def test_serialization(self):
        ctx = RepoContext(language="python", has_dockerfile=True)
        data = ctx.model_dump()
        assert data["language"] == "python"
        assert data["has_dockerfile"] is True


class TestDetectLanguage:
    def test_javascript_from_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language == "javascript"

    def test_typescript_from_tsconfig(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}')
        (tmp_path / "tsconfig.json").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language == "typescript"

    def test_python_from_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language == "python"

    def test_python_from_setup_py(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language == "python"

    def test_go_from_go_mod(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/app")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language == "go"

    def test_rust_from_cargo_toml(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language == "rust"

    def test_unknown_language(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language is None


class TestDetectFramework:
    def test_hono_from_deps(self, tmp_path: Path):
        pkg = {"dependencies": {"hono": "^4.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework == "hono"

    def test_express_from_deps(self, tmp_path: Path):
        pkg = {"dependencies": {"express": "^4.18.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework == "express"

    def test_react_from_deps(self, tmp_path: Path):
        pkg = {"dependencies": {"react": "^18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework == "react"

    def test_next_takes_priority_over_react(self, tmp_path: Path):
        pkg = {"dependencies": {"react": "^18.0.0", "next": "^14.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework == "next"

    def test_fastapi_from_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["fastapi>=0.100"]'
        )
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework == "fastapi"

    def test_django_from_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["django>=4.2"]'
        )
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework == "django"

    def test_flask_from_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["flask>=3.0"]'
        )
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework == "flask"

    def test_no_framework(self, tmp_path: Path):
        pkg = {"dependencies": {"lodash": "^4.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework is None

    def test_invalid_package_json_graceful(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("not json")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.framework is None


class TestDetectConfigFiles:
    def test_dockerfile_detected(self, tmp_path: Path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.10")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_dockerfile is True

    def test_wrangler_toml_detected(self, tmp_path: Path):
        (tmp_path / "wrangler.toml").write_text('name = "worker"')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_wrangler_config is True

    def test_wrangler_jsonc_detected(self, tmp_path: Path):
        (tmp_path / "wrangler.jsonc").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_wrangler_config is True

    def test_vercel_json_detected(self, tmp_path: Path):
        (tmp_path / "vercel.json").write_text("{}")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_vercel_config is True

    def test_fly_toml_detected(self, tmp_path: Path):
        (tmp_path / "fly.toml").write_text('app = "myapp"')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_fly_config is True

    def test_netlify_toml_detected(self, tmp_path: Path):
        (tmp_path / "netlify.toml").write_text('[build]\ncommand = "npm run build"')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_netlify_config is True

    def test_github_workflows_detected(self, tmp_path: Path):
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_github_workflows is True

    def test_no_config_files(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_dockerfile is False
        assert ctx.has_wrangler_config is False
        assert ctx.has_vercel_config is False
        assert ctx.has_fly_config is False
        assert ctx.has_netlify_config is False
        assert ctx.has_github_workflows is False
        assert ctx.has_sam_config is False
        assert ctx.has_cloudrun_config is False
        assert ctx.has_railway_config is False
        assert ctx.has_render_config is False


class TestDetectGitRemote:
    def test_git_remote_parsed(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://github.com/erniesg/hello.git\n"
        )
        with patch("subprocess.run", return_value=mock_result):
            ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.git_remote == "github.com/erniesg/hello"

    def test_git_remote_ssh(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="git@github.com:erniesg/hello.git\n"
        )
        with patch("subprocess.run", return_value=mock_result):
            ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.git_remote == "github.com/erniesg/hello"

    def test_no_git_remote(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: not a git repo"
        )
        with patch("subprocess.run", return_value=mock_result):
            ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.git_remote is None

    def test_git_not_installed(self, tmp_path: Path):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.git_remote is None


class TestGitignoreCheck:
    def test_gitignore_covers_env(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text(".env\nnode_modules/\n")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.gitignore_covers_env is True

    def test_gitignore_covers_env_star(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text(".env*\n")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.gitignore_covers_env is True

    def test_gitignore_missing(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.gitignore_covers_env is False

    def test_gitignore_no_env_pattern(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text("node_modules/\n*.pyc\n")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.gitignore_covers_env is False


class TestEnvKeys:
    def test_reads_env_keys(self, tmp_path: Path):
        (tmp_path / ".env").write_text("API_KEY=xxx\nDB_URL=postgres://\n")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.existing_env_keys == ["API_KEY", "DB_URL"]

    def test_skips_comments(self, tmp_path: Path):
        (tmp_path / ".env").write_text("# comment\nKEY=value\n")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.existing_env_keys == ["KEY"]

    def test_no_env_file(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.existing_env_keys == []


class TestDetectCommands:
    def test_npm_test_detected(self, tmp_path: Path):
        pkg = {"scripts": {"test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.test_command == "npm test"

    def test_pytest_detected(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.test_command == "pytest"

    def test_go_test_detected(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/app")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.test_command == "go test ./..."

    def test_cargo_test_detected(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.test_command == "cargo test"

    def test_npm_build_detected(self, tmp_path: Path):
        pkg = {"scripts": {"build": "vite build"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.build_command == "npm run build"

    def test_go_build_detected(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/app")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.build_command == "go build ./..."

    def test_no_test_command(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.test_command is None

    def test_no_build_command(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.build_command is None


class TestDetectNewProviderConfigs:
    """Tests for AWS SAM, Cloud Run, Railway, and Render config detection."""

    # --- AWS SAM ---
    def test_sam_config_template_yaml(self, tmp_path: Path):
        (tmp_path / "template.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_sam_config is True

    def test_sam_config_template_yml(self, tmp_path: Path):
        (tmp_path / "template.yml").write_text("AWSTemplateFormatVersion: '2010-09-09'")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_sam_config is True

    def test_sam_config_samconfig_toml(self, tmp_path: Path):
        (tmp_path / "samconfig.toml").write_text("[default.deploy]")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_sam_config is True

    def test_no_sam_config(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_sam_config is False

    # --- GCP Cloud Run ---
    def test_cloudrun_config_cloudbuild(self, tmp_path: Path):
        (tmp_path / "cloudbuild.yaml").write_text("steps:\n- name: gcr.io/cloud-builders/docker")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_cloudrun_config is True

    def test_cloudrun_config_app_yaml(self, tmp_path: Path):
        (tmp_path / "app.yaml").write_text("runtime: python39")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_cloudrun_config is True

    def test_no_cloudrun_config(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_cloudrun_config is False

    # --- Railway ---
    def test_railway_config_json(self, tmp_path: Path):
        (tmp_path / "railway.json").write_text('{"build": {}}')
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_railway_config is True

    def test_railway_config_toml(self, tmp_path: Path):
        (tmp_path / "railway.toml").write_text("[build]")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_railway_config is True

    def test_no_railway_config(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_railway_config is False

    # --- Render ---
    def test_render_config(self, tmp_path: Path):
        (tmp_path / "render.yaml").write_text("services:\n- type: web")
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_render_config is True

    def test_no_render_config(self, tmp_path: Path):
        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.has_render_config is False


class TestCFWorkerExample:
    """Test detection against the bundled cf-worker-hello example."""

    def test_detects_cf_worker(self, tmp_path: Path):
        # Simulate cf-worker-hello structure
        (tmp_path / "wrangler.toml").write_text('name = "hello-world"')
        pkg = {
            "devDependencies": {"wrangler": "^3.0.0"},
            "scripts": {"deploy": "wrangler deploy"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / ".gitignore").write_text("node_modules/\n.dev.vars\n.env\n")

        ctx = RepoDetector().detect(str(tmp_path))
        assert ctx.language == "javascript"
        assert ctx.has_wrangler_config is True
        assert ctx.gitignore_covers_env is True
