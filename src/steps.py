"""6 步流水线的逐步执行逻辑。每一步读取 work/<id>/ 里的产物，写出自己的产物。

由 step.py 以独立子进程调用（ASR 用 ctranslate2、TTS 用 torch，必须分进程避免 CUDA 崩溃）。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from . import (compose as compose_mod, config, download, profiles, publish as publish_mod,
               scene_art, subtitles, translate as translate_mod, transcribe, tts)
from .utils import load_json, log, run, save_json

# UI 用的步骤定义：key / 名称 / 依赖的上一步 / 完成标志文件
STEP_DEFS = [
    {"key": "download",  "name": "视频下载",       "needs": None,        "artifact": "source.wav"},
    {"key": "asr",       "name": "语音识别",       "needs": "download",  "artifact": "segments.json"},
    {"key": "translate", "name": "翻译/洗稿",       "needs": "asr",       "artifact": "translated.json"},
    {"key": "tts",       "name": "中文配音",       "needs": "translate", "artifact": "dub.wav"},
    {"key": "compose",   "name": "视频合成",       "needs": "tts",       "artifact": "final.mp4"},
    {"key": "publish",   "name": "灯火归档",       "needs": "compose",   "artifact": "publish/metadata.json"},
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
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    last_error: Exception | None = None
    for attempt in range(8):
        tmp = map_file.with_name(f"{map_file.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, map_file)
            return
        except PermissionError as e:
            last_error = e
            time.sleep(0.05 * (attempt + 1))
        finally:
            tmp.unlink(missing_ok=True)
    if last_error:
        raise last_error


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
            download.ensure_pot_server()
            audio_base = [*config.YT_DLP, "--newline", "--no-continue",
                 *config.ytdlp_access_args(),
                 "-x", "--audio-format", "m4a",
                 "--ffmpeg-location", str(Path(config.FFMPEG).parent),
                 "-o", str(wd / "source.%(ext)s")]
            try:
                download._ytdlp_stream([*audio_base, "-f", "ba/b", url])
            except RuntimeError:
                # SABR 实验会封锁 DASH 音频流，降级到 mweb 渐进流抽音轨
                download._clean_partial_downloads(wd)
                log("download", "音频流被 YouTube 拦截，降级到 360p 渐进流抽音轨…")
                download._ytdlp_stream([*audio_base,
                     "--extractor-args", "youtube:player_client=mweb",
                     "-f", "18/b", url])
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
    source_language = (
        (cfg.get("source_language") or "").strip()
        or (data.get("language") if isinstance(data, dict) else None)
    )
    engine = cfg.get("engine", "deepseek")
    rewrite_level = cfg.get("rewrite_level", "high")
    before = load_json(wd / "translated.json")
    out = translate_mod.translate(
        segs,
        wd,
        engine=engine,
        rewrite_level=rewrite_level,
        source_language=source_language,
        profile=cfg.get("profile"),
        max_zh_segment_chars=cfg.get(
            "max_zh_segment_chars",
            config.TRANSLATE_MAX_ZH_SEGMENT_CHARS,
        ),
    )
    if before is not None and before != out:
        for name in ("dub.wav", "dub_segments.json", "dub_speed.json",
                     "compose_audio_speed.json",
                     "subs.srt", "subs.vtt",
                     "subs.ass", "cover_title.txt", "image_title.txt"):
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
    if cfg.get("f5_parallel") is not None:
        try:
            config.F5_TTS_PARALLEL = max(1, min(4, int(cfg["f5_parallel"])))
        except (TypeError, ValueError):
            pass
    segs = load_json(wd / "translated.json")
    # 改了配音参数需要重算：删除旧产物；F5 原速中间音频只临时生成，不再落盘保留。
    if cfg.get("force"):
        for f in ("dub.wav", "dub_segments.json", "dub_speed.json",
                  "compose_audio_speed.json"):
            (wd / f).unlink(missing_ok=True)
    _, retimed = tts.synthesize(segs, wd)
    (wd / "compose_audio_speed.json").unlink(missing_ok=True)
    for name in ("subs.srt", "subs.vtt", "subs.ass", "final.mp4",
                 "compose.result.json", "cover_title.txt", "image_title.txt"):
        (wd / name).unlink(missing_ok=True)
    result = {"count": len(retimed), "duration": retimed[-1]["end"] if retimed else 0}
    return result


def _atempo_filter(speed: float) -> str:
    speed = float(speed or 1.0)
    if speed <= 0:
        speed = 1.0
    parts: list[float] = []
    while speed > 2.0:
        parts.append(2.0)
        speed /= 2.0
    while speed < 0.5:
        parts.append(0.5)
        speed /= 0.5
    parts.append(speed)
    return ",".join(f"atempo={x:.6g}" for x in parts)


def _apply_compose_audio_speed(wd: Path, target_speed) -> list[dict]:
    """Apply step-5-only speed to dub.wav in place and keep subtitles aligned."""
    try:
        target = max(0.5, min(1.6, float(target_speed or 1.0)))
    except (TypeError, ValueError):
        target = 1.0

    meta_path = wd / "compose_audio_speed.json"
    meta = load_json(meta_path) or {}
    try:
        previous = max(0.5, min(1.6, float(meta.get("speed", 1.0))))
    except (TypeError, ValueError):
        previous = 1.0

    wav = wd / "dub.wav"
    seg_path = wd / "dub_segments.json"
    segs = load_json(seg_path) or []
    ratio = target / previous if previous else target

    if abs(ratio - 1.0) < 1e-3:
        save_json(meta_path, {"speed": target})
        if abs(target - 1.0) >= 1e-3:
            log("compose", f"沿用第 5 步配音变速 {target:.2f}x")
        return segs

    tmp = wav.with_name(f"{wav.stem}.compose_speedtmp{wav.suffix}")
    run([
        config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav),
        "-filter:a", _atempo_filter(ratio),
        str(tmp),
    ], desc=f"第 5 步配音二次变速 {target:.2f}x")
    os.replace(tmp, wav)

    adjusted = []
    for seg in segs:
        adjusted.append({
            **seg,
            "start": round(float(seg.get("start", 0)) / ratio, 3),
            "end": round(float(seg.get("end", 0)) / ratio, 3),
        })
    save_json(seg_path, adjusted)
    save_json(meta_path, {"speed": target})
    log("compose", f"配音已二次变速到 {target:.2f}x，字幕时间已同步")
    return adjusted


def _resolve_image_title(wd: Path, cfg: dict) -> str:
    if not cfg.get("image_title_enabled"):
        return ""
    title_path = wd / "image_title.txt"
    explicit = str(cfg.get("image_title") or "").strip()
    if explicit:
        title_path.write_text(explicit, encoding="utf-8")
        return explicit
    if title_path.is_file():
        cached = title_path.read_text(encoding="utf-8").strip()
        if cached:
            return cached
    translated = load_json(wd / "translated.json") or []
    full_text = "".join(str(item.get("zh") or "") for item in translated)
    if not full_text.strip():
        return ""
    generated = publish_mod.gen_title(full_text).strip()
    if generated:
        title_path.write_text(generated, encoding="utf-8")
        log("compose", f"自动生成画面主标题：{generated}")
    return generated


# ----------------------------------------------------------------- 5) 合成
def run_compose(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    segs = _apply_compose_audio_speed(wd, cfg.get("audio_speed", 1.0))
    if not segs:
        segs = load_json(wd / "dub_segments.json") or load_json(wd / "translated.json")
    bilingual = bool(cfg.get("bilingual", False))
    presets = {p["key"]: p for p in config.SUB_PRESETS}
    style = dict(presets.get(cfg.get("preset", config.SUB_PRESET), presets["classic"]))
    try:
        fontsize = int(cfg.get("fontsize") or 0)
        if 24 <= fontsize <= 140:
            style["fontsize"] = fontsize
    except (TypeError, ValueError):
        pass
    if cfg.get("position") in ("bottom", "middle", "top"):
        style["position"] = cfg["position"]
    style["play_res_x"] = int(cfg.get("canvas_width", 1920))
    style["play_res_y"] = int(cfg.get("canvas_height", 1080))
    if cfg.get("subtitle_marginv") is not None:
        style["marginv"] = int(cfg["subtitle_marginv"])
    if cfg.get("subtitle_wrap_mode") in ("balanced", "wide", "fixed"):
        style["wrap_mode"] = cfg["subtitle_wrap_mode"]
    try:
        max_chars_per_line = int(cfg.get("subtitle_max_chars_per_line") or 0)
        if 1 <= max_chars_per_line <= 120:
            style["max_chars_per_line"] = max_chars_per_line
    except (TypeError, ValueError):
        pass
    log("compose", f"字幕样式：{style['name']}，字号 {style['fontsize']}，位置 {style['position']}")
    subtitles.build(segs, wd, bilingual=bilingual, style=style)
    ass = wd / "subs.ass"

    mode = cfg.get("mode", "original")
    if mode == "original" and not (wd / "source.mp4").exists():
        mode = "image"                          # 没有视频只能用图片
        log("compose", "无原视频，自动改用图片合成")
    image = None
    if mode == "image" and (cfg.get("file") or cfg.get("image")):
        image = Path(cfg.get("file") or cfg.get("image"))
        if not image.is_file():
            raise RuntimeError(f"上传的合成图片不存在：{image}")
        log("compose", f"使用上传图片作为视频画面：{image.name}")

    cover = None
    if cfg.get("cover_original"):
        def _f(val, default, lo, hi):
            try:
                return max(lo, min(hi, float(val)))
            except (TypeError, ValueError):
                return default
        opacity = _f(cfg.get("cover_opacity"), config.SUB_COVER_OPACITY, 0.0, 1.0)
        height = _f(cfg.get("cover_height"), config.SUB_COVER_HEIGHT, 0.05, 0.45)
        cover = {"height": height, "opacity": opacity, "color": config.SUB_COVER_COLOR}
        log("compose", f"底部色条遮挡原片烧死字幕：开启（不透明度 {opacity:.2f}，高度 {height:.0%}）")

    out = final_path(job_id)
    if mode == "ai_paint":
        return _compose_ai_paint(wd, cfg, segs, (ass if cfg.get("burn", True) else None), out)
    image_title = _resolve_image_title(wd, cfg) if mode == "image" else ""
    compose_mod.compose(
        mode=mode, work_dir=wd, audio=wd / "dub.wav",
        ass=(ass if cfg.get("burn", True) else None), out_path=out,
        title=image_title, bg=cfg.get("bg", "#10131a"), bg2=cfg.get("bg2", "#1d2740"),
        image=image,
        title_x=float(cfg.get("image_title_x", 0.54)),
        title_y=float(cfg.get("image_title_y", 0.18)),
        title_font_size=int(cfg.get("image_title_font_size", 144)),
        title_width=float(cfg.get("image_title_width", 0.38)),
        canvas_width=int(cfg.get("canvas_width", 1920)),
        canvas_height=int(cfg.get("canvas_height", 1080)),
        cover=cover,
    )
    return {
        "mode": mode,
        "output": str(out),
        "image_title": image_title,
        "canvas": [
            int(cfg.get("canvas_width", 1920)),
            int(cfg.get("canvas_height", 1080)),
        ],
    }


def _cfg_num(cfg: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(cfg.get(key, default))))
    except (TypeError, ValueError):
        return default


def _compose_ai_paint(wd: Path, cfg: dict, segs: list[dict], ass: Path | None,
                      out: Path) -> dict:
    """AI 手绘模式：Claude 按场景作画（带循环动画）→ 按时间轴加运镜拼成视频。"""
    width = int(cfg.get("canvas_width", 1920))
    height = int(cfg.get("canvas_height", 1080))
    image_title = _resolve_image_title(wd, cfg)
    full_text = "".join(str(s.get("zh") or "") for s in segs)
    topic = image_title or full_text[:300]
    scenes = scene_art.generate_scenes(
        segs, wd,
        style=cfg.get("art_style"),
        width=width, height=height,
        scene_seconds=_cfg_num(cfg, "art_scene_seconds", 40, 10, 300),
        max_scenes=int(_cfg_num(cfg, "art_max_scenes", 48, 1, 200)),
        concurrency=int(_cfg_num(cfg, "art_concurrency", 6, 1, 12)),
        time_budget_min=_cfg_num(cfg, "art_time_budget", 18, 1, 120),
        topic=topic,
        animate=bool(cfg.get("art_animate", True)),
    )
    title_png = None
    if image_title:
        title_png = publish_mod.make_title_overlay(
            wd / "scenes" / "title_overlay.png", image_title,
            x=float(cfg.get("image_title_x", 0.54)),
            y=float(cfg.get("image_title_y", 0.18)),
            font_size=int(cfg.get("image_title_font_size", 144)),
            box_width=float(cfg.get("image_title_width", 0.38)),
            target_size=(width, height),
        )
    compose_mod.scenes_to_video(scenes, wd / "dub.wav", ass, out, width=width, height=height,
                                title_png=title_png)
    return {
        "mode": "ai_paint",
        "output": str(out),
        "image_title": image_title,
        "art_style": scene_art.normalize_style(cfg.get("art_style")),
        "scenes": len(scenes),
        "animated": sum(1 for sc in scenes if sc.get("loop")),
        "canvas": [width, height],
    }


# ----------------------------------------------------------------- 6) 保存信息归档
def run_publish(job_id: str, cfg: dict) -> dict:
    wd = work_dir_of(job_id)
    cover_title = str(cfg.get("cover_title") or "").strip()
    meta = publish_mod.prepare(
        work_dir=wd, final_video=final_path(job_id),
        platform=cfg.get("platform", "bilibili"),
        mode="archive",
        tid=cfg.get("tid"),
        copyright=cfg.get("copyright"),
        archive_dir=cfg.get("archive_dir"),
        cover_image=cfg.get("file") or cfg.get("cover_image"),
        cover_title=cover_title,
        cover_x=cfg.get("cover_x", config.DEFAULT_COVER_TITLE_X),
        cover_y=cfg.get("cover_y", config.DEFAULT_COVER_TITLE_Y),
        cover_font_size=cfg.get("cover_font_size", config.DEFAULT_COVER_TITLE_FONT_SIZE),
        cover_width=cfg.get("cover_width", config.DEFAULT_COVER_TITLE_WIDTH),
        douyin_cover_header=str(cfg.get("douyin_cover_header") or "").strip(),
        archive_profile=profiles.normalize_profile(
            cfg.get("archive_profile") or cfg.get("profile")
        ),
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
