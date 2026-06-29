"""第 1 步：用 yt-dlp 下载 YouTube 视频，并抽取一条音轨给 ASR。"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from . import config
from .utils import _NO_WINDOW, log, run


def _ytdlp_stream(cmd: list[str]) -> None:
    """流式跑 yt-dlp，把下载百分比/速度实时打到日志（供前端进度条解析）。"""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            creationflags=_NO_WINDOW)
    last = -1
    for line in proc.stdout:
        line = line.strip()
        m = re.search(r"(\d+(?:\.\d+)?)%", line)
        if m and "[download]" in line:
            pct = float(m.group(1))
            if int(pct) != last:                      # 每涨 1% 打一条，避免刷屏
                last = int(pct)
                sp = re.search(r"at\s+([\d.]+\s*\wi?B/s)", line)
                eta = re.search(r"ETA\s+([\d:]+)", line)
                extra = (f" · {sp.group(1)}" if sp else "") + (f" · 剩 {eta.group(1)}" if eta else "")
                log("download", f"下载 {pct:.0f}%{extra}")
        elif "[Merger]" in line:
            log("download", "合并音视频…")
        elif "Destination" in line or line.startswith(("ERROR", "WARNING")):
            log("download", line)
        elif "Downloading webpage" in line or "player API" in line or "Extracting URL" in line:
            log("download", "解析视频信息…")
    if proc.wait() != 0:
        raise RuntimeError("yt-dlp 下载失败")


def video_id_from_url(url: str) -> str:
    """从 URL 提取一个稳定的工作目录名（YouTube id 或 url 哈希）。"""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:11]


def _extract_audio(video: Path, audio: Path) -> None:
    if audio.exists():
        return
    log("download", "抽取 16k 单声道音轨用于识别")
    run([
        config.FFMPEG, "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", str(audio),
    ], desc="ffmpeg 抽音轨")


def prepare_local(src_file: Path, work_dir: Path) -> tuple[Path, Path]:
    """把本地上传的视频转成统一的 source.mp4 + source.wav。返回 (video, audio)。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    video = work_dir / "source.mp4"
    audio = work_dir / "source.wav"
    if not video.exists():
        src_file = Path(src_file)
        if src_file.suffix.lower() == ".mp4":
            log("download", f"使用上传文件：{src_file.name}")
            import shutil
            shutil.copy(src_file, video)
        else:  # 其它容器统一转码为 mp4
            log("download", f"转码上传文件为 mp4：{src_file.name}")
            run([
                config.FFMPEG, "-y", "-i", str(src_file),
                "-c:v", "libx264", "-crf", "20", "-c:a", "aac", str(video),
            ], desc="ffmpeg 转码")
    _extract_audio(video, audio)
    return video, audio


def download(url: str, work_dir: Path) -> tuple[Path, Path]:
    """下载视频到 work_dir，返回 (video_mp4, audio_wav)。已存在则跳过。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    video = work_dir / "source.mp4"
    audio = work_dir / "source.wav"

    if not video.exists():
        log("download", f"下载视频：{url}")
        _ytdlp_stream([
            *config.YT_DLP, "--no-playlist", "--newline",
            "-f", "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4]/b",
            "--merge-output-format", "mp4",
            "--ffmpeg-location", str(Path(config.FFMPEG).parent),
            "-o", str(video),
            url,
        ])
    else:
        log("download", "视频已存在，跳过下载")

    _extract_audio(video, audio)
    return video, audio
