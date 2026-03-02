"""Tests for SecretAuditor — enriched secret scanning with context and heuristics."""

from __future__ import annotations

from unittest.mock import patch

from dockcheck.tools.audit import SecretAuditor


class TestAuditBasic:
    def test_audit_basic_js_refs(self, tmp_path):
        """Scans a dir with JS refs, returns enriched contexts."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.OPENAI_API_KEY;\n")

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        assert result.total_references == 1
        assert "OPENAI_API_KEY" in result.unique_secrets
        assert len(result.contexts) == 1
        assert result.contexts[0].name == "OPENAI_API_KEY"

    def test_audit_basic_python_refs(self, tmp_path):
        """Scans a dir with Python refs."""
        src = tmp_path / "config.py"
        src.write_text('import os\ndb = os.environ["DATABASE_URL"]\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        assert result.total_references == 1
        assert "DATABASE_URL" in result.unique_secrets

    def test_empty_dir(self, tmp_path):
        """Empty directory returns empty audit."""
        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        assert result.total_references == 0
        assert result.unique_secrets == []
        assert result.contexts == []
        assert result.missing == []


class TestContextLines:
    def test_context_lines_extracted(self, tmp_path):
        """Surrounding code lines are present in context."""
        src = tmp_path / "app.py"
        src.write_text(
            "# line 1\n"
            "# line 2\n"
            "# line 3\n"
            "import os\n"
            'key = os.getenv("MY_SECRET")\n'
            "# line 6\n"
            "# line 7\n"
            "# line 8\n"
        )

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.line == 5
        # Should have lines 2-8 (3 before line 5 + line 5 + 3 after)
        assert len(ctx.context_lines) == 7
        assert 'key = os.getenv("MY_SECRET")' in ctx.context_lines

    def test_context_lines_at_file_start(self, tmp_path):
        """Context extraction handles refs near start of file."""
        src = tmp_path / "app.py"
        src.write_text('import os\nkey = os.getenv("MY_KEY")\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.line == 2
        assert len(ctx.context_lines) >= 2


class TestHasDefault:
    def test_python_getenv_with_default(self, tmp_path):
        """os.getenv("X", "fallback") → has_default=True."""
        src = tmp_path / "config.py"
        src.write_text('import os\nkey = os.getenv("API_KEY", "default-key")\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.has_default is True

    def test_python_environ_get_with_default(self, tmp_path):
        """os.environ.get("X", "fallback") → has_default=True."""
        src = tmp_path / "config.py"
        src.write_text('import os\nkey = os.environ.get("API_KEY", "fallback")\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.has_default is True

    def test_python_or_fallback(self, tmp_path):
        """os.environ.get("X") or "default" → has_default=True."""
        src = tmp_path / "config.py"
        src.write_text('import os\nkey = os.environ.get("API_KEY") or "default"\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.has_default is True

    def test_python_no_default(self, tmp_path):
        """os.environ["X"] → has_default=False."""
        src = tmp_path / "config.py"
        src.write_text('import os\ndb = os.environ["DATABASE_URL"]\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.has_default is False

    def test_js_or_fallback(self, tmp_path):
        """process.env.X || "default" → has_default=True."""
        src = tmp_path / "config.js"
        src.write_text('const key = process.env.API_KEY || "default";\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.has_default is True

    def test_js_nullish_coalescing(self, tmp_path):
        """process.env.X ?? "default" → has_default=True."""
        src = tmp_path / "config.js"
        src.write_text('const key = process.env.API_KEY ?? "fallback";\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.has_default is True

    def test_js_no_fallback(self, tmp_path):
        """process.env.X with no fallback → has_default=False."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.STRIPE_KEY;\n")

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.has_default is False


class TestInTestFile:
    def test_python_test_file(self, tmp_path):
        """Refs in test_*.py → in_test_file=True."""
        src = tmp_path / "test_config.py"
        src.write_text('import os\nkey = os.getenv("TEST_KEY")\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.in_test_file is True

    def test_js_spec_file(self, tmp_path):
        """Refs in *.spec.ts → in_test_file=True."""
        src = tmp_path / "app.spec.ts"
        src.write_text("const key = process.env.TEST_SECRET;\n")

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.in_test_file is True

    def test_tests_dir(self, tmp_path):
        """Refs in tests/ directory → in_test_file=True."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        src = test_dir / "conftest.py"
        src.write_text('import os\nkey = os.getenv("TEST_API_KEY")\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.in_test_file is True

    def test_production_file(self, tmp_path):
        """Refs in regular source → in_test_file=False."""
        src = tmp_path / "config.py"
        src.write_text('import os\nkey = os.getenv("PROD_KEY")\n')

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        ctx = result.contexts[0]
        assert ctx.in_test_file is False


class TestEnvCrossCheck:
    def test_missing_vs_available(self, tmp_path):
        """Secrets not in env or .env are listed as missing."""
        src = tmp_path / "app.js"
        src.write_text(
            "const a = process.env.FOUND_KEY;\n"
            "const b = process.env.MISSING_KEY;\n"
        )
        env = tmp_path / ".env"
        env.write_text("FOUND_KEY=abc\n")

        auditor = SecretAuditor()
        with patch.dict("os.environ", {}, clear=True):
            result = auditor.audit(str(tmp_path))

        assert "FOUND_KEY" in result.available_in_env
        assert "MISSING_KEY" in result.missing

    def test_available_via_os_environ(self, tmp_path):
        """Secrets set in os.environ are listed as available."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.RUNTIME_KEY;\n")

        auditor = SecretAuditor()
        with patch.dict("os.environ", {"RUNTIME_KEY": "value"}, clear=True):
            result = auditor.audit(str(tmp_path))

        assert "RUNTIME_KEY" in result.available_in_env
        assert "RUNTIME_KEY" not in result.missing

    def test_env_file_keys_populated(self, tmp_path):
        """Keys from .env file appear in env_file_keys."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.MY_KEY;\n")
        env = tmp_path / ".env"
        env.write_text("MY_KEY=value\nOTHER_KEY=123\n")

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        assert "MY_KEY" in result.env_file_keys
        assert "OTHER_KEY" in result.env_file_keys

    def test_all_missing_when_nothing_set(self, tmp_path):
        """All secrets missing when no .env and no os.environ."""
        src = tmp_path / "app.js"
        src.write_text(
            "const a = process.env.KEY_A;\n"
            "const b = process.env.KEY_B;\n"
        )

        auditor = SecretAuditor()
        with patch.dict("os.environ", {}, clear=True):
            result = auditor.audit(str(tmp_path))

        assert sorted(result.missing) == ["KEY_A", "KEY_B"]
        assert result.available_in_env == []


class TestAuditModel:
    def test_model_serialization(self, tmp_path):
        """AuditResult serializes to JSON."""
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.OPENAI_KEY;\n")

        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        json_str = result.model_dump_json()
        assert "OPENAI_KEY" in json_str
        assert "target_path" in json_str

    def test_target_path_is_absolute(self, tmp_path):
        """target_path in result is resolved absolute path."""
        auditor = SecretAuditor()
        result = auditor.audit(str(tmp_path))

        assert result.target_path == str(tmp_path.resolve())
