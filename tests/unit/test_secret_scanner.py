"""Tests for SecretScanner — env var detection in JS/TS/Python source code."""

from __future__ import annotations

from dockcheck.init.secret_scanner import SecretScanner


class TestJSPatterns:
    def test_process_env_dot(self, tmp_path):
        src = tmp_path / "app.js"
        src.write_text("const key = process.env.OPENAI_API_KEY;\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "OPENAI_API_KEY" in result.unique_names

    def test_process_env_bracket_double_quotes(self, tmp_path):
        src = tmp_path / "app.ts"
        src.write_text('const key = process.env["STRIPE_SECRET"];\n')

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "STRIPE_SECRET" in result.unique_names

    def test_process_env_bracket_single_quotes(self, tmp_path):
        src = tmp_path / "app.tsx"
        src.write_text("const key = process.env['DATABASE_URL'];\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "DATABASE_URL" in result.unique_names

    def test_import_meta_env(self, tmp_path):
        src = tmp_path / "config.ts"
        src.write_text("const api = import.meta.env.VITE_API_URL;\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "VITE_API_URL" in result.unique_names


class TestPythonPatterns:
    def test_os_environ_bracket(self, tmp_path):
        src = tmp_path / "config.py"
        src.write_text('import os\ndb = os.environ["DATABASE_URL"]\n')

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "DATABASE_URL" in result.unique_names

    def test_os_environ_get(self, tmp_path):
        src = tmp_path / "config.py"
        src.write_text('import os\nkey = os.environ.get("ANTHROPIC_API_KEY", "")\n')

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "ANTHROPIC_API_KEY" in result.unique_names

    def test_os_getenv(self, tmp_path):
        src = tmp_path / "config.py"
        src.write_text('import os\nkey = os.getenv("REDIS_URL")\n')

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "REDIS_URL" in result.unique_names


class TestDotenvParsing:
    def test_dotenv_keys(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=sk-abc123\nDATABASE_URL=postgres://...\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "OPENAI_API_KEY" in result.unique_names
        assert "DATABASE_URL" in result.unique_names

    def test_dotenv_skips_comments(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# This is a comment\nSECRET_KEY=abc\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "SECRET_KEY" in result.unique_names

    def test_env_example_file(self, tmp_path):
        env = tmp_path / ".env.example"
        env.write_text("API_KEY=\nDB_HOST=\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "API_KEY" in result.unique_names
        assert "DB_HOST" in result.unique_names


class TestFiltering:
    def test_excludes_deploy_secrets(self, tmp_path):
        src = tmp_path / "deploy.js"
        src.write_text("const t = process.env.CLOUDFLARE_API_TOKEN;\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "CLOUDFLARE_API_TOKEN" not in result.unique_names

    def test_excludes_non_secret_vars(self, tmp_path):
        src = tmp_path / "app.js"
        src.write_text("const env = process.env.NODE_ENV;\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "NODE_ENV" not in result.unique_names

    def test_deduplicates(self, tmp_path):
        src1 = tmp_path / "a.js"
        src1.write_text("process.env.OPENAI_API_KEY;\n")
        src2 = tmp_path / "b.js"
        src2.write_text("process.env.OPENAI_API_KEY;\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert result.unique_names.count("OPENAI_API_KEY") == 1
        assert len([r for r in result.refs if r.name == "OPENAI_API_KEY"]) == 2

    def test_results_sorted(self, tmp_path):
        src = tmp_path / "app.js"
        src.write_text(
            "process.env.ZEBRA_KEY;\n"
            "process.env.ALPHA_KEY;\n"
        )

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert result.unique_names == ["ALPHA_KEY", "ZEBRA_KEY"]


class TestEdgeCases:
    def test_empty_dir(self, tmp_path):
        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert result.refs == []
        assert result.unique_names == []

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("process.env.SECRET_KEY;\n")

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "SECRET_KEY" not in result.unique_names

    def test_nonexistent_dir(self, tmp_path):
        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path / "nonexistent"))

        assert result.refs == []

    def test_refs_have_line_numbers(self, tmp_path):
        src = tmp_path / "app.py"
        src.write_text('# line 1\nimport os\nkey = os.getenv("MY_SECRET")\n')

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        ref = next(r for r in result.refs if r.name == "MY_SECRET")
        assert ref.line == 3
        assert ref.file_path == "app.py"


class TestWranglerVars:
    def test_wrangler_vars_section(self, tmp_path):
        wrangler = tmp_path / "wrangler.toml"
        wrangler.write_text(
            "[vars]\n"
            "API_ENDPOINT = \"https://api.example.com\"\n"
            "SECRET_TOKEN = \"abc\"\n"
            "\n"
            "[env.production]\n"
        )

        scanner = SecretScanner()
        result = scanner.scan(str(tmp_path))

        assert "API_ENDPOINT" in result.unique_names
        assert "SECRET_TOKEN" in result.unique_names
