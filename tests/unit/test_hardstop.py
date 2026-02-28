"""Tests for hard stop detection — command blocklist and critical path matching."""

from pathlib import Path

import pytest

from dockcheck.tools.hardstop import (
    CriticalPathChecker,
    DiffAnalyzer,
    HardStopChecker,
)


class TestHardStopChecker:
    @pytest.fixture()
    def checker(self):
        return HardStopChecker()

    def test_safe_command(self, checker):
        result = checker.check_command("pytest tests/")
        assert result.blocked is False
        assert result.matches == []

    def test_rm_rf_blocked(self, checker):
        result = checker.check_command("rm -rf /tmp/build")
        assert result.blocked is True
        assert result.matches[0].matched_pattern == "rm -rf /"

    def test_drop_table_blocked(self, checker):
        result = checker.check_command("psql -c 'DROP TABLE users'")
        assert result.blocked is True

    def test_drop_database_blocked(self, checker):
        result = checker.check_command("DROP DATABASE production")
        assert result.blocked is True

    def test_force_push_blocked(self, checker):
        result = checker.check_command("git push --force origin main")
        assert result.blocked is True

    def test_force_push_short_flag_blocked(self, checker):
        result = checker.check_command("git push -f origin main")
        assert result.blocked is True

    def test_git_reset_hard_blocked(self, checker):
        result = checker.check_command("git reset --hard HEAD~3")
        assert result.blocked is True

    def test_terraform_destroy_blocked(self, checker):
        result = checker.check_command("terraform destroy -auto-approve")
        assert result.blocked is True

    def test_kubectl_delete_ns_blocked(self, checker):
        result = checker.check_command("kubectl delete namespace production")
        assert result.blocked is True

    def test_case_insensitive(self, checker):
        result = checker.check_command("DROP table Users")
        assert result.blocked is True

    def test_multiple_commands(self, checker):
        result = checker.check_commands([
            "echo hello",
            "rm -rf /",
            "DROP TABLE x",
            "ls -la",
        ])
        assert result.blocked is True
        assert len(result.matches) == 2

    def test_all_safe_commands(self, checker):
        result = checker.check_commands([
            "npm test",
            "docker build .",
            "git add .",
            "git commit -m 'feat: new thing'",
        ])
        assert result.blocked is False

    def test_custom_patterns(self):
        checker = HardStopChecker(patterns=["DANGER", "NUKE"])
        assert checker.check_command("run DANGER zone").blocked is True
        assert checker.check_command("safe command").blocked is False

    def test_summary_not_blocked(self, checker):
        result = checker.check_command("echo hello")
        assert "No hard stops" in result.summary

    def test_summary_blocked(self, checker):
        result = checker.check_command("rm -rf /")
        assert "BLOCKED" in result.summary


class TestCriticalPathChecker:
    @pytest.fixture()
    def checker(self):
        return CriticalPathChecker()

    def test_safe_path(self, checker):
        result = checker.check_path("src/app.py")
        assert result.blocked is False

    def test_production_path_blocked(self, checker):
        result = checker.check_path("deploy/production/config.yaml")
        assert result.blocked is True
        assert result.matches[0].matched_pattern == "**/production/**"

    def test_env_file_blocked(self, checker):
        result = checker.check_path(".env.production")
        assert result.blocked is True

    def test_env_local_blocked(self, checker):
        result = checker.check_path(".env.local")
        assert result.blocked is True

    def test_secrets_directory_blocked(self, checker):
        result = checker.check_path("config/secrets/api_key.json")
        assert result.blocked is True

    def test_multiple_paths(self, checker):
        result = checker.check_paths([
            "src/app.py",
            "deploy/production/main.tf",
            ".env",
            "tests/test_app.py",
        ])
        assert result.blocked is True
        assert len(result.matches) == 2

    def test_all_safe_paths(self, checker):
        result = checker.check_paths([
            "src/main.py",
            "tests/test_main.py",
            "docs/README.md",
        ])
        assert result.blocked is False

    def test_custom_patterns(self):
        checker = CriticalPathChecker(patterns=["**/infra/**"])
        assert checker.check_path("infra/main.tf").blocked is True
        assert checker.check_path("src/app.py").blocked is False


class TestDiffAnalyzer:
    def test_extract_file_paths_from_safe_diff(self):
        diff = (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+new\n"
        )
        paths = DiffAnalyzer.extract_file_paths(diff)
        assert paths == ["src/app.py"]

    def test_extract_multiple_file_paths(self):
        diff = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "--- a/src/utils.py\n"
            "+++ b/src/utils.py\n"
        )
        paths = DiffAnalyzer.extract_file_paths(diff)
        assert "src/app.py" in paths
        assert "src/utils.py" in paths

    def test_extract_added_lines(self):
        diff = (
            "@@ -1,3 +1,5 @@\n"
            " unchanged\n"
            "+added line 1\n"
            "+added line 2\n"
            " unchanged\n"
            "-removed\n"
        )
        added = DiffAnalyzer.extract_added_lines(diff)
        assert added == ["added line 1", "added line 2"]

    def test_count_file_deletes(self):
        diff = (
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "--- a/another.py\n"
            "+++ /dev/null\n"
        )
        assert DiffAnalyzer.count_file_deletes(diff) == 2

    def test_no_file_deletes(self):
        diff = (
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
        )
        assert DiffAnalyzer.count_file_deletes(diff) == 0

    def test_from_fixture_safe(self):
        diff_path = (
            Path(__file__).parent.parent / "fixtures" / "sample_diffs" / "safe_change.diff"
        )
        diff = diff_path.read_text()
        paths = DiffAnalyzer.extract_file_paths(diff)
        assert "src/app.py" in paths
        assert DiffAnalyzer.count_file_deletes(diff) == 0

    def test_from_fixture_dangerous(self):
        diff_path = (
            Path(__file__).parent.parent
            / "fixtures"
            / "sample_diffs"
            / "dangerous_change.diff"
        )
        diff = diff_path.read_text()
        paths = DiffAnalyzer.extract_file_paths(diff)
        assert "deploy/production/config.yaml" in paths
        assert ".env.production" in paths

    def test_from_fixture_file_delete(self):
        diff_path = (
            Path(__file__).parent.parent
            / "fixtures"
            / "sample_diffs"
            / "file_delete.diff"
        )
        diff = diff_path.read_text()
        assert DiffAnalyzer.count_file_deletes(diff) == 2
