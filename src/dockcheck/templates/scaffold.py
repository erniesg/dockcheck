"""Template scaffolding — list bundled templates and scaffold .dockcheck/ directories."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel


class TemplateInfo(BaseModel):
    """Metadata describing a bundled template."""

    name: str
    description: str
    path: Path

    model_config = {"arbitrary_types_allowed": True}


# Human-readable descriptions keyed by template directory name
_TEMPLATE_DESCRIPTIONS: dict[str, str] = {
    "hackathon": (
        "Permissive thresholds for rapid iteration — staging: 0.6, prod: 0.7. "
        "All skills enabled. Ideal for hackathons and early prototypes."
    ),
    "trading-bot": (
        "Strict thresholds for financial systems — staging: 0.95, prod: 0.99. "
        "Extra hard stops for position/order commands. Requires manual approval for production."
    ),
    "fastapi-app": (
        "Standard thresholds for Python APIs — staging: 0.8, prod: 0.9. "
        "Python Dockerfile with uvicorn. Uses pytest as test command."
    ),
    "react-app": (
        "Standard thresholds for frontend apps — staging: 0.8, prod: 0.9. "
        "Multi-stage Node/Nginx Dockerfile. Uses npm test as test command."
    ),
}


class Scaffolder:
    """Creates .dockcheck/ project scaffolds from bundled templates."""

    TEMPLATES_DIR = Path(__file__).parent / "bundled"

    @classmethod
    def list_templates(cls) -> list[TemplateInfo]:
        """Return metadata for all available templates, sorted by name."""
        if not cls.TEMPLATES_DIR.exists():
            return []

        templates: list[TemplateInfo] = []
        for entry in sorted(cls.TEMPLATES_DIR.iterdir()):
            if not entry.is_dir():
                continue
            description = _TEMPLATE_DESCRIPTIONS.get(entry.name, entry.name)
            templates.append(
                TemplateInfo(
                    name=entry.name,
                    description=description,
                    path=entry,
                )
            )
        return templates

    @classmethod
    def scaffold(
        cls,
        template: str,
        target_dir: str = ".",
        project_name: str = "my-app",
    ) -> list[str]:
        """Scaffold .dockcheck/ from a named template into *target_dir*.

        Returns a sorted list of relative paths (relative to *target_dir*)
        for all files that were created.

        Raises:
            ValueError: if the template name is not found in bundled templates.
            FileExistsError: if .dockcheck/ already exists in the target directory.
        """
        template_path = cls.TEMPLATES_DIR / template
        if not template_path.exists():
            available = [t.name for t in cls.list_templates()]
            raise ValueError(
                f"Template '{template}' not found. "
                f"Available templates: {', '.join(available)}"
            )

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

        dockcheck_dir = target / ".dockcheck"
        if dockcheck_dir.exists():
            raise FileExistsError(
                f".dockcheck/ already exists at {dockcheck_dir}. "
                "Remove it first or choose a different target directory."
            )

        created: list[str] = []

        # Walk the template directory tree
        for src_file in sorted(template_path.rglob("*")):
            if not src_file.is_file():
                continue

            # Relative path within the template bundle
            rel = src_file.relative_to(template_path)

            # Files that live in .dockcheck/ in the bundle go into .dockcheck/ in target
            # Files at the root (e.g. dockcheck.yml) go to target root
            dest_file = target / ".dockcheck" / rel if _is_dockcheck_file(rel) else target / rel

            dest_file.parent.mkdir(parents=True, exist_ok=True)

            raw = src_file.read_text(encoding="utf-8")
            rendered = _render_template(raw, project_name=project_name)
            dest_file.write_text(rendered, encoding="utf-8")

            created.append(str(dest_file.relative_to(target)))

        return sorted(created)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Files that stay in .dockcheck/ when scaffolded (everything except dockcheck.yml)
_ROOT_FILES = {"dockcheck.yml", "Dockerfile"}


def _is_dockcheck_file(rel: Path) -> bool:
    """Return True if this file should live inside .dockcheck/, False for project root."""
    return rel.name not in _ROOT_FILES


def _render_template(content: str, *, project_name: str) -> str:
    """Replace {{project_name}} placeholders in template content."""
    return re.sub(r"\{\{\s*project_name\s*\}\}", project_name, content)
