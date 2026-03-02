"""Workspace models — multi-target monorepo support."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AppSecretSpec(BaseModel):
    """An application-level secret (env var) discovered or declared."""

    name: str
    description: str = ""
    setup_url: str = ""
    required: bool = True
    source: str = "manual"  # "scanned" | "manual"


class TargetConfig(BaseModel):
    """A single deployable target within a workspace."""

    name: str
    path: str  # relative path from workspace root
    provider: str | None = None  # auto-detected if None
    app_secrets: list[AppSecretSpec] = Field(default_factory=list)
    health_url: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class WorkspaceConfig(BaseModel):
    """Top-level workspace definition for monorepo deployments."""

    name: str = ""
    targets: list[TargetConfig] = Field(default_factory=list)

    def to_yaml(self) -> str:
        """Serialize to YAML string."""
        return yaml.dump(
            self.model_dump(exclude_defaults=True),
            default_flow_style=False,
            sort_keys=False,
        )

    @classmethod
    def from_yaml(cls, text: str) -> WorkspaceConfig:
        """Parse from YAML string."""
        data = yaml.safe_load(text)
        if not data:
            return cls()
        return cls.model_validate(data)


# ---------------------------------------------------------------------------
# Config file detection patterns → provider name mapping
# ---------------------------------------------------------------------------

_CONFIG_TO_PROVIDER: dict[str, str] = {
    "wrangler.toml": "cloudflare",
    "wrangler.jsonc": "cloudflare",
    "vercel.json": "vercel",
    "fly.toml": "fly",
    "netlify.toml": "netlify",
    "Dockerfile": "docker-registry",
    "template.yaml": "aws-lambda",
    "template.yml": "aws-lambda",
    "samconfig.toml": "aws-lambda",
    "cloudbuild.yaml": "gcp-cloudrun",
    "railway.json": "railway",
    "railway.toml": "railway",
    "render.yaml": "render",
}


class WorkspaceResolver:
    """Detects and resolves workspace configurations."""

    # Directories to scan for sub-apps in auto-discovery
    _SCAN_DIRS = ("apps", "packages", "services")

    def resolve(self, path: str) -> WorkspaceConfig | None:
        """Resolve workspace config: explicit YAML first, then auto-discovery.

        Returns None if no workspace is detected (single-target project).
        """
        root = Path(path).resolve()

        # 1. Explicit workspace config
        ws_file = root / "dockcheck.workspace.yaml"
        if ws_file.exists():
            return WorkspaceConfig.from_yaml(ws_file.read_text(encoding="utf-8"))

        # 2. Auto-discovery
        return self._auto_discover(root)

    def _auto_discover(self, root: Path) -> WorkspaceConfig | None:
        """Scan for deployable targets in standard monorepo locations."""
        targets: list[TargetConfig] = []

        # Scan immediate subdirs and standard monorepo dirs
        scan_paths: list[Path] = []
        for d in sorted(root.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                scan_paths.append(d)

        for scan_dir_name in self._SCAN_DIRS:
            scan_dir = root / scan_dir_name
            if scan_dir.is_dir():
                for d in sorted(scan_dir.iterdir()):
                    if d.is_dir() and d not in scan_paths:
                        scan_paths.append(d)

        for subdir in scan_paths:
            provider = self._detect_provider_in_dir(subdir)
            if provider:
                rel_path = str(subdir.relative_to(root))
                targets.append(
                    TargetConfig(
                        name=subdir.name,
                        path=rel_path,
                        provider=provider,
                    )
                )

        if len(targets) < 2:
            return None  # Not a multi-target workspace

        return WorkspaceConfig(
            name=root.name,
            targets=targets,
        )

    @staticmethod
    def _detect_provider_in_dir(directory: Path) -> str | None:
        """Check if a directory contains a deploy config file."""
        for config_file, provider in _CONFIG_TO_PROVIDER.items():
            if (directory / config_file).exists():
                return provider
        return None

    @staticmethod
    def resolve_target_order(
        targets: list[TargetConfig],
    ) -> list[list[TargetConfig]]:
        """Topological sort of targets by depends_on → execution layers.

        Returns a list of layers. Targets in the same layer have no
        inter-dependencies and can deploy in parallel.

        Raises:
            ValueError: On cyclic or unknown dependency references.
        """
        name_to_target = {t.name: t for t in targets}

        # Validate all dependency names
        for target in targets:
            for dep in target.depends_on:
                if dep not in name_to_target:
                    raise ValueError(
                        f"Target '{target.name}' depends on unknown target '{dep}'."
                    )

        completed: set[str] = set()
        remaining = list(targets)
        layers: list[list[TargetConfig]] = []

        while remaining:
            ready = [
                t for t in remaining
                if all(d in completed for d in t.depends_on)
            ]
            if not ready:
                cycle_names = [t.name for t in remaining]
                raise ValueError(
                    f"Cyclic dependency detected among targets: {cycle_names}"
                )

            layers.append(ready)
            for t in ready:
                completed.add(t.name)
                remaining.remove(t)

        return layers
