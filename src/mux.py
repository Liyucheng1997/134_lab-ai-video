"""第 5 步（下）：烧录中文硬字幕 + 替换为中文配音，输出最终 mp4。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config
from .utils import _NO_WINDOW, log, run


def _escape_subs_path(ass_path: Path) -> str:
    """ffmpeg subtitles 滤镜在 Windows 上需要转义盘符冒号和反斜杠。"""
    p = str(ass_path).replace("\\", "/").replace(":", "\\:")
    return p


def _cover_filter(cover: dict | None) -> str:
    """底部全宽半透明色条，遮住原片烧死的字幕。

    返回带尾逗号的 drawbox 滤镜片段（接在 subtitles 之前，让色条在字幕下层），
    cover 为空时返回空串。height 为色条高度占画面比例，y 贴底。
    """
    if not cover:
        return ""
    h = float(cover.get("height", config.SUB_COVER_HEIGHT))
    op = float(cover.get("opacity", config.SUB_COVER_OPACITY))
    color = str(cover.get("color", config.SUB_COVER_COLOR))
    return (f"drawbox=x=0:y=ih*(1-{h:.4f}):w=iw:h=ih*{h:.4f}:"
            f"color={color}@{op:.3f}:t=fill,")


def _nvenc_listed() -> bool:
    try:
        out = subprocess.run(
            [config.FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        )
        return "h264_nvenc" in (out.stdout or "")
    except Exception:
        return False


def _build_cmd(video: Path, dub_audio: Path, vf: str, vcodec: list[str], out_path: Path) -> list[str]:
    return [
        config.FFMPEG, "-y",
        "-i", str(video),
        "-i", str(dub_audio),
        "-vf", vf,
        "-map", "0:v:0", "-map", "1:a:0",
        *vcodec,
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]


def mux(video: Path, dub_audio: Path, ass: Path, out_path: Path,
        cover: dict | None = None) -> Path:
    """合成：原视频画面 + 烧录字幕 + 中文配音音轨。

    优先尝试 NVENC 硬件编码；若驱动/版本不匹配则自动回退到 libx264。
    cover 不为空时，先在底部垫一条半透明色条遮住原片烧死的字幕。
    """
    if cover:
        log("mux", "底部色条遮挡原字幕：开启")
    vf = f"{_cover_filter(cover)}subtitles='{_escape_subs_path(ass)}'"
    x264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]

    if _nvenc_listed():
        # 必须强制 8-bit yuv420p：源若为 10-bit（如 AV1 10-bit），NVENC 默认会输出
        # H.264 High 10（10-bit），B站/微信/多数播放器不兼容，会"格式错误"。
        nvenc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-pix_fmt", "yuv420p"]
        try:
            log("mux", "尝试 NVENC 硬件编码")
            run(_build_cmd(video, dub_audio, vf, nvenc, out_path),
                desc="烧录字幕 + 合成配音（NVENC）")
            log("mux", f"输出完成：{out_path}")
            return out_path
        except RuntimeError as e:
            log("mux", f"NVENC 不可用（{str(e)[:80]}…），回退 libx264")

    log("mux", "使用 libx264 软件编码")
    run(_build_cmd(video, dub_audio, vf, x264, out_path),
        desc="烧录字幕 + 合成配音（libx264）")
    log("mux", f"输出完成：{out_path}")
    return out_path
