"""第 2 步：用 faster-whisper 把音频转成带时间戳的句子段。

输出 segments：[{"start": float, "end": float, "text": str}, ...]
"""
from __future__ import annotations

from pathlib import Path

from . import config
from .utils import load_json, log, save_json

_model = None


def _get_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel

    log("asr", f"加载 faster-whisper：{config.WHISPER_MODEL} ({config.WHISPER_DEVICE})")
    try:
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
        )
    except Exception as e:  # GPU 不行就退回 CPU
        log("asr", f"GPU 加载失败（{e}），改用 CPU int8")
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(audio: Path, work_dir: Path, language: str | None = None) -> list[dict]:
    """转写音频，返回句段列表。结果缓存到 segments.json，可断点续跑。"""
    cache = work_dir / "segments.json"
    cached = load_json(cache)
    if cached:
        log("asr", f"复用已有转写：{len(cached['segments'])} 段")
        return cached["segments"]

    model = _get_model()
    log("asr", "转写中（首次会稍慢）…")
    seg_iter, info = model.transcribe(
        str(audio),
        language=language,                 # None = 自动检测
        vad_filter=True,                   # 去静音，断句更干净
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
    )
    log("asr", f"检测语言：{info.language} (p={info.language_probability:.2f})")

    segments = []
    for s in seg_iter:
        text = s.text.strip()
        if not text:
            continue
        segments.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": text})
        log("asr", f"  [{s.start:6.1f}-{s.end:6.1f}] {text[:60]}")

    save_json(cache, {"language": info.language, "segments": segments})
    log("asr", f"完成：{len(segments)} 段")
    return segments
