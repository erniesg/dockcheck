"""Secret auditor — enriches scanner output with code context and heuristics."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, Field

from dockcheck.init.secret_scanner import SecretScanner

# ---------------------------------------------------------------------------
# Heuristic patterns for default/fallback detection
# ---------------------------------------------------------------------------

# Python: os.getenv("X", "default") or os.environ.get("X", "default")
_PY_DEFAULT = re.compile(
    r"os\.(?:getenv|environ\.get)\(\s*[\"'][A-Z][A-Z0-9_]+[\"']\s*,\s*\S"
)

# Python: os.environ.get("X") or "default"  /  os.getenv("X") or "default"
_PY_OR_DEFAULT = re.compile(
    r"os\.(?:getenv|environ\.get)\(.+?\)\s+or\s+[\"']"
)

# JS: process.env.X || "default"  /  process.env.X ?? "default"
_JS_FALLBACK = re.compile(
    r"process\.env[\.\[].+?(?:\|\||[?][?])\s*[\"']"
)

# JS: process.env.X || "default" (bracket form)
_JS_BRACKET_FALLBACK = re.compile(
    r"process\.env\[.+?\]\s*(?:\|\||[?][?])\s*[\"']"
)

# Test file path indicators
_TEST_INDICATORS = {"test_", "test.", "_test.", ".test.", ".spec.", "tests/", "spec/", "mock"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SecretContext(BaseModel):
    """One env var reference with surrounding code context."""

    name: str
    file_path: str
    line: int
    context_lines: list[str] = Field(default_factory=list)
    has_default: bool = False
    in_test_file: bool = False


class AuditResult(BaseModel):
    """Enriched scan result for agent consumption."""

    target_path: str
    total_references: int = 0
    unique_secrets: list[str] = Field(default_factory=list)
    contexts: list[SecretContext] = Field(default_factory=list)
    env_file_keys: list[str] = Field(default_factory=list)
    available_in_env: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


class SecretAuditor:
    """Enriches raw SecretScanner output with code context and heuristics."""

    def __init__(self, scanner: SecretScanner | None = None) -> None:
        self._scanner = scanner or SecretScanner()

    def audit(self, path: str) -> AuditResult:
        """Scan a directory and enrich each reference with context.

        Args:
            path: Directory to audit.

        Returns:
            Enriched AuditResult with contexts, env cross-checks, etc.
        """
        root = Path(path).resolve()
        scan = self._scanner.scan(str(root))

        if not scan.refs:
            return AuditResult(target_path=str(root))

        # Build contexts for each reference
        contexts: list[SecretContext] = []
        for ref in scan.refs:
            file_abs = root / ref.file_path
            context_lines = self._extract_context(file_abs, ref.line)
            match_line = context_lines[min(3, len(context_lines) - 1)] if context_lines else ""

            contexts.append(SecretContext(
                name=ref.name,
                file_path=ref.file_path,
                line=ref.line,
                context_lines=context_lines,
                has_default=self._has_default(match_line),
                in_test_file=self._is_test_file(ref.file_path),
            ))

        # Cross-check with .env files
        env_file_keys = self._read_env_file_keys(root)

        # Cross-check with os.environ
        available_in_env = [
            name for name in scan.unique_names
            if name in os.environ or name in env_file_keys
        ]

        missing = [
            name for name in scan.unique_names
            if name not in os.environ and name not in env_file_keys
        ]

        return AuditResult(
            target_path=str(root),
            total_references=len(scan.refs),
            unique_secrets=scan.unique_names,
            contexts=contexts,
            env_file_keys=sorted(env_file_keys),
            available_in_env=sorted(available_in_env),
            missing=sorted(missing),
        )

    @staticmethod
    def _extract_context(file_path: Path, line: int, radius: int = 3) -> list[str]:
        """Extract ±radius lines around the given line number.

        Args:
            file_path: Absolute path to the source file.
            line: 1-based line number of the reference.
            radius: Number of context lines before and after.

        Returns:
            List of source lines (up to 2*radius + 1).
        """
        try:
            all_lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except (OSError, UnicodeDecodeError):
            return []

        start = max(0, line - 1 - radius)
        end = min(len(all_lines), line + radius)
        return all_lines[start:end]

    @staticmethod
    def _has_default(line: str) -> bool:
        """Heuristic: does this line contain a default/fallback pattern?"""
        if not line:
            return False
        return bool(
            _PY_DEFAULT.search(line)
            or _PY_OR_DEFAULT.search(line)
            or _JS_FALLBACK.search(line)
            or _JS_BRACKET_FALLBACK.search(line)
        )

    @staticmethod
    def _is_test_file(file_path: str) -> bool:
        """Check if file path looks like a test file."""
        lower = file_path.lower()
        return any(indicator in lower for indicator in _TEST_INDICATORS)

    @staticmethod
    def _read_env_file_keys(root: Path) -> set[str]:
        """Read key names from .env files in the directory."""
        keys: set[str] = set()
        dotenv_names = {".env", ".env.local", ".dev.vars"}
        for name in dotenv_names:
            env_path = root / name
            if env_path.exists():
                try:
                    content = env_path.read_text(encoding="utf-8", errors="ignore")
                except (OSError, UnicodeDecodeError):
                    continue
                for raw_line in content.splitlines():
                    stripped = raw_line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if "=" in stripped:
                        key = stripped.split("=", 1)[0].strip()
                        if key and key[0].isupper():
                            keys.add(key)
        return keys
