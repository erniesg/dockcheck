"""Repo scanner — detects language, framework, deploy targets, and config."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field


class RepoContext(BaseModel):
    """Snapshot of a repository's structure and tooling."""

    language: str | None = None
    framework: str | None = None
    has_dockerfile: bool = False
    has_wrangler_config: bool = False
    has_vercel_config: bool = False
    has_fly_config: bool = False
    has_netlify_config: bool = False
    has_sam_config: bool = False
    has_cloudrun_config: bool = False
    has_railway_config: bool = False
    has_render_config: bool = False
    git_remote: str | None = None
    has_github_workflows: bool = False
    gitignore_covers_env: bool = False
    existing_env_keys: list[str] = Field(default_factory=list)
    test_command: str | None = None
    build_command: str | None = None
    lint_command: str | None = None
    format_command: str | None = None


class RepoDetector:
    """Scans a directory to build a RepoContext."""

    def detect(self, path: str = ".") -> RepoContext:
        root = Path(path).resolve()
        ctx = RepoContext()

        ctx.language = self._detect_language(root)
        ctx.framework = self._detect_framework(root)
        ctx.has_dockerfile = (root / "Dockerfile").exists()
        ctx.has_wrangler_config = (
            (root / "wrangler.toml").exists()
            or (root / "wrangler.jsonc").exists()
        )
        ctx.has_vercel_config = (root / "vercel.json").exists()
        ctx.has_fly_config = (root / "fly.toml").exists()
        ctx.has_netlify_config = (root / "netlify.toml").exists()
        ctx.has_sam_config = (
            (root / "template.yaml").exists()
            or (root / "template.yml").exists()
            or (root / "samconfig.toml").exists()
        )
        ctx.has_cloudrun_config = (
            (root / "cloudbuild.yaml").exists()
            or (root / "app.yaml").exists()
        )
        ctx.has_railway_config = (
            (root / "railway.json").exists()
            or (root / "railway.toml").exists()
        )
        ctx.has_render_config = (root / "render.yaml").exists()
        ctx.git_remote = self._detect_git_remote(root)
        ctx.has_github_workflows = (root / ".github" / "workflows").is_dir()
        ctx.gitignore_covers_env = self._check_gitignore(root)
        ctx.existing_env_keys = self._read_env_keys(root)
        ctx.test_command = self._detect_test_command(root, ctx.language)
        ctx.build_command = self._detect_build_command(root, ctx.language)
        ctx.lint_command = self._detect_lint_command(root, ctx.language)
        ctx.format_command = self._detect_format_command(root, ctx.language)

        return ctx

    def _detect_language(self, root: Path) -> str | None:
        if (root / "package.json").exists():
            # Check for TypeScript
            if (root / "tsconfig.json").exists():
                return "typescript"
            return "javascript"
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            return "python"
        if (root / "go.mod").exists():
            return "go"
        if (root / "Cargo.toml").exists():
            return "rust"
        return None

    def _detect_framework(self, root: Path) -> str | None:
        pkg_json = root / "package.json"
        if pkg_json.exists():
            return self._detect_js_framework(pkg_json)

        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            return self._detect_python_framework(pyproject)

        return None

    def _detect_js_framework(self, pkg_json: Path) -> str | None:
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        all_deps: dict[str, str] = {}
        all_deps.update(data.get("dependencies", {}))
        all_deps.update(data.get("devDependencies", {}))

        # Check in priority order
        frameworks = [
            ("next", "next"),
            ("hono", "hono"),
            ("express", "express"),
            ("fastify", "fastify"),
            ("react", "react"),
            ("vue", "vue"),
            ("svelte", "svelte"),
        ]
        for dep, name in frameworks:
            if dep in all_deps:
                return name
        return None

    def _detect_python_framework(self, pyproject: Path) -> str | None:
        try:
            content = pyproject.read_text(encoding="utf-8")
        except OSError:
            return None

        # Simple substring check — avoids toml dependency
        frameworks = [
            ("fastapi", "fastapi"),
            ("django", "django"),
            ("flask", "flask"),
        ]
        for dep, name in frameworks:
            if dep in content:
                return name
        return None

    def _detect_git_remote(self, root: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                # Normalize git@ URLs to readable form
                if url.startswith("git@"):
                    url = url.replace("git@", "").replace(":", "/", 1)
                if url.endswith(".git"):
                    url = url[:-4]
                # Strip https:// prefix
                if url.startswith("https://"):
                    url = url[len("https://"):]
                return url
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def _check_gitignore(self, root: Path) -> bool:
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            return False
        try:
            content = gitignore.read_text(encoding="utf-8")
        except OSError:
            return False

        for line in content.splitlines():
            stripped = line.strip()
            if stripped in (".env", ".env*", ".env.*", ".env.local"):
                return True
        return False

    def _read_env_keys(self, root: Path) -> list[str]:
        env_file = root / ".env"
        if not env_file.exists():
            return []
        try:
            keys: list[str] = []
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key = line.partition("=")[0].strip()
                    if key:
                        keys.append(key)
            return keys
        except OSError:
            return []

    def _detect_test_command(
        self, root: Path, language: str | None
    ) -> str | None:
        if language in ("javascript", "typescript"):
            pkg_json = root / "package.json"
            if pkg_json.exists():
                try:
                    data = json.loads(pkg_json.read_text(encoding="utf-8"))
                    scripts = data.get("scripts", {})
                    if "test" in scripts:
                        return "npm test"
                except (json.JSONDecodeError, OSError):
                    pass
        elif language == "python":
            if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
                return "pytest"
        elif language == "go":
            return "go test ./..."
        elif language == "rust":
            return "cargo test"
        return None

    def _detect_build_command(
        self, root: Path, language: str | None
    ) -> str | None:
        if language in ("javascript", "typescript"):
            pkg_json = root / "package.json"
            if pkg_json.exists():
                try:
                    data = json.loads(pkg_json.read_text(encoding="utf-8"))
                    scripts = data.get("scripts", {})
                    if "build" in scripts:
                        return "npm run build"
                except (json.JSONDecodeError, OSError):
                    pass
        elif language == "python":
            if (root / "Dockerfile").exists():
                return "docker build -t app ."
        elif language == "go":
            return "go build ./..."
        elif language == "rust":
            return "cargo build --release"
        return None

    def _detect_lint_command(
        self, root: Path, language: str | None
    ) -> str | None:
        if language in ("javascript", "typescript"):
            # Check package.json scripts first
            pkg_json = root / "package.json"
            if pkg_json.exists():
                try:
                    data = json.loads(pkg_json.read_text(encoding="utf-8"))
                    scripts = data.get("scripts", {})
                    if "lint" in scripts:
                        return "npm run lint"
                except (json.JSONDecodeError, OSError):
                    pass
            # Check for config files
            if (root / "biome.json").exists() or (root / "biome.jsonc").exists():
                return "npx biome check ."
            if (root / ".eslintrc.json").exists() or (root / ".eslintrc.js").exists():
                return "npx eslint ."
            if (root / "eslint.config.js").exists() or (root / "eslint.config.mjs").exists():
                return "npx eslint ."
        elif language == "python":
            # Check pyproject.toml for ruff config
            pyproject = root / "pyproject.toml"
            if pyproject.exists():
                try:
                    content = pyproject.read_text(encoding="utf-8")
                    if "[tool.ruff" in content:
                        return "ruff check ."
                except OSError:
                    pass
            if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists():
                return "ruff check ."
            if (root / "setup.cfg").exists() or (root / ".flake8").exists():
                return "flake8"
        elif language == "go":
            return "golangci-lint run"
        elif language == "rust":
            return "cargo clippy"
        return None

    def _detect_format_command(
        self, root: Path, language: str | None
    ) -> str | None:
        if language in ("javascript", "typescript"):
            pkg_json = root / "package.json"
            if pkg_json.exists():
                try:
                    data = json.loads(pkg_json.read_text(encoding="utf-8"))
                    scripts = data.get("scripts", {})
                    if "format" in scripts:
                        return "npm run format"
                except (json.JSONDecodeError, OSError):
                    pass
            if (root / "biome.json").exists() or (root / "biome.jsonc").exists():
                return "npx biome format ."
            if (root / ".prettierrc").exists() or (root / ".prettierrc.json").exists():
                return "npx prettier --check ."
            if (root / "prettier.config.js").exists() or (root / "prettier.config.mjs").exists():
                return "npx prettier --check ."
        elif language == "python":
            pyproject = root / "pyproject.toml"
            if pyproject.exists():
                try:
                    content = pyproject.read_text(encoding="utf-8")
                    if "[tool.ruff" in content:
                        return "ruff format --check ."
                except OSError:
                    pass
            if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists():
                return "ruff format --check ."
        elif language == "rust":
            return "cargo fmt --check"
        return None
