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


def _text(s: dict, bilingual: bool) -> str:
    zh = s.get("zh", "").strip()
    if bilingual and s.get("text"):
        return zh + "\n" + s["text"].strip()
    return zh


def _vtt_ts(seconds: float) -> str:
    return srt_timestamp(seconds).replace(",", ".")


def write_srt(segments: list[dict], work_dir: Path, bilingual: bool = False) -> Path:
    path = work_dir / "subs.srt"
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{srt_timestamp(s['start'])} --> {srt_timestamp(s['end'])}")
        lines.append(_text(s, bilingual))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_vtt(segments: list[dict], work_dir: Path, bilingual: bool = False) -> Path:
    path = work_dir / "subs.vtt"
    lines = ["WEBVTT", ""]
    for s in segments:
        lines.append(f"{_vtt_ts(s['start'])} --> {_vtt_ts(s['end'])}")
        lines.append(_text(s, bilingual))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def cue_list(segments: list[dict]) -> list[dict]:
    """给前端做字幕可视化用：紧凑的 cue 列表。"""
    return [{"start": s["start"], "end": s["end"],
             "zh": s.get("zh", "").strip(), "text": s.get("text", "").strip()}
            for s in segments]


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ZH,{font},{size},{primary},{outline},&H96000000,{bold},0,1,3,1,{align},90,90,{marginv},1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
"""

_ALIGN = {"bottom": (2, 60), "middle": (5, 0), "top": (8, 60)}


def _hex_to_ass(h: str) -> str:
    """#RRGGBB -> ASS &H00BBGGRR（不透明）。"""
    h = (h or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"&H00{bb}{gg}{rr}".upper()


def write_ass(segments: list[dict], work_dir: Path, bilingual: bool = False,
              style: dict | None = None) -> Path:
    path = work_dir / "subs.ass"
    s = style or {}
    font = s.get("font", config.SUB_FONT)
    size = int(s.get("fontsize", config.SUB_FONTSIZE))
    primary = _hex_to_ass(s.get("primary", config.SUB_PRIMARY))
    outline = _hex_to_ass(s.get("outline", config.SUB_OUTLINE))
    bold = -1 if str(s.get("bold", config.SUB_BOLD)) in ("1", "True", "true") else 0
    align, marginv = _ALIGN.get(s.get("position", config.SUB_POSITION), _ALIGN["bottom"])
    # PlayRes 固定 1080p，字号为该画布下的绝对值；libass 会随实际分辨率自动缩放
    out = [_ASS_HEADER.format(font=font, size=size, primary=primary, outline=outline,
                              bold=bold, align=align, marginv=marginv)]
    for s in segments:
        zh = _wrap(s.get("zh", "").strip())
        if not zh:
            continue
        if bilingual and s.get("text"):
            zh = zh + "\\N" + s["text"].strip()
        out.append(
            f"Dialogue: 0,{ass_timestamp(s['start'])},{ass_timestamp(s['end'])},ZH,0,0,0,,{zh}"
        )
    path.write_text("\n".join(out), encoding="utf-8")
    log("subs", f"字幕生成：{path.name}（{len(segments)} 条）")
    return path


def build(segments: list[dict], work_dir: Path, fmt: str = "ass",
          bilingual: bool = False, style: dict | None = None) -> Path:
    """生成字幕（srt/vtt 备份 + 按样式的 ass 用于烧录）。返回 ass 路径。"""
    write_srt(segments, work_dir, bilingual)
    write_vtt(segments, work_dir, bilingual)
    return write_ass(segments, work_dir, bilingual, style=style)
