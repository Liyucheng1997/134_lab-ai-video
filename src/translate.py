"""第 3 步：用 DeepSeek API 整体翻译视频字幕，保持与时间戳一一对应。

策略：把完整字幕稿一次性发给 DeepSeek，让模型先按视频上下文理解和润色，
再按原 ASR 段号输出中文。这样可以处理英文夹杂、跨段断句和口语顺序问题，
同时保留后续 TTS/字幕所需的段边界。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from . import config
from .utils import load_json, log, save_json

_CACHE_VERSION = "contextual-v4-tail-ad-trim"
_MAX_DEEPSEEK_LINES = 320
_MAX_DEEPSEEK_CHARS = 24000
_TAIL_AD_CHECK_LINES = 28

_REWRITE_LEVELS = {
    "low": {
        "name": "低级",
        "instruction": (
            "低级洗稿：以忠实翻译为主，只做轻度润色。"
            "保留原信息顺序和语气，修正生硬直译、跨行断句和明显不通顺处，"
            "让中文更连贯、自然、适合朗读。"
        ),
    },
    "medium": {
        "name": "中级",
        "instruction": (
            "中级洗稿：在不改变原意和事实的前提下，主动改写成更符合中文表达习惯的说法。"
            "可以合并语义重复、调整相邻字幕内的语序、补足中文必要的承接词，"
            "让逻辑更顺、语义更连贯，更符合中国人的阅读习惯和认知方式。"
        ),
    },
    "high": {
        "name": "高级",
        "instruction": (
            "高级洗稿：在严格不新增事实、不改变立场和核心含义的前提下，做较强的中文化重写。"
            "可以重组相邻字幕的表达、压缩啰嗦口语、弱化英文句式痕迹、强化中文因果和递进关系，"
            "输出像中文创作者自然写出的配音稿，但不得编造、夸张或偏离原文意思。"
        ),
    },
}

_SYS_PROMPT = (
    "你是专业的影视字幕翻译和中文配音稿改写师。"
    "用户会给你一整段视频字幕，包含每条字幕的序号、时间戳和原文。"
    "请先在内部通读全文，整合上下文、修正跨行断句、处理英文夹杂和口语省略，"
    "再输出适合中文配音和中文字幕的自然简体中文。"
    "要求：中文要口语化、顺畅、简洁；不要逐词硬译；人名、品牌名、术语按中文习惯处理；"
    "如果相邻字幕原本是一个完整句子，可以在理解后把中文语序自然分配到这些相邻序号中。"
    "严格保持输入序号集合一致：每个输入序号必须输出一条，不新增、不删除、不改序号。"
    "每条中文不要过长，适合单条字幕朗读；不要输出空字符串，除非原文完全不是可朗读内容。"
    "只输出 JSON：{\"lines\": [{\"i\": 序号, \"zh\": \"中文\"}, ...]}，不要任何额外文字。"
)


def _normalize_rewrite_level(level: str | None) -> str:
    level = (level or "low").strip().lower()
    aliases = {
        "低": "low", "低级": "low", "轻度": "low",
        "中": "medium", "中级": "medium", "中等": "medium",
        "高": "high", "高级": "high", "深度": "high",
    }
    level = aliases.get(level, level)
    return level if level in _REWRITE_LEVELS else "low"


def _parse_json_object(content: str) -> dict:
    """解析 DeepSeek 返回的 JSON；兼容偶发的代码块或前后缀文本。"""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _call_deepseek(payload_lines: list[dict], *, context_note: str = "",
                   rewrite_level: str = "low") -> dict:
    rewrite_level = _normalize_rewrite_level(rewrite_level)
    rewrite = _REWRITE_LEVELS[rewrite_level]
    payload = {
        "task": "translate_full_video_subtitles_with_context",
        "context_note": context_note,
        "rewrite_level": rewrite_level,
        "rewrite_instruction": rewrite["instruction"],
        "lines": payload_lines,
    }
    user_content = json.dumps(payload, ensure_ascii=False)
    sys_prompt = (
        _SYS_PROMPT
        + f"本次 AI 洗稿档位：{rewrite['name']}。{rewrite['instruction']}"
        + "洗稿必须服务于翻译质量：原意、事实、人称、情绪和逻辑关系不能被改变。"
    )
    resp = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": sys_prompt},
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
    return _parse_json_object(content)


def _payload_lines(items: list[dict]) -> list[dict]:
    lines = []
    for b in items:
        line = {"i": b["i"], "text": b["text"]}
        if "start" in b and "end" in b:
            line["start"] = b["start"]
            line["end"] = b["end"]
        lines.append(line)
    return lines


def _extract_lines(data: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for item in data.get("lines", []):
        try:
            out[int(item["i"])] = str(item["zh"]).strip()
        except (KeyError, ValueError, TypeError):
            continue
    return out


_AD_TRIM_SYS_PROMPT = (
    "你是视频字幕结尾质检员。用户会给你视频最后几条字幕，包含原文和中文译文。"
    "请判断结尾是否出现与正片主题无关的推广、广告、购买引导、下载引导、网站引流、"
    "课程/书籍/章节视频/独家内容销售、订阅或关注号召。"
    "只有这些内容位于视频尾部、且删除后不会破坏正片结论时，才裁剪。"
    "如果只是作者正常总结、祝福、结语，或仍在讲正文内容，不要裁剪。"
    "如果需要裁剪，返回从哪一个输入序号开始一直删到结尾。"
    "只输出 JSON：{\"trim\": true/false, \"cut_from_i\": 序号或 null, \"reason\": \"简短原因\"}。"
)


def _inspect_tail_ad_with_deepseek(out: list[dict]) -> dict:
    tail = out[-_TAIL_AD_CHECK_LINES:]
    payload = {
        "task": "inspect_video_tail_for_advertising",
        "instruction": (
            "检查最后几条字幕是否是尾部广告。广告例子包括："
            "downloadable now、at xxx.com、exclusive chapter videos、pages、course、book、"
            "visit website、buy、subscribe、follow、link in bio 等。"
            "命中时 cut_from_i 必须是广告开始的最早序号。"
        ),
        "lines": [
            {
                "i": int(s.get("i", idx)),
                "start": s.get("start"),
                "end": s.get("end"),
                "text": s.get("text", ""),
                "zh": s.get("zh", ""),
            }
            for idx, s in enumerate(tail)
        ],
    }
    resp = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": _AD_TRIM_SYS_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return _parse_json_object(resp.json()["choices"][0]["message"]["content"])


def _apply_tail_ad_trim(out: list[dict], decision: dict) -> tuple[list[dict], dict]:
    if not decision.get("trim"):
        return out, {"trimmed": False, "reason": decision.get("reason", "")}
    try:
        cut_from = int(decision["cut_from_i"])
    except (KeyError, TypeError, ValueError):
        return out, {"trimmed": False, "reason": "DeepSeek 未返回有效裁剪起点"}

    indexes = [int(s.get("i", idx)) for idx, s in enumerate(out)]
    if cut_from not in indexes:
        return out, {"trimmed": False, "reason": f"裁剪起点 {cut_from} 不在字幕序号中"}
    pos = indexes.index(cut_from)
    # 只允许裁剪尾部区域，避免模型误删正文中段。
    tail_start = max(0, len(out) - _TAIL_AD_CHECK_LINES)
    if pos < tail_start:
        return out, {"trimmed": False, "reason": f"裁剪起点 {cut_from} 不在尾部检测窗口内"}
    if pos == 0:
        return out, {"trimmed": False, "reason": "拒绝清空全部字幕"}

    removed = out[pos:]
    return out[:pos], {
        "trimmed": True,
        "cut_from_i": cut_from,
        "removed_count": len(removed),
        "removed_start": removed[0].get("start"),
        "removed_end": removed[-1].get("end"),
        "reason": decision.get("reason", ""),
    }


def _trim_tail_ad(out: list[dict], *, engine: str) -> tuple[list[dict], dict]:
    if engine != "deepseek":
        return out, {"trimmed": False, "reason": "非 DeepSeek 翻译引擎，跳过尾部广告质检"}
    if not out:
        return out, {"trimmed": False, "reason": "无字幕可检查"}
    try:
        decision = _inspect_tail_ad_with_deepseek(out)
        trimmed, meta = _apply_tail_ad_trim(out, decision)
        if meta.get("trimmed"):
            log(
                "translate",
                f"尾部广告裁剪：从第 {meta['cut_from_i'] + 1} 段开始删除 "
                f"{meta['removed_count']} 段（{meta.get('reason') or 'DeepSeek 判定为尾部推广'}）",
            )
        else:
            log("translate", "尾部广告质检：未发现需要裁剪的广告")
        return trimmed, meta
    except Exception as e:
        log("translate", f"尾部广告质检失败，保留原结尾（{e}）")
        return out, {"trimmed": False, "error": str(e)}


def _translate_contextual(items: list[dict], *, context_note: str = "",
                          rewrite_level: str = "low") -> dict[int, str]:
    """items: [{"i": idx, "text": ..., "start": ..., "end": ...}]，返回 {idx: 中文}。"""
    data = _call_deepseek(
        _payload_lines(items),
        context_note=context_note,
        rewrite_level=rewrite_level,
    )
    return _extract_lines(data)


def _translate_google(segments: list[dict]) -> dict[int, str]:
    """用 deep-translator 的免费 Google 翻译，逐条翻译（带缓存友好的分块日志）。"""
    from deep_translator import GoogleTranslator

    tr = GoogleTranslator(source="auto", target="zh-CN")
    out: dict[int, str] = {}
    for i, s in enumerate(segments):
        try:
            out[i] = (tr.translate(s["text"]) or "").strip()
        except Exception:
            out[i] = ""
        if (i + 1) % 25 == 0 or i + 1 == len(segments):
            log("translate", f"Google 翻译 {i + 1} / {len(segments)}")
    return out


def _split_for_context(segments: list[dict]) -> list[list[dict]]:
    """超长视频按相邻字幕切块；每块仍用上下文翻译，不做逐句翻译。"""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    chars = 0
    for i, s in enumerate(segments):
        text = s.get("text", "")
        item = {"i": i, "text": text, "start": s.get("start"), "end": s.get("end")}
        next_chars = chars + len(text)
        if cur and (len(cur) >= _MAX_DEEPSEEK_LINES or next_chars >= _MAX_DEEPSEEK_CHARS):
            chunks.append(cur)
            cur = []
            chars = 0
        cur.append(item)
        chars += len(text)
    if cur:
        chunks.append(cur)
    return chunks


def _translate_deepseek(segments: list[dict], batch_size: int,
                        rewrite_level: str = "low") -> dict[int, str]:
    indexed = [
        {"i": i, "text": s.get("text", ""), "start": s.get("start"), "end": s.get("end")}
        for i, s in enumerate(segments)
    ]
    result: dict[int, str] = {}
    chunks = _split_for_context(segments)
    multi_chunk = len(chunks) > 1
    for ci, chunk in enumerate(chunks, 1):
        first, last = chunk[0]["i"] + 1, chunk[-1]["i"] + 1
        if multi_chunk:
            prev_text = " ".join(s.get("text", "") for s in segments[max(0, chunk[0]["i"] - 8):chunk[0]["i"]])
            next_text = " ".join(s.get("text", "") for s in segments[chunk[-1]["i"] + 1:chunk[-1]["i"] + 9])
            note = (
                f"这是长视频的第 {ci}/{len(chunks)} 段字幕。"
                f"前文摘要线索：{prev_text[:1200]}。后文线索：{next_text[:1200]}。"
                "请保持术语、人称和语气与上下文一致。"
            )
            log("translate", f"DeepSeek 上下文翻译 {first}-{last} / {len(indexed)}")
        else:
            note = "这是完整视频的全部字幕，请按完整上下文翻译。"
            log("translate", f"DeepSeek 整段上下文翻译 1-{len(indexed)} / {len(indexed)}")
        try:
            got = _translate_contextual(chunk, context_note=note, rewrite_level=rewrite_level)
        except Exception as e:
            log("translate", f"上下文翻译失败（{e}），尝试缩小窗口重试")
            got = {}

        missing = [b for b in chunk if b["i"] not in got or not got[b["i"]]]
        if missing and len(chunk) > max(10, batch_size):
            for start in range(0, len(chunk), batch_size):
                sub = chunk[start:start + batch_size]
                log("translate", f"DeepSeek 补齐窗口 {sub[0]['i'] + 1}-{sub[-1]['i'] + 1}")
                try:
                    got.update(_translate_contextual(
                        sub,
                        context_note=note,
                        rewrite_level=rewrite_level,
                    ))
                except Exception:
                    pass
            missing = [b for b in chunk if b["i"] not in got or not got[b["i"]]]

        for b in missing:  # 最后兜底，避免空字幕/空配音
            if b["i"] not in got or not got[b["i"]]:
                try:
                    got[b["i"]] = _translate_contextual(
                        [b],
                        context_note="只补齐这个缺失序号。",
                        rewrite_level=rewrite_level,
                    ).get(b["i"], "")
                except Exception:
                    got[b["i"]] = ""
        result.update(got)
    return result


def translate(segments: list[dict], work_dir: Path, engine: str = "deepseek",
              batch_size: int = 25, rewrite_level: str = "low") -> list[dict]:
    """给每个 segment 增加 'zh' 字段。engine: deepseek | google。结果缓存到 translated.json。"""
    rewrite_level = _normalize_rewrite_level(rewrite_level)
    cache = work_dir / "translated.json"
    meta_cache = work_dir / "translated.meta.json"
    cached = load_json(cache)
    meta = load_json(meta_cache) or {}
    if (cached
            and meta.get("version") == _CACHE_VERSION
            and meta.get("engine") == engine
            and meta.get("rewrite_level", "low") == rewrite_level
            and meta.get("source_count", meta.get("count")) == len(segments)):
        log("translate", "复用已有翻译")
        return cached
    if cached:
        log("translate", "翻译算法或引擎已变化，重新生成翻译")

    log("translate", f"翻译引擎：{engine}")
    if engine == "google":
        result = _translate_google(segments)
    else:
        if not config.DEEPSEEK_API_KEY:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置或设为环境变量。")
        log("translate", f"AI 洗稿档位：{_REWRITE_LEVELS[rewrite_level]['name']}")
        result = _translate_deepseek(segments, batch_size, rewrite_level=rewrite_level)

    out = []
    for i, s in enumerate(segments):
        zh = result.get(i, "").strip() or s["text"]  # 兜底用原文，避免空字幕
        out.append({**s, "i": i, "zh": zh})

    out, trim_meta = _trim_tail_ad(out, engine=engine)

    save_json(cache, out)
    save_json(meta_cache, {
        "version": _CACHE_VERSION,
        "engine": engine,
        "rewrite_level": rewrite_level,
        "source_count": len(segments),
        "count": len(out),
        "tail_ad_trim": trim_meta,
    })
    log("translate", f"完成：{len(out)} 段")
    return out
