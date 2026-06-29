"""TTS 引擎：F5-TTS（本地·音色克隆，复用 114 听书软件）。

逐段合成中文配音并顺序拼接：每段配音首尾相接，段间只留一个很短的停顿
（参考原始间隔但封顶）。F5 为中英双语模型，内置英文参考片段也能念好中文；
也可通过 config.F5_REF_AUDIO / F5_REF_TEXT 提供自定义参考音频做声音克隆。
"""
from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np
import soundfile as sf

from . import config
from .utils import log, run, save_json

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

_CUSTOM_VOICES = {
    "人工老龙凤": (
        _VOICE_CACHE / "人工老龙凤.wav",
        "当你和一个真正觉醒的人相处时，会发生一些难以言语的事。"
        "在你能够解释之前，在任何对话开始之前，在任何有意义的东西被交换之前。",
    ),
    "大一嘉豪哥": (
        _VOICE_CACHE / "大一嘉豪哥.wav",
        "很多人以为改变人生要靠一次巨大的决定。要靠搬到新的城市，"
        "换一份新的工作，遇见一个贵人，或者突然拥有一大笔钱。可我越来越确信。",
    ),
}

VOICES = {k: k for k in [*_BUILTIN_VOICES, *_CUSTOM_VOICES]}
DEFAULT_VOICE = "人工老龙凤"
_FALLBACK_BUILTIN_VOICE = "温柔女声"


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
    custom = (config.F5_REF_AUDIO or "").strip()
    if custom and Path(custom).exists():
        ref_text = (config.F5_REF_TEXT or "").strip()
        dest = _VOICE_CACHE / ("custom_" + hashlib.sha1(custom.encode()).hexdigest()[:8] + ".wav")
        if not dest.exists():
            data, sr = sf.read(custom, dtype="float32", always_2d=False)
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
            sf.write(dest, data, sr, subtype="PCM_16")
        return str(dest), ref_text

    name = config.TTS_VOICE
    if name in _CUSTOM_VOICES:
        ref_audio, ref_text = _CUSTOM_VOICES[name]
        if Path(ref_audio).exists():
            return str(ref_audio), ref_text
        log("tts", f"自定义 F5 音色缺少参考音频：{ref_audio}，改用默认音色")

    name = name if name in _BUILTIN_VOICES else _FALLBACK_BUILTIN_VOICE
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
        remove_silence=True, file_wave=None, show_info=lambda *a, **k: None, progress=None,
    )
    wav = np.asarray(wav, dtype=np.float32)
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 0:
        wav = wav * (0.95 / peak)
    return wav, sr


def sample(text: str, out_path: Path) -> Path:
    ref_audio, ref_text = _resolve_voice()
    wav, sr = _synth_one(text[:120] or "你好，这是配音音色试听。", ref_audio, ref_text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, wav, sr)
    log("tts", f"试听音频已生成：{out_path.name}")
    return out_path


def _synth_indexed(item: tuple[int, dict, str], ref_audio: str, ref_text: str,
                   target_sr: int) -> tuple[int, dict, str, np.ndarray, float]:
    i, seg, text = item
    started = time.perf_counter()
    wav, wsr = _synth_one(text, ref_audio, ref_text)
    if wsr != target_sr:
        import librosa
        wav = librosa.resample(wav, orig_sr=wsr, target_sr=target_sr)
    return i, seg, text, wav, time.perf_counter() - started


def _atempo_filter(speed: float) -> str:
    speed = max(0.5, min(2.0, float(speed or 1.0)))
    parts: list[float] = []
    while speed > 2.0:
        parts.append(2.0)
        speed /= 2.0
    while speed < 0.5:
        parts.append(0.5)
        speed /= 0.5
    parts.append(speed)
    return ",".join(f"atempo={x:.6g}" for x in parts)


def _apply_speed(raw_wav: Path, raw_segments: list[dict], out_wav: Path,
                 out_segments: Path, speed: float) -> list[dict]:
    speed = max(0.5, min(1.6, float(speed or 1.0)))
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if abs(speed - 1.0) < 1e-3:
        if raw_wav.resolve() != out_wav.resolve():
            os.replace(raw_wav, out_wav)
    else:
        run([
            config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw_wav),
            "-filter:a", _atempo_filter(speed),
            str(out_wav),
        ], desc=f"应用配音倍速 {speed:.2f}x")

    adjusted = []
    for seg in raw_segments:
        adjusted.append({
            **seg,
            "start": round(float(seg.get("start", 0)) / speed, 3),
            "end": round(float(seg.get("end", 0)) / speed, 3),
        })
    save_json(out_segments, adjusted)
    save_json(out_wav.with_name("dub_speed.json"), {"speed": speed})
    log("tts", f"已应用后期倍速 {speed:.2f}x，供第 5 步合成使用")
    return adjusted


def synthesize(segments: list[dict], work_dir: Path, total_duration: float = 0.0
               ) -> tuple[Path, list[dict]]:
    """顺序拼接中文配音，去掉原视频里的大段空白。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / "dub.wav"
    seg_cache = work_dir / "dub_segments.json"

    ref_audio, ref_text = _resolve_voice()
    sr = 24000  # F5 输出采样率
    gap_min, gap_max = config.TTS_GAP_MIN, config.TTS_GAP_MAX
    speed = max(0.5, min(1.6, float(config.TTS_SPEED or 1.0)))

    clips: list[tuple[int, np.ndarray]] = []   # (起始采样点, 波形)
    retimed: list[dict] = []
    cursor = 0.0
    n = len(segments)
    jobs = [(i, seg, (seg.get("zh") or "").strip())
            for i, seg in enumerate(segments) if (seg.get("zh") or "").strip()]
    parallel = max(1, min(int(getattr(config, "F5_TTS_PARALLEL", 1)), len(jobs) or 1))
    log("tts", f"使用 F5-TTS / {config.TTS_VOICE} 原速合成配音，共 {n} 段，并发 {parallel}")

    # 先并发生成各段音频，再按原段落顺序拼接，保证字幕时间线稳定。
    generated: dict[int, tuple[dict, str, np.ndarray]] = {}
    if parallel <= 1:
        for done, item in enumerate(jobs, 1):
            i, seg, text, wav, used = _synth_indexed(item, ref_audio, ref_text, sr)
            generated[i] = (seg, text, wav)
            log("tts", f"  [{done}/{len(jobs)}] 段 {i + 1} 完成 {used:4.1f}s  {text[:40]}")
    else:
        _get_model()
        with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="f5tts") as pool:
            futures = [pool.submit(_synth_indexed, item, ref_audio, ref_text, sr) for item in jobs]
            for done, fut in enumerate(as_completed(futures), 1):
                i, seg, text, wav, used = fut.result()
                generated[i] = (seg, text, wav)
                log("tts", f"  [{done}/{len(jobs)}] 段 {i + 1} 完成 {used:4.1f}s  {text[:40]}")

    for i, seg in enumerate(segments):
        item = generated.get(i)
        if not item:
            continue
        seg, text, wav = item
        dur = len(wav) / sr

        start = cursor
        clips.append((int(start * sr), wav))
        retimed.append({"start": round(start, 3), "end": round(start + dur, 3),
                        "zh": text, "text": (seg.get("text") or "").strip()})
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
    raw_path = out_path if abs(speed - 1.0) < 1e-3 else (
        work_dir / f".dub_tmp_{os.getpid()}_{int(time.time() * 1000)}.wav")
    try:
        sf.write(raw_path, master, sr)
        log("tts", f"原速推理完成，正在输出 {speed:.2f}x 最终音轨")
        adjusted = _apply_speed(raw_path, retimed, out_path, seg_cache, speed)
    finally:
        if raw_path != out_path:
            raw_path.unlink(missing_ok=True)
    log("tts", f"配音音轨完成：{out_path.name}（第 5 步将使用当前倍速版本）")
    return out_path, adjusted
