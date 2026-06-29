"""统一编排：URL 或本地文件 → 中文配音+硬字幕 mp4。CLI 与 Web 服务共用。"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from . import config, download, transcribe, translate, tts, subtitles, mux
from .utils import log, media_duration

# UI 展示用的步骤定义（顺序即流程顺序）
STEPS = [
    ("download", "下载 / 导入"),
    ("asr", "语音识别"),
    ("translate", "翻译"),
    ("tts", "中文配音"),
    ("subs", "生成字幕"),
    ("mux", "合成输出"),
]


def make_job_id(url: str | None, file_name: str | None) -> str:
    if url:
        return download.video_id_from_url(url)
    seed = (file_name or "upload") + str(time.time())
    return "up_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:9]


# 重要：faster-whisper(ctranslate2) 与 F5-TTS(torch) 同进程加载 CUDA 可能互相影响或崩溃。
# 访问冲突崩溃。因此把流程拆成两个阶段，由 pipeline.py 放到各自独立的子进程里跑：
#   prep   = 下载/导入 + 识别 + 翻译   （只用 ctranslate2）
#   render = 配音 + 字幕 + 合成        （只用 torch）
# 两阶段通过 work/<id>/ 下的缓存文件衔接。


def run_prep(
    *,
    url: str | None = None,
    local_file: Path | None = None,
    job_id: str | None = None,
    whisper_model: str | None = None,
    language: str | None = None,
) -> str:
    """阶段一：下载/导入 → 识别 → 翻译。结果写入 work/<id>/。返回 job_id。"""
    if whisper_model:
        config.WHISPER_MODEL = whisper_model
    if not url and not local_file:
        raise ValueError("必须提供 url 或 local_file 其一")

    job_id = job_id or make_job_id(url, local_file.name if local_file else None)
    work_dir = config.WORK_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    log("pipeline", f"任务 {job_id} · 准备阶段")

    if local_file:
        download.prepare_local(local_file, work_dir)
    else:
        download.download(url, work_dir)

    audio = work_dir / "source.wav"
    segments = transcribe.transcribe(audio, work_dir, language=language)
    if not segments:
        raise RuntimeError("没有识别到任何语音")
    translate.translate(segments, work_dir)
    return job_id


def run_render(*, job_id: str, voice: str | None = None, out_path: str | None = None) -> Path:
    """阶段二：配音 → 字幕 → 合成。读取 prep 的缓存，输出最终 mp4。"""
    if voice:
        config.TTS_VOICE = voice
    work_dir = config.WORK_DIR / job_id
    from .utils import load_json
    segments = load_json(work_dir / "translated.json")
    if not segments:
        raise RuntimeError("缺少翻译结果，请先运行准备阶段")

    video = work_dir / "source.mp4"
    # 顺序拼接配音，返回去掉空白后的新计时；字幕按新计时生成
    dub, retimed = tts.synthesize(segments, work_dir)

    subtitles.write_srt(retimed, work_dir)
    ass = subtitles.write_ass(retimed, work_dir)

    out_path = out_path or str(work_dir / "final.mp4")
    mux.mux(video, dub, ass, out_path)
    log("pipeline", f"任务 {job_id} 完成 → {out_path}")
    return Path(out_path)
