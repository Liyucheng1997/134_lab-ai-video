"""第 4 步：用 F5-TTS（复用 114 听书软件）逐段合成中文配音，并按原时间戳对齐成整条音轨。

对齐策略（替换原声）：
- 每段在其原始 start 处落位；
- 可用时长 room = 下一段 start - 本段 start；
- 配音比 room 长 → 变速加快（保音高），最多 TTS_MAX_SPEEDUP 倍，仍超出则容忍少量重叠；
- 配音比 room 短 → 后面补静音。
最终得到与视频等长的单声道 24k 音轨。
"""
from __future__ import annotations

import glob
import hashlib
import os
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np
import soundfile as sf

from . import config
from .utils import log

_VOICE_CACHE = config.TOOLS_DIR / "voice_cache"
_VOICE_CACHE.mkdir(parents=True, exist_ok=True)

_model = None

# 内置音色：使用 f5_tts 自带的参考片段（F5 为中英双语模型，英文参考也能念好中文）。
_BUILTIN_VOICES = {
    "沉稳男声": ("multi/country.flac",
        "Six spoons of fresh snow peas, five thick slabs of blue cheese, and maybe a snack for her brother Bob."),
    "温柔女声": ("multi/main.flac",
        "Six spoons of fresh snow peas, five thick slabs of blue cheese, and maybe a snack for her brother Bob."),
    "浑厚男声": ("multi/town.flac",
        "The difference in the rainbow depends considerably upon the size of the drops, and the width of the "
        "coloured band increases as the size of the drops increases."),
}


# ----------------------------------------------------------- torchaudio 兼容补丁
def _patch_torchaudio_backend():
    """让 torchaudio.load/info 走 soundfile，绕开需要 ffmpeg 共享库的 torchcodec。"""
    try:
        import torch
        import torchaudio

        if getattr(torchaudio.load, "_patched", False):
            return

        def _load(path, *a, **k):
            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            return torch.from_numpy(data.T.copy()), sr

        _load._patched = True
        torchaudio.load = _load

        def _info(path, *a, **k):
            info = sf.info(str(path))
            return type("AudioInfo", (), {
                "sample_rate": info.samplerate,
                "num_frames": info.frames,
                "num_channels": info.channels,
            })()

        torchaudio.info = _info
    except Exception:
        pass


def _f5_examples_dir() -> str:
    import importlib.util
    spec = importlib.util.find_spec("f5_tts")
    base = os.path.dirname(spec.origin) if spec.origin else list(spec.submodule_search_locations)[0]
    return os.path.join(base, "infer", "examples")


def _resolve_voice() -> tuple[str, str]:
    """返回 (ref_audio_wav, ref_text)。支持自定义声音克隆。"""
    custom = os.environ.get("TTS_REF_AUDIO", "").strip()
    if custom and Path(custom).exists():
        ref_text = os.environ.get("TTS_REF_TEXT", "").strip()
        dest = _VOICE_CACHE / ("custom_" + hashlib.sha1(custom.encode()).hexdigest()[:8] + ".wav")
        if not dest.exists():
            data, sr = sf.read(custom, dtype="float32", always_2d=False)
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
            sf.write(dest, data, sr, subtype="PCM_16")
        return str(dest), ref_text

    name = config.TTS_VOICE if config.TTS_VOICE in _BUILTIN_VOICES else "温柔女声"
    rel, ref_text = _BUILTIN_VOICES[name]
    src = os.path.join(_f5_examples_dir(), rel)
    dest = _VOICE_CACHE / (name + ".wav")
    if not dest.exists():
        data, sr = sf.read(src, dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        sf.write(dest, data, sr, subtype="PCM_16")
    return str(dest), ref_text


def _get_model():
    global _model
    if _model is not None:
        return _model
    import torch
    _patch_torchaudio_backend()
    from f5_tts.api import F5TTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log("tts", f"加载 F5-TTS（{torch.cuda.get_device_name(0) if device == 'cuda' else 'CPU'}）")
    _model = F5TTS(model="F5TTS_v1_Base", device=device)
    return _model


def _synth_one(text: str, ref_audio: str, ref_text: str) -> tuple[np.ndarray, int]:
    model = _get_model()
    wav, sr, _ = model.infer(
        ref_file=ref_audio, ref_text=ref_text, gen_text=text,
        speed=1.0, nfe_step=32, cross_fade_duration=0.15,
        remove_silence=True, file_wave=None, show_info=lambda *a, **k: None,
    )
    wav = np.asarray(wav, dtype=np.float32)
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 0:
        wav = wav * (0.95 / peak)
    return wav, sr


def synthesize(segments: list[dict], work_dir: Path, total_duration: float = 0.0
               ) -> tuple[Path, list[dict]]:
    """顺序拼接中文配音，去掉原视频里的大段空白。

    不再对齐原始时间戳：每段配音首尾相接，段间只留一个很短的停顿（参考原始间隔但封顶）。
    返回 (dub.wav, 重新计时后的字幕段)。最终音轨通常比原视频短，视频会在配音结束处截断。
    """
    from .utils import load_json, save_json

    out_path = work_dir / "dub.wav"
    seg_cache = work_dir / "dub_segments.json"
    if out_path.exists() and seg_cache.exists():
        log("tts", "复用已有配音音轨")
        return out_path, load_json(seg_cache)

    ref_audio, ref_text = _resolve_voice()
    sr = 24000  # F5 输出采样率
    gap_min, gap_max = config.TTS_GAP_MIN, config.TTS_GAP_MAX

    clips: list[tuple[int, np.ndarray]] = []   # (起始采样点, 波形)
    retimed: list[dict] = []
    cursor = 0.0
    n = len(segments)

    for i, seg in enumerate(segments):
        text = seg.get("zh", "").strip()
        if not text:
            continue
        wav, wsr = _synth_one(text, ref_audio, ref_text)
        if wsr != sr:
            import librosa
            wav = librosa.resample(wav, orig_sr=wsr, target_sr=sr)
        dur = len(wav) / sr

        start = cursor
        clips.append((int(start * sr), wav))
        retimed.append({"start": round(start, 3), "end": round(start + dur, 3), "zh": text})
        cursor = start + dur

        # 段间停顿：参考原视频里这一段后的间隔，但限制在 [gap_min, gap_max]
        if i + 1 < n:
            orig_gap = segments[i + 1].get("start", seg["end"]) - seg.get("end", start + dur)
            cursor += min(max(orig_gap, gap_min), gap_max)
        log("tts", f"  [{i + 1}/{n}] -> {start:6.1f}s (+{dur:4.1f}s)  {text[:40]}")

    total_samples = int(cursor * sr) + sr // 2
    master = np.zeros(max(total_samples, 1), dtype=np.float32)
    for pos, wav in clips:
        end = min(pos + len(wav), total_samples)
        master[pos:end] += wav[:end - pos]

    peak = float(np.max(np.abs(master))) if master.size else 0.0
    if peak > 1.0:
        master = master / peak * 0.97
    sf.write(out_path, master, sr)
    save_json(seg_cache, retimed)
    log("tts", f"配音音轨完成：{out_path.name}（时长 {cursor:.0f}s，原视频更长的部分将被截断）")
    return out_path, retimed
