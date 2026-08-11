import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import claude_code


class ClaudeCodeStructuredCallTests(unittest.TestCase):
    def test_calls_cli_non_interactively_and_returns_structured_output(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "is_error": False,
                    "structured_output": {
                        "article_zh": "真正的改变从行动开始。",
                        "ad_removed": False,
                    },
                }),
                stderr="",
            )

        schema = {
            "type": "object",
            "properties": {
                "article_zh": {"type": "string"},
                "ad_removed": {"type": "boolean"},
            },
            "required": ["article_zh", "ad_removed"],
        }
        result = claude_code.call_structured_json(
            "你是中文视频译稿编辑。",
            {"source_article_en": "Real change begins with action."},
            schema,
            cli_path="C:/tools/claude.exe",
            model="sonnet",
            effort="high",
            timeout=123,
            runner=runner,
        )

        self.assertEqual(
            result,
            {
                "article_zh": "真正的改变从行动开始。",
                "ad_removed": False,
            },
        )
        self.assertEqual(len(calls), 1)
        command, kwargs = calls[0]
        self.assertEqual(command[0], "C:/tools/claude.exe")
        self.assertIn("-p", command)
        self.assertIn("--safe-mode", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(
            command[command.index("--permission-mode") + 1],
            "dontAsk",
        )
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(
            command[command.index("--output-format") + 1],
            "json",
        )
        self.assertEqual(
            json.loads(command[command.index("--json-schema") + 1]),
            schema,
        )
        self.assertEqual(
            command[command.index("--system-prompt") + 1],
            "你是中文视频译稿编辑。",
        )
        self.assertEqual(command[command.index("--model") + 1], "sonnet")
        self.assertEqual(command[command.index("--effort") + 1], "high")
        self.assertEqual(
            json.loads(kwargs["input"]),
            {"source_article_en": "Real change begins with action."},
        )
        self.assertEqual(kwargs["timeout"], 123)

    @patch.object(
        claude_code.shutil,
        "which",
        return_value="C:/Users/test/.local/bin/claude.exe",
    )
    def test_locates_claude_on_path_when_cli_path_is_not_given(self, _which):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "is_error": False,
                    "structured_output": {"ok": True},
                }),
                stderr="",
            )

        result = claude_code.call_structured_json(
            "Return JSON.",
            {"value": 1},
            {"type": "object"},
            runner=runner,
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            commands[0][0],
            "C:/Users/test/.local/bin/claude.exe",
        )

    @patch.object(claude_code.shutil, "which", return_value=None)
    def test_reports_when_claude_cli_is_not_installed(self, _which):
        called = False

        def runner(_command, **_kwargs):
            nonlocal called
            called = True

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeNotFoundError,
            "找不到 Claude Code CLI",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                runner=runner,
            )

        self.assertFalse(called)

    def test_reports_an_invalid_explicit_cli_path_as_not_installed(self):
        def runner(_command, **_kwargs):
            raise FileNotFoundError("missing executable")

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeNotFoundError,
            "无法启动 Claude Code CLI",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/missing/claude.exe",
                runner=runner,
            )

    def test_reports_a_cli_timeout_clearly(self):
        def runner(command, **_kwargs):
            raise subprocess.TimeoutExpired(command, timeout=12)

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            r"12.*超时|超时.*12",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                timeout=12,
                runner=runner,
            )

    def test_reports_nonzero_exit_code_with_cli_error(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=3,
                stdout="",
                stderr="Claude authentication required",
            )

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            r"退出码 3.*authentication required",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

    def test_reports_the_stdout_error_reason_when_stderr_is_empty(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps({
                    "type": "result",
                    "is_error": True,
                    "api_error_status": 401,
                    "result": (
                        "Failed to authenticate. API Error: 401 OAuth access "
                        "token has expired. Re-authenticate to continue."
                    ),
                }),
                stderr="",
            )

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            r"退出码 1.*OAuth access token has expired.*HTTP 401",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

    def test_rejects_stdout_that_is_not_valid_json(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="not-json",
                stderr="",
            )

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            "不是有效 JSON",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

    def test_rejects_empty_stdout(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="   ",
                stderr="",
            )

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            "没有返回 JSON",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

    def test_rejects_a_cli_error_envelope_even_with_exit_code_zero(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "is_error": True,
                    "result": "Claude subscription authentication failed",
                    "structured_output": {"ok": True},
                }),
                stderr="",
            )

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            "subscription authentication failed",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

    def test_rejects_a_success_envelope_without_structured_output(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "is_error": False,
                    "result": "Plain text was returned instead.",
                }),
                stderr="",
            )

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            "缺少结构化输出",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

    def test_rejects_structured_output_that_is_not_an_object(self):
        def runner(_command, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "is_error": False,
                    "structured_output": ["not", "an", "object"],
                }),
                stderr="",
            )

        with self.assertRaisesRegex(
            claude_code.ClaudeCodeError,
            "结构化输出必须是 JSON 对象",
        ):
            claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

    def test_isolates_cli_from_tools_integrations_and_provider_overrides(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "is_error": False,
                    "structured_output": {"ok": True},
                }),
                stderr="",
            )

        provider_overrides = {
            "ANTHROPIC_API_KEY": "api-secret",
            "ANTHROPIC_AUTH_TOKEN": "auth-secret",
            "ANTHROPIC_BASE_URL": "https://proxy.invalid",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLAUDE_CODE_USE_FOUNDRY": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock.invalid",
            "ANTHROPIC_VERTEX_BASE_URL": "https://vertex.invalid",
            "ANTHROPIC_FOUNDRY_BASE_URL": "https://foundry.invalid",
        }
        with patch.dict(
            claude_code.os.environ,
            provider_overrides,
            clear=False,
        ):
            result = claude_code.call_structured_json(
                "Return JSON.",
                {"value": 1},
                {"type": "object"},
                cli_path="C:/tools/claude.exe",
                runner=runner,
            )

        self.assertEqual(result, {"ok": True})
        command, kwargs = calls[0]
        self.assertIn("--no-chrome", command)
        self.assertIn("--disable-slash-commands", command)
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(
            command[command.index("--input-format") + 1],
            "text",
        )
        self.assertEqual(kwargs["errors"], "strict")
        child_env = kwargs["env"]
        for name in provider_overrides:
            self.assertNotIn(name, child_env)
        self.assertEqual(child_env["NO_COLOR"], "1")
        self.assertEqual(
            child_env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"],
            "1",
        )


if __name__ == "__main__":
    unittest.main()
