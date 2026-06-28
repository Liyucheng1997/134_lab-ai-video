"""第 3 步：用 DeepSeek API 把句段批量翻译成中文，保持与时间戳一一对应。

策略：分批发送，带行号，要求模型逐行回译；做严格对齐校验，必要时回退到逐条翻译。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from . import config
from .utils import load_json, log, save_json

_SYS_PROMPT = (
    "你是专业的影视字幕翻译。把用户给出的每一行台词翻译成简体中文，"
    "要口语化、自然、简洁，适合做配音和字幕。"
    "严格保持行数一致：输入多少行，输出多少行，按相同序号一一对应，不要合并或拆分。"
    "只输出 JSON：{\"lines\": [{\"i\": 序号, \"zh\": \"中文\"}, ...]}，不要任何额外文字。"
)


def _call_deepseek(payload_lines: list[dict]) -> dict:
    user_content = json.dumps({"lines": payload_lines}, ensure_ascii=False)
    resp = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": _SYS_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 1.0,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=180,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def _translate_batch(batch: list[dict]) -> dict[int, str]:
    """batch: [{"i": idx, "text": ...}]，返回 {idx: 中文}。"""
    payload = [{"i": b["i"], "text": b["text"]} for b in batch]
    data = _call_deepseek(payload)
    out: dict[int, str] = {}
    for item in data.get("lines", []):
        try:
            out[int(item["i"])] = str(item["zh"]).strip()
        except (KeyError, ValueError, TypeError):
            continue
    return out


def translate(segments: list[dict], work_dir: Path, batch_size: int = 25) -> list[dict]:
    """给每个 segment 增加 'zh' 字段。结果缓存到 translated.json。"""
    cache = work_dir / "translated.json"
    cached = load_json(cache)
    if cached and len(cached) == len(segments):
        log("translate", "复用已有翻译")
        return cached

    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置或设为环境变量。")

    indexed = [{"i": i, "text": s["text"]} for i, s in enumerate(segments)]
    result: dict[int, str] = {}

    for start in range(0, len(indexed), batch_size):
        batch = indexed[start:start + batch_size]
        log("translate", f"翻译 {start + 1}-{start + len(batch)} / {len(indexed)}")
        try:
            got = _translate_batch(batch)
        except Exception as e:
            log("translate", f"批量失败（{e}），回退逐条")
            got = {}
        # 对齐校验：缺失的行逐条补译
        for b in batch:
            if b["i"] not in got or not got[b["i"]]:
                try:
                    single = _translate_batch([b])
                    got[b["i"]] = single.get(b["i"], "")
                except Exception:
                    got[b["i"]] = ""
        result.update(got)

    out = []
    for i, s in enumerate(segments):
        zh = result.get(i, "").strip() or s["text"]  # 兜底用原文，避免空字幕
        out.append({**s, "zh": zh})

    save_json(cache, out)
    log("translate", f"完成：{len(out)} 段")
    return out
