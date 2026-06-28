"""第 5 步（上）：生成中文字幕。

同时产出：
- subs.srt  软字幕（留档/可选外挂）
- subs.ass  硬字幕（带样式，供 ffmpeg 烧录）
"""
from __future__ import annotations

from pathlib import Path

from . import config
from .utils import ass_timestamp, log, srt_timestamp


def _wrap(text: str, max_per_line: int = 18) -> str:
    """中文按字数折行，最多两行。"""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_per_line:
        return text
    # 尽量在中点附近的标点处断开
    mid = len(text) // 2
    best = mid
    for off in range(0, mid):
        for j in (mid - off, mid + off):
            if 0 < j < len(text) and text[j] in "，。！？、；：,.!?;: ":
                best = j + 1
                break
        else:
            continue
        break
    return text[:best].strip() + "\\N" + text[best:].strip()


def write_srt(segments: list[dict], work_dir: Path) -> Path:
    path = work_dir / "subs.srt"
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{srt_timestamp(s['start'])} --> {srt_timestamp(s['end'])}")
        lines.append(s.get("zh", "").strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ZH,{font},{size},&H00FFFFFF,&H00000000,&H96000000,0,0,1,3,1,2,80,80,55,1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(segments: list[dict], work_dir: Path) -> Path:
    path = work_dir / "subs.ass"
    # PlayRes 固定 1080p，字号为该画布下的绝对值；libass 会随实际分辨率自动缩放
    out = [_ASS_HEADER.format(font=config.SUB_FONT, size=config.SUB_FONTSIZE)]
    for s in segments:
        zh = _wrap(s.get("zh", "").strip())
        if not zh:
            continue
        out.append(
            f"Dialogue: 0,{ass_timestamp(s['start'])},{ass_timestamp(s['end'])},ZH,,0,0,0,,{zh}"
        )
    path.write_text("\n".join(out), encoding="utf-8")
    log("subs", f"字幕生成：{path.name}（{len(segments)} 条）")
    return path
