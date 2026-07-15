"""第 3 步：把英文转写合成全文，再整体翻译、去广告并重建中文句段。

DeepSeek 不再承担原 ASR 句段与中文句段的一一对齐。它只接收一篇连续英文稿，
返回一篇完整中文配音稿；本模块随后按中文语义分句，并生成供预览和 TTS 使用的
连续预估时间。第 4 步仍会按实际合成音频重新精确计时。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import requests

from . import config
from .utils import load_json, log, save_json

_CACHE_VERSION = "whole-article-v3-spoken-punctuation"
_ARTICLE_CACHE_NAME = "translation.article.json"
_SENTENCE_ENDINGS = "。！？!?."
_SOFT_SPLIT_PUNCT = "，,；;：:"
_CLOSING_QUOTES = "”’\"」』）》】"
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LONG_ENGLISH_PHRASE_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Za-z][A-Za-z'’-]*[ \t]+){3,}[A-Za-z][A-Za-z'’-]*(?![A-Za-z])"
)

_REWRITE_LEVELS = {
    "low": {
        "name": "低级",
        "instruction": (
            "低级洗稿：以忠实翻译为主，只做轻度润色。"
            "保留原信息顺序和语气，修正生硬直译、跨句断裂和明显不通顺处。"
        ),
    },
    "medium": {
        "name": "中级",
        "instruction": (
            "中级洗稿：在不改变原意和事实的前提下，把全文主动改写成自然的中文口播稿。"
            "允许合并重复表达、调整跨句语序、补足必要承接词，让论述前后连贯，"
            "但不得新增观点、案例、结论或夸张原意。"
        ),
    },
    "high": {
        "name": "深度顺稿",
        "instruction": (
            "深度顺稿：先完全理解整篇英文在说什么，再像中文母语创作者口头讲述一样重新组织全文，"
            "目标是顺口、好懂、有自然的说话节奏，不能保留英文翻译腔。"
            "遇到包含多个从句、插入语、长定语或抽象名词堆叠的英文长句，绝对不要硬译成一个中文长句；"
            "先拆出人物、动作、原因、转折和结果，再按逻辑拆成两到四个简单中文短句。"
            "原则上一个句子只表达一个重点，多用主动句和日常口语，少用层层嵌套的定语结构、"
            "生硬被动句、书面套话和机械套用这就是为什么。"
            "相邻短句之间要补上自然但克制的承接，让人一听就能跟上；不要为了口语化反复添加"
            "其实、你知道、换句话说等口头禅。"
            "不要使用任何引号给概念、关键词或术语加框，也不要用所谓的某某这种结构刻意强调，"
            "直接用正常中文把意思说清楚。原文如果是直接引语，优先改成自然的转述；"
            "确实需要保留说话内容时，用他说，后面接内容这种口语结构，不要加引号。"
            "不要使用破折号、冒号、分号、省略号或括号来补充说明，这些会让配音产生生硬停顿。"
            "标点尽量只使用逗号、句号、问号和感叹号，补充信息要改写成完整而自然的短句。"
            "如果英文转写本身有断句错误、语序混乱、口误、重复、自我修正或不完整表达，"
            "不要照着错误结构硬翻。要结合上下文判断真正想表达的意思，再用中国人习惯的主谓顺序、"
            "因果顺序和口头说法重新讲顺，但不能借润色之名添加原文没有的观点。"
            "可以跨句调整语序、合并重复和拆分长句，但这不是摘要：原文中的事实、人物、"
            "因果、条件、转折、程度、情绪和结论都必须保留，不能缩减关键信息、编造内容或夸大原意。"
            "最终稿应像中文作者直接写出的配音稿，每句通常控制在十二到三十个中文字，"
            "确有必要时可以稍长，但必须完整、自然、适合单独朗读。"
        ),
    },
}

_ARTICLE_SYS_PROMPT = (
    "你是专业的英文视频译稿编辑和中文配音稿改写师。"
    "用户提供的是已按原顺序合并的一整篇英文语音转写稿，不含需要保留的时间轴。"
    "必须先在内部通读全文，恢复被语音识别切断的句子，理解人物、指代、术语和上下文，"
    "然后把全文一次性改写为自然、连贯、适合朗读的简体中文文章。"
    "不要逐行翻译，不要沿用英文断句，不要输出中英对照。"
    "中文必须完整覆盖正文信息；专有名词可保留必要的英文缩写或品牌名，"
    "但不能留下未翻译的英文句子或连续英文短语。"
    "同时删除与正文无关的广告和引流，包括赞助口播、购买或下载引导、课程或书籍推销、"
    "网址导流、关注订阅号召、片头片尾频道推广；如果品牌或产品本身是正文讨论对象则保留。"
    "删除广告后要自然衔接前后文，不要在中文稿里提到‘已删除广告’。"
    "完成后自行检查一遍：语句是否通顺、上下文是否连贯、是否还有未翻译英文、"
    "是否误删正文或遗留广告。"
    "只输出 JSON 对象："
    "{\"article_zh\":\"完整中文文章\",\"ad_removed\":true/false,"
    "\"ad_notes\":\"简短说明，没有则留空\"}。不要输出按序号排列的句段。"
)


def _normalize_rewrite_level(level: str | None) -> str:
    level = (level or "high").strip().lower()
    aliases = {
        "低": "low", "低级": "low", "轻度": "low",
        "中": "medium", "中级": "medium", "中等": "medium",
        "高": "high", "高级": "high", "深度": "high", "深度顺稿": "high",
    }
    level = aliases.get(level, level)
    return level if level in _REWRITE_LEVELS else "low"


def _parse_json_object(content: str) -> dict:
    """解析 DeepSeek 返回的 JSON；兼容偶发的代码块或前后缀文本。"""
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _build_source_article(segments: list[dict]) -> str:
    """按原顺序把 ASR 碎片合成一篇连续英文稿，不把时间轴交给模型。"""
    parts = [re.sub(r"\s+", " ", str(s.get("text") or "")).strip() for s in segments]
    article = " ".join(part for part in parts if part)
    article = re.sub(r"\s+([,.;:!?])", r"\1", article)
    article = re.sub(r"([\(\[\{])\s+", r"\1", article)
    return re.sub(r"\s+", " ", article).strip()


def _article_digest(article: str) -> str:
    return hashlib.sha256(article.encode("utf-8")).hexdigest()


def _max_output_tokens(article: str) -> int:
    configured = int(getattr(config, "DEEPSEEK_MAX_OUTPUT_TOKENS", 8192) or 8192)
    # 英译中通常不会超过源字符数；给短稿也保留足够的 JSON 和润色空间。
    estimated = max(2048, int(len(article) * 1.15))
    return max(1024, min(configured, estimated))


def _call_deepseek_article(source_article: str, *, rewrite_level: str,
                           retry_note: str = "") -> dict:
    rewrite_level = _normalize_rewrite_level(rewrite_level)
    rewrite = _REWRITE_LEVELS[rewrite_level]
    payload = {
        "task": "translate_complete_english_article_to_clean_chinese_voiceover",
        "rewrite_level": rewrite_level,
        "rewrite_instruction": rewrite["instruction"],
        "source_article_en": source_article,
    }
    if retry_note:
        payload["quality_retry"] = retry_note

    response = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        _ARTICLE_SYS_PROMPT
                        + f"本次洗稿档位：{rewrite['name']}。{rewrite['instruction']}"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.35,
            "max_tokens": _max_output_tokens(source_article),
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=300,
    )
    response.raise_for_status()
    body = response.json()
    choice = body["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            "DeepSeek 中文全文达到输出上限，请提高 DEEPSEEK_MAX_OUTPUT_TOKENS 或缩短原视频"
        )
    return _parse_json_object(choice["message"]["content"])


def _extract_chinese_article(data: dict) -> str:
    for key in ("article_zh", "zh_article", "translated_article", "content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            article = value.strip()
            article = re.sub(r"[ \t]+", " ", article)
            article = re.sub(r"\s*\n\s*", "\n", article)
            return article
    raise ValueError("DeepSeek 未返回 article_zh 完整中文稿")


def _normalize_deep_polish_punctuation(article: str) -> str:
    """清除会让 TTS 产生书面化停顿的标记，仅用于深度顺稿。"""
    text = re.sub(r"[“”‘’„‟\"「」『』]", "", article or "")
    text = re.sub(r"\s*[—–―]{1,}\s*", "，", text)
    text = re.sub(r"\s*(?:……+|…+|\.{3,})\s*", "，", text)
    text = re.sub(r"\s*[：:；;]+\s*", "，", text)
    text = re.sub(r"\s*[,，]+\s*", "，", text)
    text = re.sub(r"，([。！？!?])", r"\1", text)
    text = re.sub(r"([。！？!?])，", r"\1", text)
    text = re.sub(r"(^|\n)，", r"\1", text)
    return text.strip(" ，\n")


def _article_quality_issue(article: str) -> str:
    cjk_count = len(_CJK_RE.findall(article))
    visible_count = len(re.sub(r"\s+", "", article))
    if visible_count < 2 or cjk_count < max(2, int(visible_count * 0.25)):
        return "返回内容不像完整中文译稿"
    match = _LONG_ENGLISH_PHRASE_RE.search(article)
    if match:
        return f"仍包含疑似未翻译英文：{match.group(0)[:80]}"
    return ""


def _translate_deepseek_article(source_article: str,
                                rewrite_level: str) -> tuple[str, dict]:
    """全文翻译；质量校验失败时整篇重试一次，绝不拿英文冒充中文。"""
    last_issue = ""
    for attempt in range(2):
        data = _call_deepseek_article(
            source_article,
            rewrite_level=rewrite_level,
            retry_note=(
                f"上一次结果未通过质检：{last_issue}。请重新翻译完整原稿，"
                "不要只修补局部，并严格清除未翻译英文和广告。"
                if attempt else ""
            ),
        )
        article = _extract_chinese_article(data)
        if rewrite_level == "high":
            article = _normalize_deep_polish_punctuation(article)
        last_issue = _article_quality_issue(article)
        if not last_issue:
            return article, {
                "ad_removed": bool(data.get("ad_removed", False)),
                "ad_notes": str(data.get("ad_notes") or "").strip(),
                "quality_retry_count": attempt,
            }
        log("translate", f"中文全文质检未通过，整篇重试：{last_issue}")
    raise RuntimeError(f"DeepSeek 连续两次未返回合格中文全文：{last_issue}")


def _text_size(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _split_after_punctuation(text: str, punctuation: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for pos, char in enumerate(text):
        if char not in punctuation:
            continue
        piece = text[start:pos + 1].strip()
        if piece:
            parts.append(piece)
        start = pos + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts or ([text.strip()] if text.strip() else [])


def _split_long_chinese_sentence(sentence: str, max_chars: int) -> list[str]:
    """只在中文软标点处分段；末尾过短时合回上一段，避免 TTS 朗读碎片。"""
    if max_chars <= 0 or _text_size(sentence) <= max_chars:
        return [sentence.strip()]
    clauses = _split_after_punctuation(sentence, _SOFT_SPLIT_PUNCT)
    if len(clauses) <= 1:
        return [sentence.strip()]

    parts: list[str] = []
    current = ""
    for clause in clauses:
        candidate = current + clause
        if current and _text_size(candidate) > max_chars:
            parts.append(current.strip())
            current = clause
        else:
            current = candidate
    if current.strip():
        parts.append(current.strip())

    min_chars = max(8, max_chars // 3)
    if len(parts) > 1 and _text_size(parts[-1]) < min_chars:
        parts[-2] += parts[-1]
        parts.pop()
    return parts or [sentence.strip()]


def _split_chinese_sentences(paragraph: str) -> list[str]:
    """按句末标点切分，并把紧随其后的右引号留在上一句。"""
    out: list[str] = []
    start = 0
    pos = 0
    while pos < len(paragraph):
        if paragraph[pos] not in _SENTENCE_ENDINGS:
            pos += 1
            continue
        end = pos + 1
        while end < len(paragraph) and paragraph[end] in _CLOSING_QUOTES:
            end += 1
        piece = paragraph[start:end].strip()
        if piece:
            out.append(piece)
        start = end
        pos = end
    tail = paragraph[start:].strip()
    if tail:
        out.append(tail)
    return out or ([paragraph.strip()] if paragraph.strip() else [])


def _split_zh_article(article: str, max_chars: int | None = None) -> list[str]:
    """先按完整中文句号分句，再仅对过长句在逗号/分号处做语义软切分。"""
    if max_chars is None:
        max_chars = int(getattr(config, "TRANSLATE_MAX_ZH_SEGMENT_CHARS", 42) or 0)
    normalized = re.sub(r"[ \t]+", " ", article or "").strip()
    paragraphs = [p.strip() for p in re.split(r"\s*\n+\s*", normalized) if p.strip()]
    out: list[str] = []
    for paragraph in paragraphs or ([normalized] if normalized else []):
        sentences = _split_chinese_sentences(paragraph)
        for sentence in sentences:
            out.extend(_split_long_chinese_sentence(sentence, max_chars))
    return [part for part in out if part]


def _number(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _source_position(source: dict, index: int, count: int,
                     source_start: float, source_duration: float) -> float:
    start = _number(source.get("start"), source_start)
    end = _number(source.get("end"), start)
    if source_duration > 0 and end >= start:
        return min(0.999999, max(0.0, ((start + end) / 2 - source_start) / source_duration))
    return (index + 0.5) / max(1, count)


def _retime_article_segments(sentences: list[str], source_segments: list[dict]) -> list[dict]:
    """按中文信息量生成连续预估时间，并仅为审阅近似挂接原英文片段。"""
    if not sentences:
        return []
    starts = [_number(s.get("start"), 0.0) for s in source_segments]
    ends = [_number(s.get("end"), starts[i]) for i, s in enumerate(source_segments)]
    source_start = min(starts) if starts else 0.0
    source_end = max(ends) if ends else source_start
    source_duration = max(0.0, source_end - source_start)
    if source_duration <= 0:
        source_duration = max(1.0, sum(max(1, _text_size(s)) for s in sentences) / 4.0)
        source_end = source_start + source_duration

    weights = [max(1, _text_size(sentence)) for sentence in sentences]
    total_weight = sum(weights)
    boundaries = []
    running = 0
    for weight in weights:
        running += weight
        boundaries.append(running / total_weight)

    source_buckets: list[list[tuple[int, dict]]] = [[] for _ in sentences]
    for index, source in enumerate(source_segments):
        position = _source_position(
            source, index, len(source_segments), source_start, source_duration
        )
        bucket = next((i for i, boundary in enumerate(boundaries) if position < boundary),
                      len(sentences) - 1)
        source_buckets[bucket].append((index, source))

    out: list[dict] = []
    cursor_weight = 0
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        start = source_start + source_duration * (cursor_weight / total_weight)
        cursor_weight += weight
        end = source_end if index == len(sentences) - 1 else (
            source_start + source_duration * (cursor_weight / total_weight)
        )
        bucket = source_buckets[index]
        source_indexes = [source_index for source_index, _ in bucket]
        source_text = " ".join(
            re.sub(r"\s+", " ", str(source.get("text") or "")).strip()
            for _, source in bucket if str(source.get("text") or "").strip()
        )
        out.append({
            "i": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": source_text,
            "zh": sentence,
            "source_indexes": source_indexes,
            "timing": "estimated_from_full_article",
        })
    return out


def _translate_google_segments(segments: list[dict]) -> list[dict]:
    """Google 仍是逐条免费回退方案；DeepSeek 才使用全文文章模式。"""
    from deep_translator import GoogleTranslator

    translator = GoogleTranslator(source="auto", target="zh-CN")
    out = []
    for index, source in enumerate(segments):
        try:
            zh = (translator.translate(source.get("text", "")) or "").strip()
        except Exception:
            zh = ""
        if not zh:
            raise RuntimeError(f"Google 翻译第 {index + 1} 段失败，已停止，避免英文进入中文配音")
        out.append({**source, "i": index, "zh": zh, "source_indexes": [index]})
        if (index + 1) % 25 == 0 or index + 1 == len(segments):
            log("translate", f"Google 翻译 {index + 1} / {len(segments)}")
    return out


def translate(segments: list[dict], work_dir: Path, engine: str = "deepseek",
              batch_size: int = 25, rewrite_level: str = "high") -> list[dict]:
    """生成 translated.json；batch_size 仅为兼容旧调用，全文模式不会分批。"""
    del batch_size
    rewrite_level = _normalize_rewrite_level(rewrite_level)
    work_dir.mkdir(parents=True, exist_ok=True)
    cache = work_dir / "translated.json"
    meta_cache = work_dir / "translated.meta.json"
    article_cache = work_dir / _ARTICLE_CACHE_NAME
    source_article = _build_source_article(segments)
    if not source_article:
        raise RuntimeError("英文转写为空，无法生成中文全文")
    source_digest = _article_digest(source_article)

    cached = load_json(cache)
    meta = load_json(meta_cache) or {}
    if (cached
            and meta.get("version") == _CACHE_VERSION
            and meta.get("engine") == engine
            and meta.get("rewrite_level", "high") == rewrite_level
            and meta.get("source_digest") == source_digest):
        log("translate", "复用已有全文翻译")
        return cached
    if cached:
        log("translate", "翻译算法、引擎或英文原稿已变化，重新生成全文翻译")

    log("translate", f"翻译引擎：{engine}")
    article_meta = {"ad_removed": False, "ad_notes": "", "quality_retry_count": 0}
    if engine == "google":
        out = _translate_google_segments(segments)
        zh_article = "".join(item["zh"] for item in out)
        segmentation_mode = "source_segments"
    else:
        if not config.DEEPSEEK_API_KEY:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置或改用 Google。")
        log("translate", f"AI 洗稿档位：{_REWRITE_LEVELS[rewrite_level]['name']}")
        log("translate", f"英文全文合并完成：{len(segments)} 段，{len(source_article)} 字符")
        zh_article, article_meta = _translate_deepseek_article(source_article, rewrite_level)
        sentences = _split_zh_article(zh_article)
        if not sentences:
            raise RuntimeError("中文全文重新分句后为空")
        out = _retime_article_segments(sentences, segments)
        segmentation_mode = "whole_article_retimed"
        log("translate", f"中文全文重分句：{len(sentences)} 段，并已生成连续预估时间")
        if article_meta["ad_removed"]:
            log("translate", f"全文广告清理：{article_meta['ad_notes'] or '已删除非正文推广内容'}")
        else:
            log("translate", "全文广告清理：未发现需删除的推广内容")

    save_json(article_cache, {
        "version": _CACHE_VERSION,
        "engine": engine,
        "rewrite_level": rewrite_level,
        "source_article_en": source_article,
        "article_zh": zh_article,
        **article_meta,
    })
    save_json(cache, out)
    save_json(meta_cache, {
        "version": _CACHE_VERSION,
        "engine": engine,
        "rewrite_level": rewrite_level,
        "source_count": len(segments),
        "source_digest": source_digest,
        "count": len(out),
        "article_cache": _ARTICLE_CACHE_NAME,
        "segmentation": {
            "mode": segmentation_mode,
            "source_count": len(segments),
            "output_count": len(out),
            "max_zh_segment_chars": int(
                getattr(config, "TRANSLATE_MAX_ZH_SEGMENT_CHARS", 42) or 0
            ),
            "timing": "estimated_before_tts; exact_in_dub_segments.json",
        },
        "advertising": article_meta,
    })
    log("translate", f"完成：{len(out)} 段")
    return out
