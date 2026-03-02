"""Secret scanner — detects env var references in source code."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field


class SecretRef(BaseModel):
    """A single env var reference found in source code."""

    name: str
    file_path: str
    line: int


class ScanResult(BaseModel):
    """Aggregated scan result."""

    refs: list[SecretRef] = Field(default_factory=list)
    unique_names: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# JS/TS: process.env.X, process.env["X"], process.env['X']
_JS_PROCESS_ENV_DOT = re.compile(r"process\.env\.([A-Z][A-Z0-9_]+)")
_JS_PROCESS_ENV_BRACKET = re.compile(r"process\.env\[([\"'])([A-Z][A-Z0-9_]+)\1\]")

# Vite / import.meta.env.X
_VITE_ENV = re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]+)")

# Python: os.environ["X"], os.environ.get("X"), os.getenv("X")
_PY_ENVIRON_BRACKET = re.compile(r"os\.environ\[([\"'])([A-Z][A-Z0-9_]+)\1\]")
_PY_ENVIRON_GET = re.compile(r"os\.environ\.get\(\s*([\"'])([A-Z][A-Z0-9_]+)\1")
_PY_GETENV = re.compile(r"os\.getenv\(\s*([\"'])([A-Z][A-Z0-9_]+)\1")

# .env file: KEY=value
_DOTENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]+)\s*=", re.MULTILINE)

# wrangler.toml [vars] section keys
_WRANGLER_VARS_KEY = re.compile(r"^([A-Z][A-Z0-9_]+)\s*=", re.MULTILINE)

# File extensions to scan
_SOURCE_EXTENSIONS = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".py", ".pyw",
}

_DOTENV_NAMES = {".env", ".env.example", ".env.sample", ".env.local", ".dev.vars"}

# Binary / generated dirs to skip
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "coverage",
    ".dockcheck", ".github",
}


class SecretScanner:
    """Scans source code to discover app-level env var references."""

    # Deploy-infrastructure secrets to exclude from app secret results
    DEPLOY_SECRET_NAMES: set[str] = {
        "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
        "VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID",
        "FLY_API_TOKEN",
        "NETLIFY_AUTH_TOKEN", "NETLIFY_SITE_ID",
        "DOCKER_USERNAME", "DOCKER_PASSWORD",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION",
        "GCP_PROJECT_ID", "GCP_SERVICE_ACCOUNT_KEY",
        "RAILWAY_TOKEN",
        "RENDER_DEPLOY_HOOK_URL", "RENDER_API_KEY",
        "GITHUB_TOKEN",
    }

    # Known non-secret env vars to exclude
    IGNORE_NAMES: set[str] = {
        "NODE_ENV", "PORT", "PATH", "HOME", "USER", "SHELL",
        "LANG", "TERM", "PWD", "HOSTNAME", "TMPDIR", "TMP", "TEMP",
        "CI", "DEBUG", "VERBOSE", "LOG_LEVEL", "LOGLEVEL",
        "npm_lifecycle_event", "npm_package_name",
    }

    def scan(self, path: str) -> ScanResult:
        """Scan a directory for env var references.

        Returns a ScanResult with all references and deduplicated names.
        """
        root = Path(path).resolve()
        refs: list[SecretRef] = []

        if not root.is_dir():
            return ScanResult()

        # Scan source files
        for file_path in self._iter_source_files(root):
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = str(file_path.relative_to(root))
            refs.extend(self._scan_source(content, rel_path))

        # Scan .env files
        for dotenv_name in _DOTENV_NAMES:
            dotenv_path = root / dotenv_name
            if dotenv_path.exists():
                try:
                    content = dotenv_path.read_text(encoding="utf-8", errors="ignore")
                except (OSError, UnicodeDecodeError):
                    continue
                refs.extend(self._scan_dotenv(content, dotenv_name))

        # Scan wrangler.toml [vars] section
        wrangler = root / "wrangler.toml"
        if wrangler.exists():
            try:
                content = wrangler.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                content = ""
            refs.extend(self._scan_wrangler_vars(content, "wrangler.toml"))

        # Filter and deduplicate
        filtered = [
            r for r in refs
            if r.name not in self.DEPLOY_SECRET_NAMES
            and r.name not in self.IGNORE_NAMES
        ]

        seen: set[str] = set()
        unique_names: list[str] = []
        for ref in filtered:
            if ref.name not in seen:
                seen.add(ref.name)
                unique_names.append(ref.name)
        unique_names.sort()

        return ScanResult(refs=filtered, unique_names=unique_names)

    def _iter_source_files(self, root: Path) -> list[Path]:
        """Iterate over source files, skipping binary/generated dirs."""
        files: list[Path] = []
        for item in root.rglob("*"):
            if any(skip in item.parts for skip in _SKIP_DIRS):
                continue
            if item.is_file() and item.suffix in _SOURCE_EXTENSIONS:
                files.append(item)
        return sorted(files)

    @staticmethod
    def _scan_source(content: str, rel_path: str) -> list[SecretRef]:
        """Extract env var references from source code."""
        refs: list[SecretRef] = []
        lines = content.splitlines()

        for line_num, line in enumerate(lines, start=1):
            # JS/TS patterns
            for m in _JS_PROCESS_ENV_DOT.finditer(line):
                refs.append(SecretRef(name=m.group(1), file_path=rel_path, line=line_num))
            for m in _JS_PROCESS_ENV_BRACKET.finditer(line):
                refs.append(SecretRef(name=m.group(2), file_path=rel_path, line=line_num))
            for m in _VITE_ENV.finditer(line):
                refs.append(SecretRef(name=m.group(1), file_path=rel_path, line=line_num))

            # Python patterns
            for m in _PY_ENVIRON_BRACKET.finditer(line):
                refs.append(SecretRef(name=m.group(2), file_path=rel_path, line=line_num))
            for m in _PY_ENVIRON_GET.finditer(line):
                refs.append(SecretRef(name=m.group(2), file_path=rel_path, line=line_num))
            for m in _PY_GETENV.finditer(line):
                refs.append(SecretRef(name=m.group(2), file_path=rel_path, line=line_num))

        return refs

    @staticmethod
    def _scan_dotenv(content: str, rel_path: str) -> list[SecretRef]:
        """Extract key names from a .env file."""
        refs: list[SecretRef] = []
        for line_num, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _DOTENV_LINE.match(stripped)
            if m:
                refs.append(SecretRef(name=m.group(1), file_path=rel_path, line=line_num))
        return refs

    @staticmethod
    def _scan_wrangler_vars(content: str, rel_path: str) -> list[SecretRef]:
        """Extract [vars] keys from wrangler.toml."""
        refs: list[SecretRef] = []
        in_vars = False
        for line_num, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped == "[vars]":
                in_vars = True
                continue
            if stripped.startswith("[") and in_vars:
                break  # Left [vars] section
            if in_vars:
                m = _WRANGLER_VARS_KEY.match(stripped)
                if m:
                    refs.append(
                        SecretRef(name=m.group(1), file_path=rel_path, line=line_num)
                    )
        return refs
