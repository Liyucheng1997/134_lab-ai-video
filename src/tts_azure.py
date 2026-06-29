"""TTS 引擎：Azure 认知服务 文本转语音（云端·Neural，REST V1）。

逐段调用 Azure TTS REST 接口（24kHz 单声道 PCM），顺序拼接成整条音轨。
语速由界面滑块映射到 SSML prosody rate，可选朗读风格（mstts express-as）。
需在 .env 配置 AZURE_SPEECH_KEY 与 AZURE_SPEECH_REGION。
"""
from __future__ import annotations

import io
import time
import urllib.request
import urllib.error
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import soundfile as sf

from . import config
from .utils import load_json, log, save_json

# 显示名 -> Azure 神经网络语音 id。挑选偏叙事/疗愈、适合荣格 & 纳瓦尔内容的中文音色。
VOICES = {
    "云野·沉稳叙事男声": "zh-CN-YunyeNeural",
    "云泽·睿智沧桑男声": "zh-CN-YunzeNeural",
    "云希·温暖年轻男声": "zh-CN-YunxiNeural",
    "云扬·专业播报男声": "zh-CN-YunyangNeural",
    "晓晓·治愈多情感女声": "zh-CN-XiaoxiaoNeural",
    "晓辰·自然叙事女声": "zh-CN-XiaochenNeural",
    "晓墨·细腻情感女声": "zh-CN-XiaomoNeural",
}
DEFAULT_VOICE = "云野·沉稳叙事男声"

_OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"
_token: tuple[float, str] | None = None


def _require_creds() -> tuple[str, str]:
    key = (config.AZURE_SPEECH_KEY or "").strip()
    region = (config.AZURE_SPEECH_REGION or "").strip()
    if not key:
        raise RuntimeError("未配置 AZURE_SPEECH_KEY，请在 .env 填写 Azure 语音服务密钥")
    if not region:
        raise RuntimeError("未配置 AZURE_SPEECH_REGION，请在 .env 填写 Azure 区域，如 eastus")
    return key, region


def _get_token() -> str:
    """获取并缓存鉴权 token（有效期 10 分钟，提前到 9 分钟刷新）。"""
    global _token
    now = time.time()
    if _token and now - _token[0] < 9 * 60:
        return _token[1]
    key, region = _require_creds()
    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    req = urllib.request.Request(url, data=b"", method="POST",
                                 headers={"Ocp-Apim-Subscription-Key": key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        token = resp.read().decode("utf-8")
    _token = (now, token)
    return token


def _voice_id() -> str:
    v = (config.TTS_VOICE or "").strip()
    if v in VOICES:
        return VOICES[v]
    if v.lower().startswith("zh-") or v.endswith("Neural"):
        return v
    return VOICES[DEFAULT_VOICE]


def _rate_str() -> str:
    """界面语速（约 0.7~1.3）映射为 SSML prosody rate 百分比。"""
    pct = round((max(float(config.TTS_SPEED), 0.1) - 1.0) * 100)
    return f"{pct:+d}%"


def _build_ssml(text: str) -> str:
    voice = _voice_id()
    inner = f'<prosody rate="{_rate_str()}">{escape(text)}</prosody>'
    style = (config.AZURE_TTS_STYLE or "").strip()
    if style:
        degree = (config.AZURE_TTS_STYLE_DEGREE or "1").strip()
        inner = (f'<mstts:express-as style="{escape(style)}" styledegree="{escape(degree)}">'
                 f'{inner}</mstts:express-as>')
    return (
        "<speak version='1.0' xml:lang='zh-CN' "
        "xmlns:mstts='https://www.w3.org/2001/mstts'>"
        f"<voice name='{voice}'>{inner}</voice></speak>"
    )


def _synth_one(text: str, retries: int = 3) -> tuple[np.ndarray, int]:
    _, region = _require_creds()
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    body = _build_ssml(text).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Authorization": f"Bearer {_get_token()}",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
                "User-Agent": "ai-video-pipeline",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
            wav, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
            if getattr(wav, "ndim", 1) > 1:
                wav = wav.mean(axis=1)
            return np.asarray(wav, dtype=np.float32), int(sr)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 401:            # token 过期，强制刷新后重试
                global _token
                _token = None
            if e.code not in (401, 429, 500, 503) or attempt == retries:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "ignore")[:300]
                except Exception:
                    pass
                raise RuntimeError(f"Azure TTS 请求失败（HTTP {e.code}）：{detail}") from e
            time.sleep(1.5 * attempt)
        except urllib.error.URLError as e:
            last_err = e
            if attempt == retries:
                raise RuntimeError(f"Azure TTS 网络错误：{e}") from e
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Azure TTS 合成失败：{last_err}")


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

    sr = 24000  # 输出格式固定 24kHz
    gap_min, gap_max = config.TTS_GAP_MIN, config.TTS_GAP_MAX
    clips: list[tuple[int, np.ndarray]] = []
    retimed: list[dict] = []
    cursor = 0.0
    n = len(segments)
    style = (config.AZURE_TTS_STYLE or "").strip() or "默认"
    log("tts", f"使用 Azure TTS / {_voice_id()}（风格 {style}）合成配音，共 {n} 段")

    for i, seg in enumerate(segments):
        text = (seg.get("zh") or "").strip()
        if not text:
            continue
        wav, wsr = _synth_one(text)
        if wsr != sr:
            import librosa
            wav = librosa.resample(wav, orig_sr=wsr, target_sr=sr)
        dur = len(wav) / sr

        start = cursor
        clips.append((int(start * sr), wav))
        retimed.append({"start": round(start, 3), "end": round(start + dur, 3),
                        "zh": text, "text": (seg.get("text") or "").strip()})
        cursor = start + dur
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
    log("tts", f"配音音轨完成：{out_path.name}（时长 {cursor:.0f}s）")
    return out_path, retimed
