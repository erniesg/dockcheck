"""Shared fixtures for smoke tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture()
def copy_example(tmp_path: Path):
    """Return a helper that copies an example project into a temp directory."""

    def _copy(example_name: str) -> Path:
        src = EXAMPLES_DIR / example_name
        if not src.exists():
            pytest.skip(f"Example {example_name!r} not found at {src}")
        dest = tmp_path / example_name
        shutil.copytree(src, dest)
        return dest

    return _copy


def require_env(*vars: str) -> dict[str, str]:
    """Return env dict for the given var names, or skip if any are missing."""
    env = {}
    for var in vars:
        val = os.environ.get(var)
        if not val:
            pytest.skip(f"Missing env var: {var}")
        env[var] = val
    return env
