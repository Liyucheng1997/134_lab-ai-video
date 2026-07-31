"""荣格 / 纳瓦尔创作档案的统一默认配置。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from . import config

DEFAULT_PROFILE = "jung"

_NAVAL_IMAGE = config.BASE_DIR / "pictures" / "02_纳瓦尔.png"

_PROFILES = {
    "jung": {
        "key": "jung",
        "name": "荣格",
        "description": "英语源 · 人工老龙凤 · 原视频画面",
        "asr_language": "en",
        "translate_engine": "claude_code",
        "rewrite_level": "high",
        "max_zh_segment_chars": config.TRANSLATE_MAX_ZH_SEGMENT_CHARS,
        "tts_engine": "f5",
        "tts_voice": "人工老龙凤",
        "tts_speed": 1.0,
        "tts_parallel": 4,
        "compose_mode": "original",
        "canvas_width": 1920,
        "canvas_height": 1080,
        "image_title_enabled": False,
        "image_title": "",
        "image_title_x": 0.47,
        "image_title_y": 0.10,
        "image_title_font_size": 112,
        "image_title_width": 0.53,
        "subtitle_position": "bottom",
        "subtitle_fontsize": 70,
        "subtitle_marginv": 60,
        "subtitle_wrap_mode": "balanced",
        "subtitle_max_chars_per_line": 24,
        "douyin_cover_header": "",
        "image": config.DEFAULT_COVER_IMAGE,
    },
    "naval": {
        "key": "naval",
        "name": "纳瓦尔",
        "description": "中文源 · 大一嘉豪哥 · 9:16 竖屏图片",
        "asr_language": "zh",
        "translate_engine": "claude_code",
        "rewrite_level": "high",
        "max_zh_segment_chars": 30,
        "tts_engine": "f5",
        "tts_voice": "大一嘉豪哥",
        "tts_speed": 1.0,
        "tts_parallel": 4,
        "compose_mode": "image",
        "canvas_width": 1080,
        "canvas_height": 1920,
        "image_title_enabled": True,
        "image_title": "关注我，和纳瓦尔一起成长",
        "image_title_x": 0.10,
        "image_title_y": 0.13,
        "image_title_font_size": 110,
        "image_title_width": 0.84,
        "subtitle_position": "middle",
        "subtitle_fontsize": 64,
        "subtitle_marginv": 0,
        "subtitle_wrap_mode": "fixed",
        "subtitle_max_chars_per_line": 15,
        "douyin_cover_header": "纳瓦尔宝典",
        "image": _NAVAL_IMAGE,
    },
}


def normalize_profile(profile: str | None) -> str:
    key = str(profile or DEFAULT_PROFILE).strip().lower()
    return key if key in _PROFILES else DEFAULT_PROFILE


def get_profile(profile: str | None) -> dict:
    return deepcopy(_PROFILES[normalize_profile(profile)])


def apply_defaults(configs: dict | None, profile: str | None) -> dict:
    """把档案默认值填入六步配置；调用方明确提供的值优先。"""
    selected = get_profile(profile)
    profile_key = selected["key"]
    out = deepcopy(configs or {})
    for step in ("download", "asr", "translate", "tts", "compose", "publish"):
        out.setdefault(step, {})
        out[step].setdefault("profile", profile_key)

    out["asr"].setdefault("language", selected["asr_language"])
    out["translate"].setdefault("engine", selected["translate_engine"])
    out["translate"].setdefault("rewrite_level", selected["rewrite_level"])
    out["translate"].setdefault(
        "max_zh_segment_chars", selected["max_zh_segment_chars"]
    )
    out["tts"].setdefault("engine", selected["tts_engine"])
    out["tts"].setdefault("voice", selected["tts_voice"])
    out["tts"].setdefault("speed", selected["tts_speed"])
    out["tts"].setdefault("f5_parallel", selected["tts_parallel"])
    out["compose"].setdefault("mode", selected["compose_mode"])
    out["compose"].setdefault("canvas_width", selected["canvas_width"])
    out["compose"].setdefault("canvas_height", selected["canvas_height"])
    out["compose"].setdefault("image_title_enabled", selected["image_title_enabled"])
    out["compose"].setdefault("image_title", selected["image_title"])
    out["compose"].setdefault("image_title_x", selected["image_title_x"])
    out["compose"].setdefault("image_title_y", selected["image_title_y"])
    out["compose"].setdefault("image_title_font_size", selected["image_title_font_size"])
    out["compose"].setdefault("image_title_width", selected["image_title_width"])
    out["compose"].setdefault("position", selected["subtitle_position"])
    out["compose"].setdefault("fontsize", selected["subtitle_fontsize"])
    out["compose"].setdefault("subtitle_marginv", selected["subtitle_marginv"])
    out["compose"].setdefault("subtitle_wrap_mode", selected["subtitle_wrap_mode"])
    if selected["subtitle_max_chars_per_line"] is not None:
        out["compose"].setdefault(
            "subtitle_max_chars_per_line",
            selected["subtitle_max_chars_per_line"],
        )
    if out["compose"].get("mode") == "image":
        out["compose"].setdefault("image", str(selected["image"]))
    out["publish"].setdefault("cover_image", str(selected["image"]))
    out["publish"].setdefault("douyin_cover_header", selected["douyin_cover_header"])
    out["publish"].setdefault("archive_profile", profile_key)
    return out


def apply_step_defaults(step: str, cfg: dict | None) -> dict:
    current = dict(cfg or {})
    profile_key = normalize_profile(current.get("profile"))
    return apply_defaults({step: current}, profile_key)[step]


def public_profiles() -> list[dict]:
    return [
        {
            "key": item["key"],
            "name": item["name"],
            "description": item["description"],
            "asr_language": item["asr_language"],
            "translate_engine": item["translate_engine"],
            "rewrite_level": item["rewrite_level"],
            "max_zh_segment_chars": item["max_zh_segment_chars"],
            "tts_engine": item["tts_engine"],
            "tts_voice": item["tts_voice"],
            "tts_speed": item["tts_speed"],
            "tts_parallel": item["tts_parallel"],
            "compose_mode": item["compose_mode"],
            "canvas_width": item["canvas_width"],
            "canvas_height": item["canvas_height"],
            "image_title_enabled": item["image_title_enabled"],
            "image_title": item["image_title"],
            "image_title_x": item["image_title_x"],
            "image_title_y": item["image_title_y"],
            "image_title_font_size": item["image_title_font_size"],
            "image_title_width": item["image_title_width"],
            "subtitle_position": item["subtitle_position"],
            "subtitle_fontsize": item["subtitle_fontsize"],
            "subtitle_marginv": item["subtitle_marginv"],
            "subtitle_wrap_mode": item["subtitle_wrap_mode"],
            "subtitle_max_chars_per_line": item["subtitle_max_chars_per_line"],
            "image_available": Path(item["image"]).is_file(),
            "image_url": f"/api/profiles/{item['key']}/image.png",
        }
        for item in _PROFILES.values()
    ]


def image_path(profile: str | None) -> Path:
    return Path(_PROFILES[normalize_profile(profile)]["image"])
