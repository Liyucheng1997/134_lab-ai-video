"""TTS 引擎：CosyVoice2（阿里·开源本地）。

CosyVoice2-0.5B 走 instruct2（自然语言情感/风格指令）+ zero-shot 音色克隆。
音色来自参考音频（默认用 CosyVoice 仓库自带 asset/zero_shot_prompt.wav，也可在
.env 用 COSYVOICE_REF_AUDIO/REF_TEXT 克隆指定声音）；界面上选择的「音色」其实是
不同的朗读情感预设（instruct 文案），特别适合荣格/纳瓦尔这类疗愈+思辨内容。

需先安装 CosyVoice 仓库依赖并下载模型，详见 README 与 .env.example。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config
from .utils import load_json, log, save_json

_model = None
_DEFAULT_PROMPT_TEXT = "希望你以后能够做的比我还好呦。"  # 仓库 asset/zero_shot_prompt.wav 的内容

# 显示名 -> instruct 情感/风格指令（音色由参考音频决定，这里调的是语气）。
VOICES = {
    "疗愈·温暖沉稳": "用温暖、沉稳、富有疗愈感的语气，像一位睿智的引路人娓娓道来。",
    "叙事·平静克制": "用平静、克制、富有思辨感的语气，缓缓地讲述。",
    "亲切·轻柔抚慰": "用轻柔、亲切、带着抚慰感的语气，轻声诉说。",
    "深沉·磁性低音": "用低沉、磁性、富有感染力的声音，沉稳地讲述。",
}
DEFAULT_VOICE = "疗愈·温暖沉稳"


def _instruct_text() -> str:
    v = (config.TTS_VOICE or "").strip()
    if v in VOICES:
        return VOICES[v]
    return (config.COSYVOICE_INSTRUCT or "").strip()


def _resolve_ref() -> tuple[str, str]:
    """返回 (参考音频路径, 参考文字)。"""
    custom = (config.COSYVOICE_REF_AUDIO or "").strip()
    if custom and Path(custom).exists():
        return custom, (config.COSYVOICE_REF_TEXT or "").strip()
    default = Path(config.COSYVOICE_REPO_DIR) / "asset" / "zero_shot_prompt.wav"
    if not default.exists():
        raise RuntimeError(
            f"未找到 CosyVoice 参考音频：{default}。请在 .env 设置 COSYVOICE_REF_AUDIO，"
            "或确认 COSYVOICE_REPO_DIR 指向 CosyVoice 仓库根目录。")
    return str(default), _DEFAULT_PROMPT_TEXT


def _patch_torchaudio_backend():
    """torchaudio 2.x 默认把 load/info 路由到 torchcodec（未装），改走 soundfile。"""
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


def _get_model():
    global _model
    if _model is not None:
        return _model

    _patch_torchaudio_backend()
    repo = config.COSYVOICE_REPO_DIR
    if not Path(repo).exists():
        raise RuntimeError(
            f"未找到 CosyVoice 仓库：{repo}。请 git clone CosyVoice 并设置 COSYVOICE_REPO_DIR。")
    for p in (repo, os.path.join(repo, "third_party", "Matcha-TTS")):
        if p not in sys.path:
            sys.path.append(p)

    import torch  # noqa: F401  确保 CUDA 环境就绪
    from cosyvoice.cli.cosyvoice import CosyVoice2

    model_dir = config.COSYVOICE_MODEL_DIR
    if not Path(model_dir).exists():
        raise RuntimeError(
            f"未找到 CosyVoice2 模型：{model_dir}。请下载 CosyVoice2-0.5B 并设置 COSYVOICE_MODEL_DIR。")
    log("tts", f"加载 CosyVoice2：{model_dir}")
    _model = CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=False)
    return _model


def _synth_one(text: str) -> tuple[np.ndarray, int]:
    model = _get_model()                       # 先加载（会把仓库加入 sys.path）
    ref_audio, ref_text = _resolve_ref()       # prompt_wav 传路径，frontend 内部自行 load_wav
    speed = max(float(config.TTS_SPEED), 0.1)
    instruct = _instruct_text()
    # text_frontend=False：跳过 wetext/pynini 文本正则（Windows 难装），译文本身已是干净中文
    if instruct:
        gen = model.inference_instruct2(text, instruct, ref_audio, stream=False,
                                        speed=speed, text_frontend=False)
    else:
        gen = model.inference_zero_shot(text, ref_text, ref_audio, stream=False,
                                        speed=speed, text_frontend=False)

    parts = []
    for out in gen:
        seg = out["tts_speech"].cpu().numpy().reshape(-1)
        parts.append(seg.astype(np.float32))
    wav = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 0:
        wav = wav * (0.95 / peak)
    return wav, int(model.sample_rate)


def sample(text: str, out_path: Path) -> Path:
    wav, sr = _synth_one(text[:120] or "你好，这是配音音色试听。")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, wav, sr)
    log("tts", f"试听音频已生成：{out_path.name}")
    return out_path


def synthesize(segments: list[dict], work_dir: Path, total_duration: float = 0.0
               ) -> tuple[Path, list[dict]]:
    out_path = work_dir / "dub.wav"
    seg_cache = work_dir / "dub_segments.json"
    if out_path.exists() and seg_cache.exists():
        log("tts", "复用已有配音音轨")
        return out_path, load_json(seg_cache)

    sr_ref: int | None = None
    gap_min, gap_max = config.TTS_GAP_MIN, config.TTS_GAP_MAX
    clips: list[tuple[float, np.ndarray]] = []   # (起始秒, 波形)
    retimed: list[dict] = []
    cursor = 0.0
    n = len(segments)
    log("tts", f"使用 CosyVoice2 合成配音（{config.TTS_VOICE}），共 {n} 段")

    for i, seg in enumerate(segments):
        text = (seg.get("zh") or "").strip()
        if not text:
            continue
        wav, sr = _synth_one(text)
        if sr_ref is None:
            sr_ref = sr
        elif sr != sr_ref:
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=sr_ref)
            sr = sr_ref
        dur = len(wav) / sr

        start = cursor
        clips.append((start, wav))
        retimed.append({"start": round(start, 3), "end": round(start + dur, 3),
                        "zh": text, "text": (seg.get("text") or "").strip()})
        cursor = start + dur
        if i + 1 < n:
            orig_gap = segments[i + 1].get("start", seg["end"]) - seg.get("end", start + dur)
            cursor += min(max(orig_gap, gap_min), gap_max)
        log("tts", f"  [{i + 1}/{n}] -> {start:6.1f}s (+{dur:4.1f}s)  {text[:40]}")

    sr_final = sr_ref or 24000
    total_samples = int(cursor * sr_final) + sr_final // 2
    master = np.zeros(max(total_samples, 1), dtype=np.float32)
    for start, wav in clips:
        pos = int(start * sr_final)
        end = min(pos + len(wav), total_samples)
        master[pos:end] += wav[:end - pos]

    peak = float(np.max(np.abs(master))) if master.size else 0.0
    if peak > 1.0:
        master = master / peak * 0.97
    sf.write(out_path, master, sr_final)
    save_json(seg_cache, retimed)
    log("tts", f"配音音轨完成：{out_path.name}（时长 {cursor:.0f}s）")
    return out_path, retimed
