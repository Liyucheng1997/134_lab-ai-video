"""第 1 步：用 yt-dlp 下载 YouTube 视频，并抽取一条音轨给 ASR。"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from . import config
from .utils import _NO_WINDOW, log, run


_BOT_CHECK_RE = re.compile(
    r"confirm you.?re not a bot|Sign in to confirm|429|Too Many Requests",
    re.IGNORECASE,
)
# 拿不到可用视频流，通常是 n challenge 没解开，只剩图片流可选。
_NO_FORMAT_RE = re.compile(
    r"Requested format is not available|Only images are available"
    r"|challenge solving failed",
    re.IGNORECASE,
)


def _download_failure_message(saw_bot_check: bool = False,
                              saw_no_format: bool = False) -> str:
    if saw_bot_check:
        if config.ytdlp_cookie_args():
            return (
                "yt-dlp 下载失败：YouTube 判定为机器人。已配置的 cookie 可能已失效，"
                "请重新导出 cookie 文件；若刚触发 429 限流，等待几十分钟后再试。"
            )
        return (
            "yt-dlp 下载失败：YouTube 要求登录验证。请在 .env 里配置 "
            "YTDLP_COOKIE_FILE（推荐，导出 cookies.txt）或 "
            "YTDLP_COOKIES_FROM_BROWSER（如 chrome、edge、firefox）后重试。"
        )
    if saw_no_format:
        if config.ytdlp_challenge_args():
            return (
                "yt-dlp 下载失败：拿不到可用视频流。EJS 解算脚本可能需要更新，"
                "或该视频有区域/会员限制。"
            )
        return (
            "yt-dlp 下载失败：缺少 JS 运行时或 EJS 解算脚本，YouTube 只返回图片流。"
            "请安装 node 或 deno，并确认 .env 里 YTDLP_REMOTE_COMPONENTS=ejs:github。"
        )
    return "yt-dlp 下载失败"


def _ytdlp_stream(cmd: list[str]) -> None:
    """流式跑 yt-dlp，把下载百分比/速度实时打到日志（供前端进度条解析）。"""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            creationflags=_NO_WINDOW)
    last = -1
    saw_bot_check = False
    saw_no_format = False
    for line in proc.stdout:
        line = line.strip()
        if _BOT_CHECK_RE.search(line):
            saw_bot_check = True
        if _NO_FORMAT_RE.search(line):
            saw_no_format = True
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
        raise RuntimeError(
            _download_failure_message(saw_bot_check, saw_no_format)
        )


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
        if config.ytdlp_cookie_args():
            log("download", "使用 cookie 认证")
        _ytdlp_stream([
            *config.YT_DLP, "--no-playlist", "--newline",
            *config.ytdlp_access_args(),
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
