"""第 4 步：中文配音引擎调度器。

按 config.TTS_ENGINE 选择具体引擎实现：
- qwen3     Qwen3-TTS（本地·稳定）
- f5        F5-TTS（本地·音色克隆）
- azure     Azure 神经网络语音（云端·最自然，需 KEY）
- cosyvoice CosyVoice2（阿里·开源本地，情感指令）

每个引擎模块都暴露相同接口：synthesize(segments, work_dir) / sample(text, out) / VOICES。
"""
from __future__ import annotations

import importlib
from pathlib import Path

from . import config

# 引擎 key -> (模块名, 显示名)
_ENGINES = {
    "qwen3": ("tts_qwen", "Qwen3-TTS（本地·稳定）"),
    "f5": ("tts_f5", "F5-TTS（本地·音色克隆）"),
    "azure": ("tts_azure", "Azure TTS（云端·最自然）"),
    "gemini": ("tts_gemini", "Gemini TTS（云端·可控情感）"),
    "cosyvoice": ("tts_cosyvoice", "CosyVoice2（本地·情感指令）"),
}
DEFAULT_ENGINE = "qwen3"


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
    from pathlib import Path

    if key == "azure":
        if not config.AZURE_SPEECH_KEY:
            return False, "需在 .env 填 AZURE_SPEECH_KEY 与 AZURE_SPEECH_REGION"
        return True, ""
    if key == "gemini":
        if not config.GEMINI_API_KEY:
            return False, "需在 .env 填 GEMINI_API_KEY（或 GOOGLE_API_KEY）"
        return True, ""
    if key == "qwen3":
        if iu.find_spec("qwen_tts") is None:
            return False, "未安装 qwen_tts"
        return True, ""
    if key == "f5":
        if iu.find_spec("f5_tts") is None:
            return False, "未安装 f5-tts，请 pip install f5-tts"
        return True, ""
    if key == "cosyvoice":
        if not Path(config.COSYVOICE_REPO_DIR).exists():
            return False, "未找到 CosyVoice 仓库，请 clone 并设置 COSYVOICE_REPO_DIR"
        if not Path(config.COSYVOICE_MODEL_DIR).exists():
            return False, "未下载 CosyVoice2-0.5B，请设置 COSYVOICE_MODEL_DIR"
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
