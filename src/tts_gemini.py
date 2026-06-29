"""TTS 引擎：Google Gemini TTS（云端·可控情感，REST generateContent）。

逐段调用 Gemini 语音模型（输出 24kHz 16-bit 单声道 PCM），顺序拼接成整条音轨。
通过自然语言指令控制语气（GEMINI_TTS_STYLE），语速用 ffmpeg atempo 处理。
需在 .env 配置 GEMINI_API_KEY（或 GOOGLE_API_KEY）。
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config
from .utils import _NO_WINDOW, load_json, log, save_json

# 显示名 -> Gemini 预置音色。Gemini 官方不标性别，以下为社区实测偏「男声」的全部音色，
# 多语言、自动识别中文。★ = 偏适合灵性/哲学/疗愈赛道（低沉、亲密、沉静）的声音，建议优先试听。
VOICES = {
    "Charon·沉稳叙事 ★": "Charon",          # Informative，低沉、有信服力
    "Enceladus·气声亲密 ★": "Enceladus",    # Breathy，气声、贴耳，灵性赛道很火的那种
    "Algieba·圆润顺滑 ★": "Algieba",        # Smooth，顺滑温润
    "Algenib·沙哑磁性 ★": "Algenib",        # Gravelly，沙哑、有颗粒感
    "Iapetus·清澈干净 ★": "Iapetus",        # Clear
    "Rasalgethi·知性沉静 ★": "Rasalgethi",  # Informative
    "Schedar·平稳沉着 ★": "Schedar",        # Even，平稳不起伏
    "Gacrux·成熟厚重 ★": "Gacrux",          # Mature，成熟中年感
    "Umbriel·从容随和": "Umbriel",          # Easy-going
    "Sadaltager·博学讲述": "Sadaltager",    # Knowledgeable
    "Orus·坚定有力": "Orus",                # Firm
    "Alnilam·坚定稳重": "Alnilam",          # Firm
    "Zubenelgenubi·随性口语": "Zubenelgenubi",  # Casual
    "Achird·亲切友好": "Achird",            # Friendly
    "Puck·轻快上扬": "Puck",                # Upbeat
    "Fenrir·激昂有劲": "Fenrir",            # Excitable
}
DEFAULT_VOICE = "Charon·沉稳叙事 ★"

_PCM_RATE = 24000

# 计费估算：美元/百万 token（输入文本, 输出音频）。官方价可能变动，可用 .env 覆盖。
_PRICE = {
    "gemini-2.5-pro": (1.00, 20.00),
    "gemini-2.5-flash": (0.50, 10.00),
}
_usage = {"in": 0, "out": 0, "total": 0}


def _reset_usage() -> None:
    _usage["in"] = _usage["out"] = _usage["total"] = 0


def _add_usage(meta: dict | None) -> None:
    if not meta:
        return
    _usage["in"] += int(meta.get("promptTokenCount", 0) or 0)
    _usage["out"] += int(meta.get("candidatesTokenCount", 0) or 0)
    _usage["total"] += int(meta.get("totalTokenCount", 0) or 0)


def _price_per_million() -> tuple[float, float]:
    if config.GEMINI_PRICE_IN > 0 or config.GEMINI_PRICE_OUT > 0:
        return config.GEMINI_PRICE_IN, config.GEMINI_PRICE_OUT
    model = _model_name()
    for k, v in _PRICE.items():
        if model.startswith(k):
            return v
    return _PRICE["gemini-2.5-flash"]


def _usage_cost() -> dict:
    pin, pout = _price_per_million()
    usd = _usage["in"] / 1e6 * pin + _usage["out"] / 1e6 * pout
    cny = usd * config.USD_TO_CNY
    total = _usage["total"] or (_usage["in"] + _usage["out"])
    return {
        "engine": "gemini",
        "model": _model_name(),
        "input_tokens": _usage["in"],
        "audio_tokens": _usage["out"],
        "total_tokens": total,
        "usd": round(usd, 6),
        "cny": round(cny, 4),
    }


def _log_cost(prefix: str) -> dict:
    cost = _usage_cost()
    log("tts", f"{prefix}：输入 {_usage['in']} + 输出(音频) {_usage['out']} = {cost['total_tokens']} tokens，"
               f"约 ${cost['usd']:.4f}（≈¥{cost['cny']:.3f}，估算）")
    return cost


def _api_key() -> str:
    key = (config.GEMINI_API_KEY or "").split("#", 1)[0].strip()
    if not key:
        raise RuntimeError("未配置 GEMINI_API_KEY，请在 .env 填写 Google Gemini API 密钥")
    return key


def _voice_id() -> str:
    v = (config.TTS_VOICE or "").strip()
    if v in VOICES:
        return VOICES[v]
    # 也允许直接填 Gemini 原始音色名
    if v and v in VOICES.values():
        return v
    return VOICES[DEFAULT_VOICE]


def _sanitize_style(s: str) -> str:
    """清理语气指令：去掉引号/书名号等会让 Gemini TTS 空返回(finishReason=OTHER)的标点。"""
    s = re.sub(r'[“”"\'‘’「」『』《》()（）]', "", s or "")
    s = re.sub(r"\s+", " ", s).strip(" ：:，,。.")
    return s


def _prompt_text(text: str) -> str:
    style = _sanitize_style(config.GEMINI_TTS_STYLE or "")
    anchor = "全程保持同一个说话人、同一音色、同一年龄感和同一麦克风距离，不要在不同段落间改变声线"
    if style:
        return f"{anchor}；{style}：{text}"
    return f"{anchor}：{text}"


def _rate_from_mime(mime: str) -> int:
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else _PCM_RATE


def _model_name() -> str:
    # 容错：.env 里若把行内注释 (# ...) 或多余空格混进值，这里剥掉，只取首个 token
    raw = (config.GEMINI_TTS_MODEL or "").split("#", 1)[0].strip()
    return raw.split()[0] if raw else "gemini-2.5-flash-preview-tts"


def _request_audio(url: str, text: str, use_style: bool) -> tuple[np.ndarray | None, int, str]:
    """单次请求。返回 (波形|None, 采样率, 状态说明)。None 表示本次未拿到音频，可重试。"""
    prompt = _prompt_text(text) if use_style else text
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": _voice_id()}}
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=max(60, int(getattr(config, "GEMINI_TTS_TIMEOUT", 180)))) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    inline = next((p["inlineData"] for p in parts if isinstance(p, dict) and "inlineData" in p), None)
    if inline is None:
        # Gemini 偶发空返回（finishReason=OTHER 等），尤其带 style 前缀时；交给上层重试
        return None, 0, f"空返回(finishReason={cand.get('finishReason')})"
    _add_usage(data.get("usageMetadata"))
    raw = base64.b64decode(inline["data"])
    sr = _rate_from_mime(inline.get("mimeType", ""))
    wav = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 0.98:
        wav = wav * (0.98 / peak)
    return wav, sr, "ok"


def _synth_one(text: str, retries: int = 5) -> tuple[np.ndarray, int]:
    model = _model_name()
    base = (config.GEMINI_BASE_URL or "https://generativelanguage.googleapis.com").rstrip("/")
    url = f"{base}/v1beta/models/{model}:generateContent?key={_api_key()}"
    has_style = bool(_sanitize_style(config.GEMINI_TTS_STYLE or ""))
    drop_style = False     # 一旦出现空返回/误判，后续不再加 style（纯文本实测几乎 100% 成功）
    last = ""
    for attempt in range(1, retries + 1):
        use_style = has_style and not drop_style and attempt <= retries - 2
        try:
            wav, sr, status = _request_audio(url, text, use_style)
            if wav is not None:
                return wav, sr
            last = status            # 空返回(finishReason=OTHER)：模型把 style 当成了"生成文本"
            drop_style = True
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            last = f"HTTP {e.code} {detail}"
            # 模型误判为文本生成 → 去掉 style 重试；429/5xx → 退避重试；其它 4xx → 直接失败
            if "should only be used for TTS" in detail or "only be used for TTS" in detail:
                drop_style = True
            elif e.code not in (429, 500, 503) or attempt == retries:
                raise RuntimeError(f"Gemini TTS 请求失败（HTTP {e.code}）：{detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
            # 含读超时(TimeoutError)/连接中断(OSError)/空响应解析错；超时后重试通常即可恢复
            last = f"{type(e).__name__}: {e}"
            if attempt == retries:
                raise RuntimeError(f"Gemini TTS 解析/网络错误：{e}") from e
        time.sleep(min(1.5 * attempt, 6.0))
    raise RuntimeError(f"Gemini TTS 合成失败（已重试 {retries} 次）：{last}")


def _weight(text: str) -> float:
    text = (text or "").strip()
    punctuation = sum(1 for ch in text if ch in "，。！？；：、,.!?;:")
    content = sum(1 for ch in text if not ch.isspace())
    return max(1.0, content + punctuation * 2.5)


def _chunk_segments(segments: list[dict], max_chars: int | None = None) -> list[list[dict]]:
    """把相邻段落合并成不超过 max_chars 字的块，一个块一个 Gemini 请求。"""
    max_chars = max(120, int(max_chars or getattr(config, "GEMINI_TTS_MAX_CHARS", 800)))
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 0
    for seg in segments:
        text = (seg.get("zh") or "").strip()
        if not text:
            continue
        if cur and cur_len + len(text) > max_chars:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(seg)
        cur_len += len(text)
    if cur:
        chunks.append(cur)
    return chunks


def _normalize_clip(wav: np.ndarray) -> np.ndarray:
    """按有效人声 RMS 做温和归一，降低分块之间的响度和距离感差异。"""
    target = max(0.02, min(0.20, float(getattr(config, "GEMINI_TTS_TARGET_RMS", 0.075))))
    if wav.size == 0:
        return wav
    active = wav[np.abs(wav) > 0.006]
    if active.size < max(200, wav.size // 50):
        active = wav
    rms = float(np.sqrt(np.mean(active * active))) if active.size else 0.0
    if rms <= 1e-6:
        return wav
    gain = max(0.55, min(1.8, target / rms))
    out = wav * gain
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.98:
        out = out * (0.98 / peak)
    return out.astype(np.float32, copy=False)


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


def _apply_speed_file(path: Path, speed: float) -> None:
    if abs(speed - 1.0) < 0.01:
        return
    temp = path.with_name(f"{path.stem}.speedtmp{path.suffix}")
    proc = subprocess.run(
        [config.FFMPEG, "-y", "-i", str(path), "-filter:a", _atempo_filter(speed), str(temp)],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Gemini TTS 语速处理失败：{(proc.stderr or '')[-1200:]}")
    temp.replace(path)


def sample(text: str, out_path: Path) -> Path:
    _reset_usage()
    wav, sr = _synth_one(text[:120] or "你好，这是配音音色试听。")
    _log_cost("试听用量")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, wav, sr)
    _apply_speed_file(out_path, max(float(config.TTS_SPEED), 0.1))
    log("tts", f"试听音频已生成：{out_path.name}")
    return out_path


def synthesize(segments: list[dict], work_dir: Path, total_duration: float = 0.0
               ) -> tuple[Path, list[dict]]:
    out_path = work_dir / "dub.wav"
    seg_cache = work_dir / "dub_segments.json"
    if out_path.exists() and seg_cache.exists():
        log("tts", "复用已有配音音轨")
        return out_path, load_json(seg_cache)

    _reset_usage()
    speed = max(float(config.TTS_SPEED), 0.1)
    gap = min(max(config.TTS_GAP_MIN, 0.0), config.TTS_GAP_MAX)
    sr_ref: int | None = None
    clips: list[tuple[float, np.ndarray]] = []   # (起始秒, 波形)
    retimed: list[dict] = []
    cursor = 0.0

    # 分块：把多段合成一个请求，大幅减少请求数（免费额度/限流友好），块内按字数权重重新计时
    chunks = _chunk_segments(segments)
    if not chunks:
        raise RuntimeError("没有可合成的中文文本")
    log("tts", f"使用 Gemini TTS / {_voice_id()}（{config.GEMINI_TTS_MODEL}）合成配音，"
               f"共 {sum(len(c) for c in chunks)} 段 → {len(chunks)} 个请求，"
               f"每批最多 {config.GEMINI_TTS_MAX_CHARS} 字，启用稳定声线提示")

    for ci, chunk in enumerate(chunks, 1):
        text = "".join((s.get("zh") or "").strip() for s in chunk)
        wav, sr = _synth_one(text)
        if sr_ref is None:
            sr_ref = sr
        elif sr != sr_ref:
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=sr_ref)
            sr = sr_ref
        wav = _normalize_clip(wav)
        chunk_dur = len(wav) / sr

        # 块内各段按字数权重分配时长（与 Qwen 引擎一致），保证字幕与配音对齐
        weights = [_weight(s.get("zh", "")) for s in chunk]
        total_w = sum(weights) or 1.0
        clips.append((cursor, wav))
        pos = cursor
        for s, w in zip(chunk, weights):
            d = chunk_dur * w / total_w
            retimed.append({"start": round(pos, 3), "end": round(pos + d, 3),
                            "zh": (s.get("zh") or "").strip(),
                            "text": (s.get("text") or "").strip()})
            pos += d
        cursor += chunk_dur + (gap if ci < len(chunks) else 0.0)
        log("tts", f"  [{ci}/{len(chunks)}] {chunk_dur:.1f}s  {text[:40]}")

    sr_final = sr_ref or _PCM_RATE
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
    _apply_speed_file(out_path, speed)
    if abs(speed - 1.0) >= 0.01:
        retimed = [
            {
                **seg,
                "start": round(seg["start"] / speed, 3),
                "end": round(seg["end"] / speed, 3),
            }
            for seg in retimed
        ]
    save_json(seg_cache, retimed)
    cost = _log_cost("本片 Gemini 用量")
    save_json(work_dir / "gemini_tts_usage.json", cost)
    log("tts", f"配音音轨完成：{out_path.name}（时长 {cursor:.0f}s）")
    return out_path, retimed
