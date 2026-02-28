"""Pre-commit hook generation for dockcheck."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class HookConfig(BaseModel):
    """Configuration for pre-commit hooks."""

    run_check_on_commit: bool = True
    check_hard_stops_only: bool = True
    framework: str = "script"  # "script", "pre-commit", "lefthook"


def generate_pre_commit_script(config: Optional[HookConfig] = None) -> str:
    """Generate a standalone git pre-commit hook script."""
    cfg = config or HookConfig()

    flags = ""
    if cfg.check_hard_stops_only:
        flags = " --commands \"$(git diff --cached --name-only)\""

    return f"""#!/bin/sh
# dockcheck pre-commit hook
# Runs policy check on staged changes before allowing commit

set -e

# Get staged diff
DIFF=$(git diff --cached)

if [ -z "$DIFF" ]; then
    exit 0
fi

# Run dockcheck policy check
echo "Running dockcheck pre-commit check..."
echo "$DIFF" | dockcheck check --diff -

EXIT_CODE=$?

if [ $EXIT_CODE -eq 2 ]; then
    echo ""
    echo "BLOCKED: dockcheck detected hard stop violations."
    echo "Fix the issues above or use --no-verify to bypass (not recommended)."
    exit 1
elif [ $EXIT_CODE -eq 1 ]; then
    echo ""
    echo "WARNING: dockcheck detected policy violations."
    echo "Proceeding with commit, but deployment may be blocked."
fi

exit 0
"""


def generate_pre_commit_yaml() -> str:
    """Generate .pre-commit-config.yaml entry for dockcheck."""
    return """repos:
  - repo: local
    hooks:
      - id: dockcheck
        name: dockcheck policy check
        entry: bash -c 'git diff --cached | dockcheck check --diff -'
        language: system
        pass_filenames: false
        stages: [commit]
"""


def generate_lefthook_yaml() -> str:
    """Generate lefthook.yml entry for dockcheck."""
    return """pre-commit:
  commands:
    dockcheck:
      run: git diff --cached | dockcheck check --diff -
      fail_text: "dockcheck policy check failed"
"""


def install_hook(
    target_dir: str = ".",
    config: Optional[HookConfig] = None,
) -> Path:
    """Install a pre-commit hook to .git/hooks/."""
    cfg = config or HookConfig()
    git_hooks_dir = Path(target_dir) / ".git" / "hooks"

    if not git_hooks_dir.parent.exists():
        raise FileNotFoundError(
            f"Not a git repository: {Path(target_dir).resolve()}"
        )

    git_hooks_dir.mkdir(parents=True, exist_ok=True)

    if cfg.framework == "script":
        hook_path = git_hooks_dir / "pre-commit"
        hook_path.write_text(generate_pre_commit_script(cfg))
        hook_path.chmod(0o755)
        return hook_path
    elif cfg.framework == "pre-commit":
        config_path = Path(target_dir) / ".pre-commit-config.yaml"
        config_path.write_text(generate_pre_commit_yaml())
        return config_path
    elif cfg.framework == "lefthook":
        config_path = Path(target_dir) / "lefthook.yml"
        config_path.write_text(generate_lefthook_yaml())
        return config_path
    else:
        raise ValueError(f"Unknown hook framework: {cfg.framework}")
