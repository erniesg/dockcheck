"""Tests for workspace models, YAML round-trip, auto-discovery, and topological sort."""

from __future__ import annotations

import pytest

from dockcheck.init.workspace import (
    AppSecretSpec,
    TargetConfig,
    WorkspaceConfig,
    WorkspaceResolver,
)

# ---------------------------------------------------------------------------
# WorkspaceConfig model
# ---------------------------------------------------------------------------


class TestWorkspaceConfig:
    def test_empty_config(self):
        ws = WorkspaceConfig()
        assert ws.name == ""
        assert ws.targets == []

    def test_config_with_targets(self):
        ws = WorkspaceConfig(
            name="myapp",
            targets=[
                TargetConfig(name="api", path="apps/api", provider="fly"),
                TargetConfig(name="web", path="apps/web", provider="cloudflare"),
            ],
        )
        assert len(ws.targets) == 2
        assert ws.targets[0].name == "api"

    def test_target_with_app_secrets(self):
        t = TargetConfig(
            name="api",
            path="api",
            app_secrets=[
                AppSecretSpec(name="OPENAI_API_KEY", source="scanned"),
            ],
        )
        assert len(t.app_secrets) == 1
        assert t.app_secrets[0].source == "scanned"

    def test_target_depends_on(self):
        t = TargetConfig(name="web", path="web", depends_on=["api"])
        assert t.depends_on == ["api"]


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


class TestYamlRoundTrip:
    def test_round_trip_basic(self):
        ws = WorkspaceConfig(
            name="test",
            targets=[
                TargetConfig(name="api", path="api", provider="fly"),
            ],
        )
        yaml_str = ws.to_yaml()
        parsed = WorkspaceConfig.from_yaml(yaml_str)
        assert parsed.name == "test"
        assert len(parsed.targets) == 1
        assert parsed.targets[0].name == "api"
        assert parsed.targets[0].provider == "fly"

    def test_round_trip_with_secrets_and_deps(self):
        ws = WorkspaceConfig(
            name="mono",
            targets=[
                TargetConfig(
                    name="api",
                    path="apps/api",
                    provider="fly",
                    app_secrets=[AppSecretSpec(name="DB_URL")],
                ),
                TargetConfig(
                    name="web",
                    path="apps/web",
                    provider="cloudflare",
                    depends_on=["api"],
                ),
            ],
        )
        yaml_str = ws.to_yaml()
        parsed = WorkspaceConfig.from_yaml(yaml_str)
        assert len(parsed.targets) == 2
        assert parsed.targets[0].app_secrets[0].name == "DB_URL"
        assert parsed.targets[1].depends_on == ["api"]

    def test_from_yaml_empty(self):
        ws = WorkspaceConfig.from_yaml("")
        assert ws.name == ""
        assert ws.targets == []

    def test_from_yaml_none_content(self):
        ws = WorkspaceConfig.from_yaml("---\n")
        assert ws.name == ""


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------


class TestAutoDiscovery:
    def test_discovers_two_wrangler_subdirs(self, tmp_path):
        (tmp_path / "worker-a").mkdir()
        (tmp_path / "worker-a" / "wrangler.toml").write_text("[env]\n")
        (tmp_path / "worker-b").mkdir()
        (tmp_path / "worker-b" / "wrangler.toml").write_text("[env]\n")

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))

        assert ws is not None
        assert len(ws.targets) == 2
        names = {t.name for t in ws.targets}
        assert names == {"worker-a", "worker-b"}
        assert all(t.provider == "cloudflare" for t in ws.targets)

    def test_discovers_mixed_providers(self, tmp_path):
        (tmp_path / "api").mkdir()
        (tmp_path / "api" / "fly.toml").write_text("")
        (tmp_path / "web").mkdir()
        (tmp_path / "web" / "vercel.json").write_text("{}")

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))

        assert ws is not None
        providers = {t.name: t.provider for t in ws.targets}
        assert providers["api"] == "fly"
        assert providers["web"] == "vercel"

    def test_discovers_in_apps_subdir(self, tmp_path):
        apps = tmp_path / "apps"
        apps.mkdir()
        (apps / "server").mkdir()
        (apps / "server" / "fly.toml").write_text("")
        (apps / "client").mkdir()
        (apps / "client" / "wrangler.toml").write_text("")

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))

        assert ws is not None
        assert len(ws.targets) == 2

    def test_single_target_returns_none(self, tmp_path):
        """Single target is not a workspace."""
        (tmp_path / "api").mkdir()
        (tmp_path / "api" / "fly.toml").write_text("")

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))
        assert ws is None

    def test_no_targets_returns_none(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')")

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))
        assert ws is None

    def test_explicit_yaml_takes_precedence(self, tmp_path):
        # Create an explicit workspace config
        ws_yaml = tmp_path / "dockcheck.workspace.yaml"
        ws_yaml.write_text(
            "name: explicit\ntargets:\n  - name: x\n    path: x\n"
        )
        # Also create auto-discoverable dirs
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "fly.toml").write_text("")
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "fly.toml").write_text("")

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))
        assert ws is not None
        assert ws.name == "explicit"
        assert len(ws.targets) == 1

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "wrangler.toml").write_text("")
        (tmp_path / "visible").mkdir()
        (tmp_path / "visible" / "wrangler.toml").write_text("")
        (tmp_path / "other").mkdir()
        (tmp_path / "other" / "fly.toml").write_text("")

        resolver = WorkspaceResolver()
        ws = resolver.resolve(str(tmp_path))

        assert ws is not None
        names = {t.name for t in ws.targets}
        assert ".hidden" not in names


# ---------------------------------------------------------------------------
# Topological sort (resolve_target_order)
# ---------------------------------------------------------------------------


class TestResolveTargetOrder:
    def test_no_dependencies_single_layer(self):
        targets = [
            TargetConfig(name="a", path="a"),
            TargetConfig(name="b", path="b"),
        ]
        layers = WorkspaceResolver.resolve_target_order(targets)
        assert len(layers) == 1
        names = {t.name for t in layers[0]}
        assert names == {"a", "b"}

    def test_linear_chain(self):
        targets = [
            TargetConfig(name="a", path="a"),
            TargetConfig(name="b", path="b", depends_on=["a"]),
            TargetConfig(name="c", path="c", depends_on=["b"]),
        ]
        layers = WorkspaceResolver.resolve_target_order(targets)
        assert len(layers) == 3
        assert layers[0][0].name == "a"
        assert layers[1][0].name == "b"
        assert layers[2][0].name == "c"

    def test_parallel_targets(self):
        targets = [
            TargetConfig(name="api", path="api"),
            TargetConfig(name="worker", path="worker"),
            TargetConfig(name="web", path="web", depends_on=["api"]),
        ]
        layers = WorkspaceResolver.resolve_target_order(targets)
        assert len(layers) == 2
        layer0_names = {t.name for t in layers[0]}
        assert layer0_names == {"api", "worker"}
        assert layers[1][0].name == "web"

    def test_diamond_dependency(self):
        targets = [
            TargetConfig(name="a", path="a"),
            TargetConfig(name="b", path="b", depends_on=["a"]),
            TargetConfig(name="c", path="c", depends_on=["a"]),
            TargetConfig(name="d", path="d", depends_on=["b", "c"]),
        ]
        layers = WorkspaceResolver.resolve_target_order(targets)
        assert len(layers) == 3

    def test_cycle_raises(self):
        targets = [
            TargetConfig(name="a", path="a", depends_on=["b"]),
            TargetConfig(name="b", path="b", depends_on=["a"]),
        ]
        with pytest.raises(ValueError, match="Cyclic dependency"):
            WorkspaceResolver.resolve_target_order(targets)

    def test_unknown_dep_raises(self):
        targets = [
            TargetConfig(name="a", path="a", depends_on=["missing"]),
        ]
        with pytest.raises(ValueError, match="unknown target 'missing'"):
            WorkspaceResolver.resolve_target_order(targets)

    def test_empty_targets(self):
        layers = WorkspaceResolver.resolve_target_order([])
        assert layers == []
