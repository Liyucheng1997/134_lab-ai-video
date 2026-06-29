"""第 4 步：用 Qwen3-TTS 生成中文配音。

默认使用 Qwen3-TTS CustomVoice 的 ryan 音色。为了减少逐句生成导致的音色和
停顿漂移，正文会按字符数分块生成，每块内部保持连续口播，再按文本权重给字幕
重新计时。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config
from .utils import _NO_WINDOW, load_json, log, media_duration, save_json

_model = None

_BUILTIN_VOICES = {
    "ryan": "ryan",
    "aiden": "aiden",
    "dylan": "dylan",
    "eric": "eric",
    "ono_anna": "ono_anna",
    "serena": "serena",
    "sohee": "sohee",
    "uncle_fu": "uncle_fu",
    "vivian": "vivian",
}


def _voice_name() -> str:
    voice = (config.TTS_VOICE or "ryan").strip()
    return voice if voice in _BUILTIN_VOICES else "ryan"


def _configure_qwen_paths() -> None:
    env_path = config.PROJECT_VENV
    model_root = config.QWEN_TTS_CACHE_DIR
    model_root.mkdir(parents=True, exist_ok=True)

    os.environ["HF_HOME"] = str(model_root / "hf_home")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(model_root / "huggingface")
    os.environ["HF_HUB_CACHE"] = str(model_root / "huggingface")
    os.environ["MODELSCOPE_CACHE"] = str(model_root / "modelscope")
    os.environ["XDG_CACHE_HOME"] = str(model_root / "xdg_cache")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    path_parts = [
        env_path,
        env_path / "Scripts",
        env_path / "Library" / "bin",
        Path(config.FFMPEG).parent,
    ]
    os.environ["PATH"] = os.pathsep.join(map(str, path_parts)) + os.pathsep + os.environ.get("PATH", "")


def _dtype_from_config():
    import torch

    dtype = config.QWEN_TTS_DTYPE
    mapping = {
        "auto": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }
    return mapping.get(dtype, mapping["auto"])


def _get_model():
    global _model
    if _model is not None:
        return _model

    _configure_qwen_paths()
    import torch
    from qwen_tts import Qwen3TTSModel

    if config.QWEN_TTS_DEVICE.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("Qwen3-TTS 配置为 CUDA，但当前 torch.cuda.is_available() 为 false")
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
        torch.backends.cuda.matmul.allow_tf32 = True

    kwargs = {
        "device_map": config.QWEN_TTS_DEVICE,
        "dtype": _dtype_from_config(),
    }
    if config.QWEN_TTS_ATTENTION:
        kwargs["attn_implementation"] = config.QWEN_TTS_ATTENTION

    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    log("tts", f"加载 Qwen3-TTS：{config.QWEN_TTS_MODEL}（{device_name}）")
    _model = Qwen3TTSModel.from_pretrained(config.QWEN_TTS_MODEL, **kwargs)
    return _model


def _atempo_filter(speed: float) -> str:
    if speed <= 0:
        raise ValueError("TTS_SPEED 必须大于 0")
    factors: list[float] = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6g}" for factor in factors)


def _apply_speed(path: Path, speed: float) -> None:
    if abs(speed - 1.0) < 0.001:
        return
    temp = path.with_name(f"{path.stem}.speedtmp{path.suffix}")
    proc = subprocess.run(
        [config.FFMPEG, "-y", "-i", str(path), "-filter:a", _atempo_filter(speed), str(temp)],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Qwen3-TTS 语速处理失败：{(proc.stderr or '')[-1200:]}")
    temp.replace(path)


def _normalize_wav(wav: np.ndarray) -> np.ndarray:
    wav = np.asarray(wav, dtype=np.float32)
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 0:
        wav = wav * (0.95 / peak)
    return wav


def _generate_batch(texts: list[str]) -> tuple[list[np.ndarray], int]:
    model = _get_model()
    if config.QWEN_TTS_MODE == "voice-design":
        wavs, sr = model.generate_voice_design(
            text=texts,
            language=config.QWEN_TTS_LANGUAGE,
            instruct=config.QWEN_TTS_INSTRUCT,
        )
    else:
        wavs, sr = model.generate_custom_voice(
            text=texts,
            language=config.QWEN_TTS_LANGUAGE,
            speaker=_voice_name(),
            instruct=config.QWEN_TTS_INSTRUCT,
        )
    return [_normalize_wav(wav) for wav in wavs], int(sr)


def _generate_to_file(text: str, out_path: Path) -> tuple[np.ndarray, int]:
    wavs, sr = _generate_batch([text])
    wav = wavs[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, wav, sr)
    _apply_speed(out_path, config.TTS_SPEED)
    wav, sr = sf.read(out_path, dtype="float32", always_2d=False)
    if getattr(wav, "ndim", 1) > 1:
        wav = wav.mean(axis=1)
    return np.asarray(wav, dtype=np.float32), int(sr)


def _scene_weight(text: str) -> float:
    text = text.strip()
    punctuation = sum(1 for ch in text if ch in "，。！？；：、,.!?;:")
    content = sum(1 for ch in text if not ch.isspace())
    return max(1.0, content + punctuation * 2.5)


def _chunk_segments(segments: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0
    max_chars = max(120, int(config.QWEN_TTS_MAX_CHARS))

    for seg in segments:
        text = (seg.get("zh") or "").strip()
        if not text:
            continue
        text_len = len(text)
        if current and current_len + text_len > max_chars:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(seg)
        current_len += text_len
    if current:
        chunks.append(current)
    return chunks


def sample(text: str, out_path: Path, voice: str | None = None) -> Path:
    """合成一小段试听音频（用于前端选音色时预览）。"""
    if voice:
        config.TTS_VOICE = voice
    _configure_qwen_paths()
    _generate_to_file(text[:120] or "你好，这是配音音色试听。", out_path)
    log("tts", f"试听音频已生成：{out_path.name}")
    return out_path


def synthesize(segments: list[dict], work_dir: Path, total_duration: float = 0.0
               ) -> tuple[Path, list[dict]]:
    out_path = work_dir / "dub.wav"
    seg_cache = work_dir / "dub_segments.json"
    if out_path.exists() and seg_cache.exists():
        log("tts", "复用已有配音音轨")
        return out_path, load_json(seg_cache)

    chunks = _chunk_segments(segments)
    if not chunks:
        raise RuntimeError("没有可合成的中文文本")

    batch_size = max(1, int(getattr(config, "QWEN_TTS_BATCH_SIZE", 2)))
    log("tts", f"使用 Qwen3-TTS / {_voice_name()} 合成配音，共 {len(chunks)} 个文本块，batch={batch_size}")

    all_audio: list[np.ndarray] = []
    retimed: list[dict] = []
    sr_ref: int | None = None
    cursor = 0.0
    speed = max(float(config.TTS_SPEED), 0.01)

    chunk_texts = [
        "\n".join((seg.get("zh") or "").strip() for seg in chunk if (seg.get("zh") or "").strip())
        for chunk in chunks
    ]

    for batch_start in range(0, len(chunks), batch_size):
        batch_chunks = chunks[batch_start:batch_start + batch_size]
        batch_texts = chunk_texts[batch_start:batch_start + batch_size]
        try:
            batch_wavs, sr = _generate_batch(batch_texts)
        except RuntimeError as e:
            if batch_size <= 1 or "out of memory" not in str(e).lower():
                raise
            log("tts", "batch 显存不足，自动降级为单块生成")
            batch_wavs = []
            sr = sr_ref or 24000
            for text in batch_texts:
                one_wavs, sr = _generate_batch([text])
                batch_wavs.extend(one_wavs)
        if sr_ref is None:
            sr_ref = sr
        elif sr != sr_ref:
            raise RuntimeError(f"Qwen3-TTS 输出采样率不一致：{sr_ref} vs {sr}")

        for offset, (chunk, text, wav) in enumerate(zip(batch_chunks, batch_texts, batch_wavs), start=1):
            chunk_index = batch_start + offset
            raw_duration = len(wav) / sr
            chunk_duration = raw_duration / speed
            weights = [_scene_weight(seg.get("zh", "")) for seg in chunk]
            total_weight = sum(weights) or 1.0
            pos = cursor
            for seg, weight in zip(chunk, weights):
                dur = chunk_duration * weight / total_weight
                retimed.append({
                    "start": round(pos, 3),
                    "end": round(pos + dur, 3),
                    "zh": (seg.get("zh") or "").strip(),
                    "text": (seg.get("text") or "").strip(),
                })
                pos += dur

            all_audio.append(wav)
            cursor += chunk_duration
            if chunk_index < len(chunks):
                gap = min(max(config.TTS_GAP_MIN, 0.0), config.TTS_GAP_MAX)
                if gap > 0:
                    all_audio.append(np.zeros(int(gap * speed * sr), dtype=np.float32))
                    cursor += gap
            log("tts", f"  [{chunk_index}/{len(chunks)}] {chunk_duration:.1f}s  {text[:40]}")

    sr_final = sr_ref or 24000
    master = np.concatenate(all_audio) if all_audio else np.zeros(1, dtype=np.float32)
    peak = float(np.max(np.abs(master))) if master.size else 0.0
    if peak > 1.0:
        master = master / peak * 0.97
    sf.write(out_path, master, sr_final)
    _apply_speed(out_path, config.TTS_SPEED)
    save_json(seg_cache, retimed)
    log("tts", f"配音音轨完成：{out_path.name}（时长 {media_duration(out_path):.0f}s）")
    return out_path, retimed
