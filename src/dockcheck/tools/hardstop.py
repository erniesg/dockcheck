"""Hard stop detection — command blocklist and critical path checking."""

from __future__ import annotations

import fnmatch
import os
import re

from pydantic import BaseModel, Field


class HardStopMatch(BaseModel):
    input_value: str
    matched_pattern: str
    category: str  # "command" or "critical_path"


class HardStopResult(BaseModel):
    blocked: bool
    matches: list[HardStopMatch] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.blocked:
            return "No hard stops triggered."
        lines = [f"BLOCKED — {len(self.matches)} hard stop(s) triggered:"]
        for m in self.matches:
            lines.append(f"  [{m.category}] '{m.input_value}' matched '{m.matched_pattern}'")
        return "\n".join(lines)


# Default dangerous command patterns
DEFAULT_COMMAND_BLOCKLIST = [
    "rm -rf /",
    "rm -rf /*",
    "rm -r /",
    "DROP TABLE",
    "DROP DATABASE",
    "git push --force",
    "git push -f",
    "git reset --hard",
    "terraform destroy",
    "kubectl delete namespace",
    "kubectl delete ns",
    "chmod 777",
    ":(){ :|:& };:",  # fork bomb
]


class HardStopChecker:
    """Scans commands against a blocklist of dangerous patterns."""

    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns = patterns if patterns is not None else DEFAULT_COMMAND_BLOCKLIST

    def check_command(self, command: str) -> HardStopResult:
        matches: list[HardStopMatch] = []
        cmd_lower = command.lower().strip()

        for pattern in self.patterns:
            pattern_lower = pattern.lower()
            if pattern_lower in cmd_lower:
                matches.append(
                    HardStopMatch(
                        input_value=command,
                        matched_pattern=pattern,
                        category="command",
                    )
                )

        return HardStopResult(blocked=len(matches) > 0, matches=matches)

    def check_commands(self, commands: list[str]) -> HardStopResult:
        all_matches: list[HardStopMatch] = []
        for cmd in commands:
            result = self.check_command(cmd)
            all_matches.extend(result.matches)
        return HardStopResult(blocked=len(all_matches) > 0, matches=all_matches)


class CriticalPathChecker:
    """Matches file paths against glob patterns for critical/protected paths."""

    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns = patterns or [
            "**/production/**",
            "**/.env*",
            "**/secrets/**",
            "**/*credentials*",
            "**/*secret*key*",
        ]

    def _matches_glob(self, file_path: str, pattern: str) -> bool:
        """Match a file path against a glob pattern supporting ** notation."""
        # fnmatch doesn't handle ** well — normalize patterns
        # For patterns like "**/.env*", also match at root level
        if pattern.startswith("**/"):
            suffix = pattern[3:]
            # Match the suffix against the basename or any path suffix
            if fnmatch.fnmatch(file_path, suffix):
                return True
            if fnmatch.fnmatch(file_path, pattern):
                return True
            # Check each path component suffix
            parts = file_path.replace("\\", "/").split("/")
            for i in range(len(parts)):
                subpath = "/".join(parts[i:])
                if fnmatch.fnmatch(subpath, suffix):
                    return True
            return False
        return fnmatch.fnmatch(file_path, pattern)

    def check_path(self, file_path: str) -> HardStopResult:
        matches: list[HardStopMatch] = []

        for pattern in self.patterns:
            if self._matches_glob(file_path, pattern):
                matches.append(
                    HardStopMatch(
                        input_value=file_path,
                        matched_pattern=pattern,
                        category="critical_path",
                    )
                )

        return HardStopResult(blocked=len(matches) > 0, matches=matches)

    def check_paths(self, file_paths: list[str]) -> HardStopResult:
        all_matches: list[HardStopMatch] = []
        for fp in file_paths:
            result = self.check_path(fp)
            all_matches.extend(result.matches)
        return HardStopResult(blocked=len(all_matches) > 0, matches=all_matches)


class DiffAnalyzer:
    """Extracts file paths and commands from a unified diff."""

    _DIFF_FILE_RE = re.compile(r"^(?:---|\+\+\+) [ab]/(.+)$", re.MULTILINE)
    _ADDED_LINE_RE = re.compile(r"^\+(?!\+\+)(.*)$", re.MULTILINE)

    @staticmethod
    def extract_file_paths(diff: str) -> list[str]:
        return list(dict.fromkeys(DiffAnalyzer._DIFF_FILE_RE.findall(diff)))

    @staticmethod
    def extract_added_lines(diff: str) -> list[str]:
        return DiffAnalyzer._ADDED_LINE_RE.findall(diff)

    @staticmethod
    def count_file_deletes(diff: str) -> int:
        """Count files deleted in a diff (indicated by /dev/null destination)."""
        return diff.count("+++ /dev/null")
