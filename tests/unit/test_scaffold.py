"""Tests for template scaffolding — listing, variable substitution, and file creation."""

from __future__ import annotations

import pytest
import yaml

from dockcheck.templates.scaffold import Scaffolder, TemplateInfo

# Expected template names based on the bundled templates
EXPECTED_TEMPLATES = {"hackathon", "trading-bot", "fastapi-app", "react-app"}


class TestListTemplates:
    """Tests for Scaffolder.list_templates()."""

    def test_returns_list(self):
        result = Scaffolder.list_templates()
        assert isinstance(result, list)

    def test_returns_all_expected_templates(self):
        names = {t.name for t in Scaffolder.list_templates()}
        assert names == EXPECTED_TEMPLATES

    def test_returns_template_info_objects(self):
        for item in Scaffolder.list_templates():
            assert isinstance(item, TemplateInfo)

    def test_each_template_has_nonempty_description(self):
        for item in Scaffolder.list_templates():
            assert item.description, f"Template '{item.name}' has empty description"

    def test_templates_sorted_by_name(self):
        names = [t.name for t in Scaffolder.list_templates()]
        assert names == sorted(names)

    def test_each_template_path_exists(self):
        for item in Scaffolder.list_templates():
            assert item.path.exists(), f"Template path does not exist: {item.path}"
            assert item.path.is_dir()

    def test_hackathon_description_mentions_thresholds(self):
        templates = {t.name: t for t in Scaffolder.list_templates()}
        assert "0.6" in templates["hackathon"].description
        assert "0.7" in templates["hackathon"].description

    def test_trading_bot_description_mentions_strict(self):
        templates = {t.name: t for t in Scaffolder.list_templates()}
        desc = templates["trading-bot"].description
        assert "0.95" in desc
        assert "0.99" in desc

    def test_fastapi_description_mentions_pytest(self):
        templates = {t.name: t for t in Scaffolder.list_templates()}
        desc = templates["fastapi-app"].description.lower()
        assert "pytest" in desc

    def test_react_description_mentions_npm(self):
        templates = {t.name: t for t in Scaffolder.list_templates()}
        desc = templates["react-app"].description.lower()
        assert "npm" in desc


class TestScaffold:
    """Tests for Scaffolder.scaffold() — file creation in a temp directory."""

    def test_scaffold_returns_list_of_created_files(self, tmp_path):
        created = Scaffolder.scaffold("hackathon", str(tmp_path))
        assert isinstance(created, list)
        assert len(created) > 0

    def test_scaffold_creates_policy_yaml(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path))
        assert (tmp_path / ".dockcheck" / "policy.yaml").exists()

    def test_scaffold_creates_dockcheck_yml_at_root(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path))
        assert (tmp_path / "dockcheck.yml").exists()

    def test_scaffold_creates_dockerfile_at_root(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path))
        assert (tmp_path / "Dockerfile").exists()

    def test_scaffold_raises_on_unknown_template(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            Scaffolder.scaffold("nonexistent-template", str(tmp_path))

    def test_scaffold_raises_if_dockcheck_dir_exists(self, tmp_path):
        (tmp_path / ".dockcheck").mkdir()
        with pytest.raises(FileExistsError, match=".dockcheck/"):
            Scaffolder.scaffold("hackathon", str(tmp_path))

    def test_scaffold_returns_sorted_file_list(self, tmp_path):
        created = Scaffolder.scaffold("hackathon", str(tmp_path))
        assert created == sorted(created)

    def test_scaffold_all_returned_files_exist(self, tmp_path):
        created = Scaffolder.scaffold("hackathon", str(tmp_path))
        for rel_path in created:
            assert (tmp_path / rel_path).exists(), f"Listed file not found: {rel_path}"

    def test_scaffold_creates_target_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "nested" / "project"
        assert not new_dir.exists()
        Scaffolder.scaffold("hackathon", str(new_dir))
        assert new_dir.exists()
        assert (new_dir / ".dockcheck" / "policy.yaml").exists()


class TestVariableSubstitution:
    """Tests for {{project_name}} placeholder substitution."""

    def test_default_project_name_substituted_in_dockcheck_yml(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path))
        content = (tmp_path / "dockcheck.yml").read_text()
        assert "my-app" in content
        assert "{{project_name}}" not in content

    def test_custom_project_name_substituted_in_dockcheck_yml(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path), project_name="my-service")
        content = (tmp_path / "dockcheck.yml").read_text()
        assert "my-service" in content
        assert "{{project_name}}" not in content

    def test_project_name_substituted_in_all_files(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path), project_name="awesome-app")
        for filepath in tmp_path.rglob("*"):
            if filepath.is_file():
                content = filepath.read_text()
                assert "{{project_name}}" not in content, (
                    f"Unreplaced placeholder found in {filepath}"
                )

    def test_project_name_in_build_command(self, tmp_path):
        Scaffolder.scaffold("fastapi-app", str(tmp_path), project_name="my-api")
        content = (tmp_path / "dockcheck.yml").read_text()
        assert "my-api" in content


class TestTemplateValidity:
    """Validate that each bundled template produces valid YAML."""

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_TEMPLATES))
    def test_policy_yaml_is_valid_yaml(self, tmp_path, template_name):
        target = tmp_path / template_name
        Scaffolder.scaffold(template_name, str(target))
        policy_path = target / ".dockcheck" / "policy.yaml"
        assert policy_path.exists()
        data = yaml.safe_load(policy_path.read_text())
        assert data is not None
        assert "version" in data

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_TEMPLATES))
    def test_dockcheck_yml_is_valid_yaml(self, tmp_path, template_name):
        target = tmp_path / template_name
        Scaffolder.scaffold(template_name, str(target))
        config_path = target / "dockcheck.yml"
        assert config_path.exists()
        data = yaml.safe_load(config_path.read_text())
        assert data is not None
        assert "project" in data

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_TEMPLATES))
    def test_dockcheck_yml_has_test_command(self, tmp_path, template_name):
        target = tmp_path / template_name
        Scaffolder.scaffold(template_name, str(target))
        data = yaml.safe_load((target / "dockcheck.yml").read_text())
        assert "test_command" in data["project"]
        assert data["project"]["test_command"]

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_TEMPLATES))
    def test_dockerfile_exists_and_nonempty(self, tmp_path, template_name):
        target = tmp_path / template_name
        Scaffolder.scaffold(template_name, str(target))
        dockerfile = target / "Dockerfile"
        assert dockerfile.exists()
        assert len(dockerfile.read_text()) > 0

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_TEMPLATES))
    def test_policy_has_hard_stops(self, tmp_path, template_name):
        target = tmp_path / template_name
        Scaffolder.scaffold(template_name, str(target))
        data = yaml.safe_load((target / ".dockcheck" / "policy.yaml").read_text())
        assert "hard_stops" in data
        assert len(data["hard_stops"]["commands"]) >= 8

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_TEMPLATES))
    def test_policy_has_confidence_thresholds(self, tmp_path, template_name):
        target = tmp_path / template_name
        Scaffolder.scaffold(template_name, str(target))
        data = yaml.safe_load((target / ".dockcheck" / "policy.yaml").read_text())
        assert "confidence_thresholds" in data
        thresholds = data["confidence_thresholds"]
        assert "auto_deploy_staging" in thresholds
        assert "auto_promote_prod" in thresholds
        assert "notify_human" in thresholds

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_TEMPLATES))
    def test_policy_has_skills_section(self, tmp_path, template_name):
        target = tmp_path / template_name
        Scaffolder.scaffold(template_name, str(target))
        data = yaml.safe_load((target / ".dockcheck" / "policy.yaml").read_text())
        assert "skills" in data
        assert "enabled" in data["skills"]
        assert len(data["skills"]["enabled"]) > 0


class TestTemplateSpecificBehaviour:
    """Verify template-specific thresholds and settings."""

    def test_hackathon_has_permissive_staging_threshold(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path))
        data = yaml.safe_load((tmp_path / ".dockcheck" / "policy.yaml").read_text())
        assert data["confidence_thresholds"]["auto_deploy_staging"] == 0.6

    def test_hackathon_has_permissive_prod_threshold(self, tmp_path):
        Scaffolder.scaffold("hackathon", str(tmp_path))
        data = yaml.safe_load((tmp_path / ".dockcheck" / "policy.yaml").read_text())
        assert data["confidence_thresholds"]["auto_promote_prod"] == 0.7

    def test_trading_bot_has_strict_staging_threshold(self, tmp_path):
        Scaffolder.scaffold("trading-bot", str(tmp_path))
        data = yaml.safe_load((tmp_path / ".dockcheck" / "policy.yaml").read_text())
        assert data["confidence_thresholds"]["auto_deploy_staging"] == 0.95

    def test_trading_bot_has_strict_prod_threshold(self, tmp_path):
        Scaffolder.scaffold("trading-bot", str(tmp_path))
        data = yaml.safe_load((tmp_path / ".dockcheck" / "policy.yaml").read_text())
        assert data["confidence_thresholds"]["auto_promote_prod"] == 0.99

    def test_trading_bot_blocks_modify_position(self, tmp_path):
        Scaffolder.scaffold("trading-bot", str(tmp_path))
        data = yaml.safe_load((tmp_path / ".dockcheck" / "policy.yaml").read_text())
        patterns = [c["pattern"] for c in data["hard_stops"]["commands"]]
        assert "modify_position" in patterns

    def test_trading_bot_blocks_place_order(self, tmp_path):
        Scaffolder.scaffold("trading-bot", str(tmp_path))
        data = yaml.safe_load((tmp_path / ".dockcheck" / "policy.yaml").read_text())
        patterns = [c["pattern"] for c in data["hard_stops"]["commands"]]
        assert "place_order" in patterns

    def test_trading_bot_blocks_cancel_all(self, tmp_path):
        Scaffolder.scaffold("trading-bot", str(tmp_path))
        data = yaml.safe_load((tmp_path / ".dockcheck" / "policy.yaml").read_text())
        patterns = [c["pattern"] for c in data["hard_stops"]["commands"]]
        assert "cancel_all" in patterns

    def test_fastapi_test_command_is_pytest(self, tmp_path):
        Scaffolder.scaffold("fastapi-app", str(tmp_path))
        data = yaml.safe_load((tmp_path / "dockcheck.yml").read_text())
        assert "pytest" in data["project"]["test_command"]

    def test_fastapi_dockerfile_uses_python_base(self, tmp_path):
        Scaffolder.scaffold("fastapi-app", str(tmp_path))
        content = (tmp_path / "Dockerfile").read_text()
        assert "python" in content.lower()

    def test_fastapi_dockerfile_uses_uvicorn(self, tmp_path):
        Scaffolder.scaffold("fastapi-app", str(tmp_path))
        content = (tmp_path / "Dockerfile").read_text()
        assert "uvicorn" in content

    def test_react_test_command_is_npm(self, tmp_path):
        Scaffolder.scaffold("react-app", str(tmp_path))
        data = yaml.safe_load((tmp_path / "dockcheck.yml").read_text())
        assert "npm" in data["project"]["test_command"]

    def test_react_dockerfile_uses_node_base(self, tmp_path):
        Scaffolder.scaffold("react-app", str(tmp_path))
        content = (tmp_path / "Dockerfile").read_text()
        assert "node" in content.lower()

    def test_react_dockerfile_is_multistage(self, tmp_path):
        Scaffolder.scaffold("react-app", str(tmp_path))
        content = (tmp_path / "Dockerfile").read_text()
        # Multi-stage builds have more than one FROM statement
        assert content.count("FROM") >= 2

    def test_react_dockerfile_uses_nginx(self, tmp_path):
        Scaffolder.scaffold("react-app", str(tmp_path))
        content = (tmp_path / "Dockerfile").read_text()
        assert "nginx" in content.lower()
