"""6 步流水线的逐步执行逻辑。每一步读取 work/<id>/ 里的产物，写出自己的产物。

由 step.py 以独立子进程调用（ASR 用 ctranslate2、TTS 用 torch，必须分进程避免 CUDA 崩溃）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import (compose as compose_mod, config, download, publish as publish_mod,
               subtitles, translate as translate_mod, transcribe, tts)
from .utils import load_json, log, run, save_json

# UI 用的步骤定义：key / 名称 / 依赖的上一步 / 完成标志文件
STEP_DEFS = [
    {"key": "download",  "name": "下载视频/音频", "needs": None,        "artifact": "source.wav"},
    {"key": "asr",       "name": "语音转文字",     "needs": "download",  "artifact": "segments.json"},
    {"key": "translate", "name": "翻译为中文",     "needs": "asr",       "artifact": "translated.json"},
    {"key": "tts",       "name": "中文配音",       "needs": "translate", "artifact": "dub.wav"},
    {"key": "compose",   "name": "合成视频",       "needs": "tts",       "artifact": "final.mp4"},
    {"key": "publish",   "name": "保存信息归档",   "needs": "compose",   "artifact": "publish/metadata.json"},
]


def work_dir_of(job_id: str) -> Path:
    map_file = config.OUTPUT_DIR / "_job_dirs.json"
    d = config.WORK_DIR / job_id
    if map_file.exists():
        try:
            data = json.loads(map_file.read_text(encoding="utf-8"))
            entry = data.get(job_id) if isinstance(data, dict) else None
            if isinstance(entry, dict) and entry.get("cache_dir"):
                d = Path(entry["cache_dir"])
            elif isinstance(entry, str):
                d = Path(entry)
        except Exception:
            d = config.WORK_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def final_path(job_id: str) -> Path:
    return work_dir_of(job_id) / "final.mp4"


def _register_output_project(job_id: str, archive_dir: str | Path) -> None:
    project = Path(archive_dir)
    cache = project / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    map_file = config.OUTPUT_DIR / "_job_dirs.json"
    try:
        data = json.loads(map_file.read_text(encoding="utf-8")) if map_file.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[job_id] = {"project_dir": str(project), "cache_dir": str(cache)}
    tmp = map_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(map_file)


# ----------------------------------------------------------------- 1) 下载/导入
def run_download(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    media = cfg.get("media", "video")          # video | audio
    url = (cfg.get("url") or "").strip()
    local = cfg.get("file")

    if local:                                   # 上传的本地文件
        download.prepare_local(Path(local), wd)
    elif media == "audio":                       # 只下音频
        m4a = wd / "source.m4a"
        if not m4a.exists():
            log("download", f"下载音频：{url}")
            download._ytdlp_stream([config.YT_DLP, "--newline", "-f", "ba/b",
                 "-x", "--audio-format", "m4a",
                 "--ffmpeg-location", str(Path(config.FFMPEG).parent),
                 "-o", str(wd / "source.%(ext)s"), url])
        if not (wd / "source.wav").exists():
            run([config.FFMPEG, "-y", "-i", str(m4a), "-vn", "-ac", "1", "-ar", "16000",
                 str(wd / "source.wav")], desc="抽 16k 音轨")
    else:                                        # 下视频
        download.download(url, wd)

    has_video = (wd / "source.mp4").exists()
    return {"has_video": has_video, "audio": str(wd / "source.wav")}


# ----------------------------------------------------------------- 2) 识别
def run_asr(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    if cfg.get("model"):
        config.WHISPER_MODEL = cfg["model"]
    lang = (cfg.get("language") or "").strip() or None
    segs = transcribe.transcribe(wd / "source.wav", wd, language=lang)
    return {"count": len(segs)}


# ----------------------------------------------------------------- 3) 翻译
def run_translate(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    data = load_json(wd / "segments.json")
    segs = data["segments"] if isinstance(data, dict) else data
    engine = cfg.get("engine", "deepseek")
    before = load_json(wd / "translated.json")
    out = translate_mod.translate(segs, wd, engine=engine)
    if before is not None and before != out:
        for name in ("dub.wav", "dub_segments.json", "subs.srt", "subs.vtt",
                     "subs.ass", "cover_title.txt"):
            (wd / name).unlink(missing_ok=True)
        final_path(job_id).unlink(missing_ok=True)
        log("translate", "译文已更新，已清理旧配音、字幕和成片")
    return {"count": len(out)}


# ----------------------------------------------------------------- 4) 配音
def run_tts(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    if cfg.get("engine"):
        config.TTS_ENGINE = cfg["engine"]
    if cfg.get("voice"):
        config.TTS_VOICE = cfg["voice"]
    if cfg.get("speed") is not None:
        try:
            config.TTS_SPEED = max(0.5, min(1.6, float(cfg["speed"])))
        except (TypeError, ValueError):
            pass
    segs = load_json(wd / "translated.json")
    # 改了配音参数需要重算：删除旧产物
    if cfg.get("force"):
        for f in ("dub.wav", "dub_segments.json"):
            (wd / f).unlink(missing_ok=True)
    _, retimed = tts.synthesize(segs, wd)
    return {"count": len(retimed), "duration": retimed[-1]["end"] if retimed else 0}


# ----------------------------------------------------------------- 5) 合成
def run_compose(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    segs = load_json(wd / "dub_segments.json") or load_json(wd / "translated.json")
    bilingual = bool(cfg.get("bilingual", False))
    presets = {p["key"]: p for p in config.SUB_PRESETS}
    style = presets.get(cfg.get("preset", config.SUB_PRESET), presets["classic"])
    log("compose", f"字幕样式：{style['name']}")
    subtitles.build(segs, wd, bilingual=bilingual, style=style)
    ass = wd / "subs.ass"

    # 封面标题由 DeepSeek 自动生成（取译文全文），并缓存供发布步骤复用
    full = "".join(s.get("zh", "") for s in segs)
    title = publish_mod.gen_title(full)
    (wd / "cover_title.txt").write_text(title, encoding="utf-8")
    log("compose", f"封面标题：{title}")

    mode = cfg.get("mode", "original")
    if mode == "original" and not (wd / "source.mp4").exists():
        mode = "image"                          # 没有视频只能用图片
        log("compose", "无原视频，自动改用图片合成")

    out = final_path(job_id)
    compose_mod.compose(
        mode=mode, work_dir=wd, audio=wd / "dub.wav",
        ass=(ass if cfg.get("burn", True) else None), out_path=out,
        title=title, bg=cfg.get("bg", "#10131a"), bg2=cfg.get("bg2", "#1d2740"),
    )
    return {"mode": mode, "output": str(out), "title": title}


# ----------------------------------------------------------------- 6) 保存信息归档
def run_publish(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    meta = publish_mod.prepare(
        work_dir=wd, final_video=final_path(job_id),
        platform=cfg.get("platform", "bilibili"),
        mode="archive",
        tid=cfg.get("tid"),
        copyright=cfg.get("copyright"),
        archive_dir=cfg.get("archive_dir"),
    )
    if meta.get("archive_dir"):
        _register_output_project(job_id, meta["archive_dir"])
    return meta


RUNNERS = {
    "download": run_download, "asr": run_asr, "translate": run_translate,
    "tts": run_tts, "compose": run_compose, "publish": run_publish,
}


def run_step(step: str, job_id: str, cfg: dict) -> dict:
    if step not in RUNNERS:
        raise ValueError(f"未知步骤：{step}")
    result = RUNNERS[step](job_id, cfg)
    save_json(work_dir_of(job_id) / f"{step}.result.json", result or {})
    return result or {}
