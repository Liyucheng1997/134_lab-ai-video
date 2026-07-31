"""安全调用 Claude Code CLI 并读取结构化 JSON 输出。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

from .utils import _NO_WINDOW

_PROVIDER_OVERRIDE_ENV = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_API_KEY",
}


class ClaudeCodeError(RuntimeError):
    """Claude Code CLI 调用或返回内容不可用。"""


class ClaudeCodeNotFoundError(ClaudeCodeError):
    """系统中找不到 Claude Code CLI。"""


def call_structured_json(
    system_prompt: str,
    user_payload: dict[str, Any],
    json_schema: dict[str, Any],
    *,
    cli_path: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    timeout: float = 300,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """以无工具、非交互方式调用 Claude Code，返回 schema 对应的对象。"""
    executable = cli_path or shutil.which("claude")
    if not executable:
        raise ClaudeCodeNotFoundError(
            "找不到 Claude Code CLI，请先安装 Claude Code 或传入 cli_path。"
        )
    command = [
        str(executable),
        "-p",
        "--safe-mode",
        "--no-chrome",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--input-format",
        "text",
        "--json-schema",
        json.dumps(json_schema, ensure_ascii=False, separators=(",", ":")),
        "--system-prompt",
        str(system_prompt),
    ]
    if model:
        command.extend(["--model", str(model)])
    if effort:
        command.extend(["--effort", str(effort)])

    run = runner or subprocess.run
    child_env = dict(os.environ)
    for name in _PROVIDER_OVERRIDE_ENV:
        child_env.pop(name, None)
    child_env["NO_COLOR"] = "1"
    child_env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] = "1"
    try:
        completed = run(
            command,
            input=json.dumps(user_payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout,
            check=False,
            creationflags=_NO_WINDOW,
            env=child_env,
        )
    except FileNotFoundError as exc:
        raise ClaudeCodeNotFoundError(
            f"无法启动 Claude Code CLI：{executable}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeError(
            f"Claude Code CLI 调用超过 {timeout:g} 秒，已超时。"
        ) from exc
    if completed.returncode != 0:
        detail = " ".join(str(completed.stderr or "").split())[-2000:]
        suffix = f"：{detail}" if detail else ""
        raise ClaudeCodeError(
            f"Claude Code CLI 调用失败（退出码 {completed.returncode}）{suffix}"
        )
    if not str(completed.stdout or "").strip():
        raise ClaudeCodeError("Claude Code CLI 没有返回 JSON。")
    try:
        envelope = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClaudeCodeError(
            "Claude Code CLI 输出不是有效 JSON。"
        ) from exc
    if isinstance(envelope, dict) and envelope.get("is_error"):
        detail = (
            envelope.get("result")
            or envelope.get("error")
            or envelope.get("message")
            or "Claude Code 报告未知错误"
        )
        raise ClaudeCodeError(f"Claude Code CLI 返回错误：{detail}")
    if not isinstance(envelope, dict) or "structured_output" not in envelope:
        raise ClaudeCodeError("Claude Code CLI 缺少结构化输出。")
    structured_output = envelope["structured_output"]
    if not isinstance(structured_output, dict):
        raise ClaudeCodeError(
            "Claude Code CLI 结构化输出必须是 JSON 对象。"
        )
    return structured_output
