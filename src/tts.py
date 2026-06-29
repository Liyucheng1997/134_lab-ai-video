"""第 4 步：中文配音引擎调度器。

当前项目只保留 F5-TTS。

每个引擎模块都暴露相同接口：synthesize(segments, work_dir) / sample(text, out) / VOICES。
"""
from __future__ import annotations

import importlib
from pathlib import Path

from . import config

# 引擎 key -> (模块名, 显示名)
_ENGINES = {
    "f5": ("tts_f5", "F5-TTS（本地·音色克隆）"),
}
DEFAULT_ENGINE = "f5"


def _engine_key(engine: str | None = None) -> str:
    key = (engine or config.TTS_ENGINE or DEFAULT_ENGINE).strip()
    return key if key in _ENGINES else DEFAULT_ENGINE


def _engine_module(engine: str | None = None):
    mod_name = _ENGINES[_engine_key(engine)][0]
    return importlib.import_module(f".{mod_name}", __package__)


def synthesize(segments: list[dict], work_dir: Path, total_duration: float = 0.0
               ) -> tuple[Path, list[dict]]:
    return _engine_module().synthesize(segments, work_dir, total_duration)


def sample(text: str, out_path: Path, voice: str | None = None,
           engine: str | None = None) -> Path:
    """合成一小段试听音频。engine/voice 临时覆盖全局配置。"""
    if engine:
        config.TTS_ENGINE = engine
    if voice:
        config.TTS_VOICE = voice
    return _engine_module(engine).sample(text, out_path)


def _availability(key: str) -> tuple[bool, str]:
    """返回 (是否可用, 不可用时的提示)。只做轻量检查，不加载模型。"""
    import importlib.util as iu

    if key == "f5":
        if iu.find_spec("f5_tts") is None:
            return False, "未安装 f5-tts，请 pip install f5-tts"
        return True, ""
    return True, ""


def engines_info() -> list[dict]:
    """供前端展示：每个引擎的 key/名称/音色列表/是否可用/默认音色。"""
    info = []
    for key, (mod_name, name) in _ENGINES.items():
        try:
            mod = importlib.import_module(f".{mod_name}", __package__)
            voices = list(getattr(mod, "VOICES", {}).keys())
            default_voice = getattr(mod, "DEFAULT_VOICE", voices[0] if voices else "")
        except Exception:
            voices, default_voice = [], ""
        available, hint = _availability(key)
        info.append({
            "key": key, "name": name, "voices": voices,
            "default_voice": default_voice, "available": available, "hint": hint,
        })
    return info
