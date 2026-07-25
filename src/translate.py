"""第 3 步：把转写合成全文，再整体中译或中文洗稿、去广告并重建句段。

DeepSeek 不再承担原 ASR 句段与中文句段的一一对齐。它只接收一篇连续原稿，
返回一篇完整中文配音稿；本模块随后按中文语义分句，并生成供预览和 TTS 使用的
连续预估时间。第 4 步仍会按实际合成音频重新精确计时。
"""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import requests

from . import config
from .utils import load_json, log, save_json

_CACHE_VERSION = "whole-article-v9-enumeration-punctuation"
_ARTICLE_CACHE_NAME = "translation.article.json"
_BRIDGE_CACHE_VERSION = "zh-en-semantic-bridge-v1"
_BRIDGE_CACHE_NAME = "translation.bridge.json"
_SENTENCE_ENDINGS = "。！？!?."
_SOFT_SPLIT_PUNCT = "，,、；;：:"
_CLOSING_QUOTES = "”’\"」』）》】"
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LONG_ENGLISH_PHRASE_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Za-z][A-Za-z'’-]*[ \t]+){3,}[A-Za-z][A-Za-z'’-]*(?![A-Za-z])"
)
_NAVAL_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"naval(?:[ \t]+ravikant)?(?P<possessive>['’]s)?"
    r"(?![A-Za-z0-9_])",
    flags=re.IGNORECASE,
)
_NAVAL_NAME_INSTRUCTION = (
    "术语硬规则：人物姓名 Naval 或 Naval Ravikant 一律译写为“纳瓦尔”，"
    "最终中文稿不得保留该人物的英文姓名。"
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

_CHINESE_REWRITE_INSTRUCTIONS = {
    "low": (
        "轻度中文改写：修正转写错误和不通顺表达，并在不改变信息顺序的前提下改造句式。"
        "不能只改标点或删语气词，成稿不能与原稿逐字相同。"
    ),
    "medium": (
        "中度中文改写：先理解每段要表达的事实，再重新组织句式、语序和衔接。"
        "可以合并重复内容、拆分长句，并调整不影响因果关系的信息顺序。"
        "不新增观点、案例或结论，也不要沿着原稿逐句替换同义词。"
    ),
    "high": (
        "中文深度洗稿：目标不是校订原稿，而是基于同一组事实另写一篇口播稿。"
        "读完后只保留人物、动作、原因、转折和结论等事实卡片，再从事实卡片重新落笔。"
        "必须重新选择开场，重排不影响因果关系的材料，并重造主要句子的主语、谓语和信息顺序。"
        "不要沿原稿从第一句到最后一句逐句推进，不要求与原稿逐句对应。"
        "允许合并、拆分和压缩重复说明，但核心观点、关键事实、人物、数字、案例和因果关系不能改变。"
        "大多数句子应同时改变观察角度或主语、谓语表达以及信息出现的顺序，"
        "不能只替换几个同义词。最终稿要像另一位中文作者独立讲述，顺口、清楚、适合配音。"
    ),
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

_CHINESE_TO_ENGLISH_BRIDGE_SYS_PROMPT = (
    "You are a bilingual semantic translation editor. The user provides a complete "
    "Chinese speech transcript. Translate it into one coherent, natural English "
    "semantic manuscript. This English text is an intermediate representation for "
    "a later Chinese rewrite, not a publishable script. Preserve speaker attribution, "
    "people, names, numbers, examples, conditions, causal links, contrasts, emotions, "
    "and conclusions. Repair obvious ASR fragmentation and self-corrections, but do "
    "not summarize, invent, or exaggerate. Remove unrelated sponsorships, purchase or "
    "download prompts, subscription calls, URLs, and channel promotion while keeping "
    "brands that are part of the actual discussion. Do not include any Chinese source "
    "sentences or a bilingual comparison. Return JSON only: "
    "{\"article_en\":\"complete English semantic manuscript\","
    "\"ad_removed\":true/false,\"ad_notes\":\"brief note or empty\"}."
)

_ENGLISH_BRIDGE_TO_CHINESE_SYS_PROMPT = (
    "你是中文视频口播稿创作者。用户提供的是由中文原稿转换而来的完整英文语义稿，"
    "你看不到中文原稿，也不能猜测或复原原稿措辞。先理解英文中的人物、事实、数字、"
    "案例、观点归属和因果关系，再像另一位中文作者一样从零写成自然、连贯的中文口播稿。"
    "这不是逐句回译。必须重新选择开场、主谓结构、句子边界、段落衔接和收束方式，"
    "允许重排不影响因果关系的信息，但不能漏掉关键事实、改变说话人归属、添加观点或夸张原意。"
    "不要输出英文、原文对照、解释过程或事实清单。不要补写赞助、购买、关注订阅等引流内容。"
    "只输出 JSON 对象："
    "{\"article_zh\":\"完整中文口播稿\",\"ad_removed\":true/false,"
    "\"ad_notes\":\"简短说明，没有则留空\"}。"
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


def _normalize_source_language(language: str | None, source_article: str = "") -> str:
    """把 Whisper/用户语言代码归一化；旧缓存无语言时按文本自动识别中文。"""
    value = (language or "").strip().lower().replace("_", "-")
    if value in {"zh", "cmn", "yue"} or value.startswith("zh-"):
        return "zh"
    if value:
        return value
    visible = len(re.sub(r"\s+", "", source_article or ""))
    cjk = len(_CJK_RE.findall(source_article or ""))
    return "zh" if visible and cjk / visible >= 0.5 else "en"


def _normalize_required_chinese_terms(
    article: str,
    *,
    force_naval_name: bool = False,
) -> str:
    if not force_naval_name:
        return article
    return _NAVAL_NAME_RE.sub(
        lambda match: "纳瓦尔的" if match.group("possessive") else "纳瓦尔",
        article,
    )


def _rewrite_instruction(level: str, is_chinese: bool) -> str:
    if is_chinese:
        return _CHINESE_REWRITE_INSTRUCTIONS[level]
    return _REWRITE_LEVELS[level]["instruction"]


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
    """按原顺序把 ASR 碎片合成一篇连续原稿，不把时间轴交给模型。"""
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


def _call_deepseek_english_bridge(source_article: str) -> dict:
    payload = {
        "task": "translate_chinese_article_to_english_semantic_bridge",
        "source_language": "zh",
        "target_language": "en",
        "source_article_zh": source_article,
        "requirements": [
            "preserve_complete_meaning_and_speaker_attribution",
            "repair_asr_fragmentation",
            "remove_unrelated_advertising",
            "write_natural_english_not_word_for_word_gloss",
        ],
    }
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
                    "content": _CHINESE_TO_ENGLISH_BRIDGE_SYS_PROMPT,
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.25,
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
            "DeepSeek 英文中间稿达到输出上限，请提高 "
            "DEEPSEEK_MAX_OUTPUT_TOKENS 或缩短原视频"
        )
    return _parse_json_object(choice["message"]["content"])


def _extract_english_bridge(
    data: dict,
    source_effective_length: int = 0,
) -> str:
    for key in ("article_en", "english_article", "content"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        article = re.sub(r"\s+", " ", value).strip()
        latin_count = len(re.findall(r"[A-Za-z]", article))
        cjk_count = len(_CJK_RE.findall(article))
        visible_count = len(re.sub(r"\s+", "", article))
        if (
            visible_count >= 10
            and not cjk_count
            and latin_count >= visible_count * 0.45
        ):
            if (
                source_effective_length >= 200
                and visible_count < source_effective_length * 0.45
            ):
                raise ValueError(
                    "DeepSeek 英文中间稿过短，疑似把完整原稿压缩成了摘要"
                )
            return article
    raise ValueError("DeepSeek 未返回可用的完整英文中间稿")


def _call_deepseek_chinese_from_english(
    english_bridge: str,
    *,
    rewrite_level: str,
    source_effective_length: int,
    retry_note: str = "",
    retry_attempt: int = 0,
    force_naval_name: bool = False,
) -> dict:
    rewrite_level = _normalize_rewrite_level(rewrite_level)
    payload = {
        "task": "rewrite_chinese_article_from_english_bridge",
        "source_language": "en",
        "origin_language": "zh",
        "target_language": "zh",
        "rewrite_level": rewrite_level,
        "rewrite_strategy": "english_semantic_bridge_reconstruction",
        "source_article_en": english_bridge,
        "source_length_chars": source_effective_length,
        "target_length_chars": [
            int(source_effective_length * 0.75),
            int(source_effective_length * 1.05),
        ],
    }
    if force_naval_name:
        payload["required_term_translations"] = {"Naval": "纳瓦尔"}
    if retry_note:
        payload["quality_retry"] = retry_note
        payload["retry_mode"] = (
            "new_narrative_angle"
            if retry_attempt >= 2
            else "forced_structural_rewrite"
        )
    retry_instruction = ""
    if retry_note:
        retry_instruction = (
            "上一次中文稿未通过原创度质检。当前仍只能依据英文语义稿重新创作，"
            "不要尝试复原中文原句。必须更换开场、信息焦点、主谓结构和段落组织，"
            "同时完整保留英文稿中的事实和观点归属。"
        )
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
                        _ENGLISH_BRIDGE_TO_CHINESE_SYS_PROMPT
                        + _rewrite_instruction(rewrite_level, True)
                        + "请先完成整篇文章的宏观重构，竖屏字幕长度会在生成后由程序统一切分。"
                        + (_NAVAL_NAME_INSTRUCTION if force_naval_name else "")
                        + retry_instruction
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": (
                1.05 if retry_attempt >= 2
                else 0.95 if retry_note
                else 0.85 if rewrite_level == "high"
                else 0.50
            ),
            "max_tokens": _max_output_tokens(english_bridge),
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
            "DeepSeek 中文回译稿达到输出上限，请提高 "
            "DEEPSEEK_MAX_OUTPUT_TOKENS 或缩短原视频"
        )
    return _parse_json_object(choice["message"]["content"])


def _translate_chinese_via_english(
    source_article: str,
    rewrite_level: str,
    bridge_cache_path: Path | None = None,
    force_naval_name: bool = False,
) -> tuple[str, dict]:
    source_digest = _article_digest(source_article)
    source_effective_length = len(_normalize_for_comparison(source_article))
    bridge_data = load_json(bridge_cache_path) if bridge_cache_path else None
    bridge_cache_hit = False
    if (
        isinstance(bridge_data, dict)
        and bridge_data.get("version") == _BRIDGE_CACHE_VERSION
        and bridge_data.get("source_digest") == source_digest
        and bridge_data.get("model") == config.DEEPSEEK_MODEL
    ):
        try:
            english_bridge = _extract_english_bridge(
                bridge_data,
                source_effective_length,
            )
            bridge_cache_hit = True
            log("translate", "复用已有英文语义中间稿")
        except ValueError:
            bridge_data = None
    if not bridge_cache_hit:
        bridge_response = _call_deepseek_english_bridge(source_article)
        english_bridge = _extract_english_bridge(
            bridge_response,
            source_effective_length,
        )
        bridge_data = {
            "version": _BRIDGE_CACHE_VERSION,
            "model": config.DEEPSEEK_MODEL,
            "source_language": "zh",
            "target_language": "en",
            "source_digest": source_digest,
            "article_en": english_bridge,
            "bridge_digest": _article_digest(english_bridge),
            "ad_removed": bool(bridge_response.get("ad_removed", False)),
            "ad_notes": str(bridge_response.get("ad_notes") or "").strip(),
        }
        if bridge_cache_path:
            save_json(bridge_cache_path, bridge_data)
            log("translate", "英文语义中间稿已缓存")
    normalized_level = _normalize_rewrite_level(rewrite_level)
    similarity_limit = {
        "low": 0.97,
        "medium": 0.90,
        "high": 0.82,
    }[normalized_level]
    last_issue = ""
    for attempt in range(3):
        retry_note = (
            f"上一次中文稿未通过质检：{last_issue}。"
            "请只依据英文语义稿重新生成完整中文口播稿。"
            if attempt else ""
        )
        data = _call_deepseek_chinese_from_english(
            english_bridge,
            rewrite_level=normalized_level,
            source_effective_length=source_effective_length,
            retry_note=retry_note,
            retry_attempt=attempt,
            force_naval_name=force_naval_name,
        )
        article = _normalize_required_chinese_terms(
            _extract_chinese_article(data),
            force_naval_name=force_naval_name,
        )
        if normalized_level == "high":
            article = _normalize_deep_polish_punctuation(article)
        last_issue = _article_quality_issue(
            article,
            source_article=source_article,
            similarity_limit=similarity_limit,
            deep_rewrite_checks=normalized_level == "high",
            length_ratio_bounds=(0.70, 1.10),
        )
        if not last_issue:
            return article, {
                "ad_removed": bool(
                    bridge_data.get("ad_removed", False)
                    or data.get("ad_removed", False)
                ),
                "ad_notes": str(
                    bridge_data.get("ad_notes")
                    or data.get("ad_notes")
                    or ""
                ).strip(),
                "quality_retry_count": attempt,
                "rewrite_route": "zh-en-zh",
                "bridge_digest": _article_digest(english_bridge),
                "bridge_cache_hit": bridge_cache_hit,
            }
        log("translate", f"英文中间稿回译质检未通过，整篇重试：{last_issue}")
    raise RuntimeError(
        f"DeepSeek 英文中间稿回译连续三次未通过质检：{last_issue}"
    )


def _call_deepseek_article(source_article: str, *, rewrite_level: str,
                           retry_note: str = "",
                           max_zh_segment_chars: int = 0,
                           force_naval_name: bool = False) -> dict:
    rewrite_level = _normalize_rewrite_level(rewrite_level)
    rewrite = _REWRITE_LEVELS[rewrite_level]
    payload = {
        "task": "translate_complete_article_to_clean_chinese_voiceover",
        "rewrite_level": rewrite_level,
        "rewrite_instruction": rewrite["instruction"],
        "source_language": "en",
        "target_segment_chars": max_zh_segment_chars,
        "rewrite_strategy": "meaning_first_translation",
        "source_article_en": source_article,
    }
    if force_naval_name:
        payload["required_term_translations"] = {"Naval": "纳瓦尔"}
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
                        + (_NAVAL_NAME_INSTRUCTION if force_naval_name else "")
                        + (
                            f"本次成稿用于竖屏视频，每个独立口播短句尽量控制在"
                            f"{max_zh_segment_chars}个中文字以内，优先在语义和标点完整处断句。"
                            if 0 < max_zh_segment_chars <= 30
                            else ""
                        )
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


def _normalize_for_comparison(text: str) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]+", "", text or "")


def _source_ngram_coverage(source: str, article: str, ngram_size: int = 12) -> float:
    """输出中落在原稿连续片段里的字符占比；不受段落换序影响。"""
    if len(source) < ngram_size or len(article) < ngram_size:
        return 0.0
    source_ngrams = {
        source[index:index + ngram_size]
        for index in range(len(source) - ngram_size + 1)
    }
    covered = bytearray(len(article))
    for index in range(len(article) - ngram_size + 1):
        if article[index:index + ngram_size] not in source_ngrams:
            continue
        covered[index:index + ngram_size] = b"\x01" * ngram_size
    return sum(covered) / len(article)


def _article_quality_issue(article: str, *, source_article: str = "",
                           similarity_limit: float | None = None,
                           deep_rewrite_checks: bool = False,
                           length_ratio_bounds: tuple[float, float] = (
                               0.68, 0.95
                           )) -> str:
    cjk_count = len(_CJK_RE.findall(article))
    visible_count = len(re.sub(r"\s+", "", article))
    if visible_count < 2 or cjk_count < max(2, int(visible_count * 0.25)):
        return "返回内容不像完整中文译稿"
    match = _LONG_ENGLISH_PHRASE_RE.search(article)
    if match:
        return f"仍包含疑似未翻译英文：{match.group(0)[:80]}"
    if source_article and similarity_limit is not None:
        source_clean = _normalize_for_comparison(source_article)
        article_clean = _normalize_for_comparison(article)
        if len(source_clean) >= 20 and len(article_clean) >= 20:
            matcher = SequenceMatcher(
                None, source_clean, article_clean, autojunk=False
            )
            if deep_rewrite_checks and len(article_clean) >= 36:
                copied_coverage = _source_ngram_coverage(
                    source_clean, article_clean, ngram_size=12
                )
                if copied_coverage >= 0.40:
                    return (
                        "改写结果仍在大量照搬原句"
                        f"（12字连续片段照搬率 {copied_coverage:.0%}），"
                        "需要脱离原稿措辞重写"
                    )
                longest_copy = matcher.find_longest_match().size
                if longest_copy >= 36:
                    return (
                        f"改写结果连续照搬原稿 {longest_copy} 字，"
                        "需要重写对应段落"
                    )
                if len(source_clean) >= 200:
                    length_ratio = len(article_clean) / len(source_clean)
                    minimum_ratio, maximum_ratio = length_ratio_bounds
                    if not minimum_ratio <= length_ratio <= maximum_ratio:
                        return (
                            "改写结果长度比例不合格"
                            f"（当前为原稿的 {length_ratio:.0%}，"
                            f"目标为 {minimum_ratio:.0%} 至 "
                            f"{maximum_ratio:.0%}）"
                        )
            similarity = matcher.ratio()
            if similarity >= similarity_limit:
                return (
                    "改写结果与中文原稿过于相似"
                    f"（相似度 {similarity:.0%}），需要重构表达"
                )
    return ""


def _translate_deepseek_article(source_article: str,
                                rewrite_level: str,
                                source_language: str = "en",
                                max_zh_segment_chars: int = 0,
                                bridge_cache_path: Path | None = None,
                                force_naval_name: bool = False) -> tuple[str, dict]:
    """全文中译；中文源稿改走英文语义中间稿后，不进入此英文直译循环。"""
    source_language = _normalize_source_language(source_language, source_article)
    normalized_level = _normalize_rewrite_level(rewrite_level)
    if source_language == "zh":
        log("translate", "中文洗稿路线：中文 → 英文语义稿 → 全新中文口播稿")
        return _translate_chinese_via_english(
            source_article,
            normalized_level,
            bridge_cache_path=bridge_cache_path,
            force_naval_name=force_naval_name,
        )
    last_issue = ""
    for attempt in range(2):
        data = _call_deepseek_article(
            source_article,
            rewrite_level=rewrite_level,
            max_zh_segment_chars=max_zh_segment_chars,
            force_naval_name=force_naval_name,
            retry_note=(
                f"上一次结果未通过质检：{last_issue}。请重新翻译完整原稿，"
                "不要只修补局部，并严格清除未翻译英文和广告。"
                if attempt else ""
            ),
        )
        article = _normalize_required_chinese_terms(
            _extract_chinese_article(data),
            force_naval_name=force_naval_name,
        )
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
    raise RuntimeError(
        f"DeepSeek 连续两次未返回合格中文全文：{last_issue}"
    )


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
    """优先在中文标点处分段，没有合适标点时均衡硬切，保证不超过上限。"""
    remaining = sentence.strip()
    if max_chars <= 0 or _text_size(remaining) <= max_chars:
        return [remaining]
    parts: list[str] = []
    break_punctuation = _SOFT_SPLIT_PUNCT + _SENTENCE_ENDINGS
    while _text_size(remaining) > max_chars:
        total_size = _text_size(remaining)
        part_count = (total_size + max_chars - 1) // max_chars
        target_size = (total_size + part_count - 1) // part_count
        # 允许为了自然标点多分一段，避免把“如何不靠运气”之类短语从中间切开。
        min_size = max(4, target_size // 2)
        visible_size = 0
        target_index = 0
        candidates: list[tuple[int, int]] = []
        for index, char in enumerate(remaining):
            if not char.isspace():
                visible_size += 1
            if not target_index and visible_size >= target_size:
                target_index = index + 1
            if (
                char in break_punctuation
                and min_size <= visible_size <= max_chars
            ):
                candidates.append((index + 1, visible_size))
            if visible_size >= max_chars:
                break
        if candidates:
            cut_index, _ = min(
                candidates,
                key=lambda item: abs(item[1] - target_size),
            )
        else:
            cut_index = target_index
        if cut_index <= 0:
            break
        piece = remaining[:cut_index].strip()
        if piece:
            parts.append(piece)
        remaining = remaining[cut_index:].strip()
    if remaining:
        parts.append(remaining)
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
    """按中文信息量生成连续预估时间，并仅为审阅近似挂接原文片段。"""
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
              batch_size: int = 25, rewrite_level: str = "high",
              source_language: str | None = None,
              max_zh_segment_chars: int | None = None,
              profile: str | None = None) -> list[dict]:
    """生成 translated.json；batch_size 仅为兼容旧调用，全文模式不会分批。"""
    del batch_size
    rewrite_level = _normalize_rewrite_level(rewrite_level)
    work_dir.mkdir(parents=True, exist_ok=True)
    cache = work_dir / "translated.json"
    meta_cache = work_dir / "translated.meta.json"
    article_cache = work_dir / _ARTICLE_CACHE_NAME
    source_article = _build_source_article(segments)
    if not source_article:
        raise RuntimeError("语音转写为空，无法生成中文全文")
    source_language = _normalize_source_language(source_language, source_article)
    profile_key = str(profile or "").strip().lower()
    force_naval_name = profile_key == "naval"
    try:
        max_zh_segment_chars = int(
            config.TRANSLATE_MAX_ZH_SEGMENT_CHARS
            if max_zh_segment_chars is None
            else max_zh_segment_chars
        )
    except (TypeError, ValueError):
        max_zh_segment_chars = int(config.TRANSLATE_MAX_ZH_SEGMENT_CHARS)
    max_zh_segment_chars = max(0, min(120, max_zh_segment_chars))
    source_digest = _article_digest(source_article)

    cached = load_json(cache)
    meta = load_json(meta_cache) or {}
    if (cached
            and meta.get("version") == _CACHE_VERSION
            and meta.get("engine") == engine
            and meta.get("rewrite_level", "high") == rewrite_level
            and meta.get("source_language", "en") == source_language
            and meta.get("profile", "") == profile_key
            and int(meta.get(
                "max_zh_segment_chars",
                config.TRANSLATE_MAX_ZH_SEGMENT_CHARS,
            )) == max_zh_segment_chars
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
        source_name = "中文" if source_language == "zh" else source_language.upper()
        action_name = "深度洗稿" if source_language == "zh" else "中译洗稿"
        log("translate", f"源语言：{source_name}，处理方式：{action_name}")
        log("translate", f"原稿全文合并完成：{len(segments)} 段，{len(source_article)} 字符")
        zh_article, article_meta = _translate_deepseek_article(
            source_article,
            rewrite_level,
            source_language,
            max_zh_segment_chars,
            bridge_cache_path=(
                work_dir / _BRIDGE_CACHE_NAME
                if source_language == "zh"
                else None
            ),
            force_naval_name=force_naval_name,
        )
        sentences = _split_zh_article(
            zh_article,
            max_chars=max_zh_segment_chars,
        )
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
        "source_language": source_language,
        "profile": profile_key,
        "max_zh_segment_chars": max_zh_segment_chars,
        ("source_article_zh" if source_language == "zh" else "source_article_en"):
            source_article,
        "article_zh": zh_article,
        **article_meta,
    })
    save_json(cache, out)
    save_json(meta_cache, {
        "version": _CACHE_VERSION,
        "engine": engine,
        "rewrite_level": rewrite_level,
        "source_language": source_language,
        "profile": profile_key,
        "max_zh_segment_chars": max_zh_segment_chars,
        "source_count": len(segments),
        "source_digest": source_digest,
        "count": len(out),
        "article_cache": _ARTICLE_CACHE_NAME,
        "segmentation": {
            "mode": segmentation_mode,
            "source_count": len(segments),
            "output_count": len(out),
            "max_zh_segment_chars": max_zh_segment_chars,
            "timing": "estimated_before_tts; exact_in_dub_segments.json",
        },
        "advertising": article_meta,
    })
    log("translate", f"完成：{len(out)} 段")
    return out
