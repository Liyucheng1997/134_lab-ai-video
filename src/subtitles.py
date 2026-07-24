"""第 5 步（上）：生成中文字幕。

同时产出：
- subs.srt  软字幕（留档/可选外挂）
- subs.ass  硬字幕（带样式，供 ffmpeg 烧录）
"""
from __future__ import annotations

from pathlib import Path

from . import config
from .utils import ass_timestamp, log, srt_timestamp


def _wrap(
    text: str,
    max_per_line: int = 18,
    mode: str = "balanced",
) -> str:
    """中文按画布允许的字数折行，优先在标点处断开。"""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_per_line:
        return text
    max_per_line = max(1, int(max_per_line))
    normalized_mode = str(mode or "").strip().lower()
    fixed = normalized_mode == "fixed"
    wide = normalized_mode == "wide"
    punctuation = "，。！？、；：,.!?;: "
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_per_line:
        if fixed:
            cut = max_per_line
        else:
            if wide:
                target = max_per_line
                lower = max(1, target - 4)
                upper = min(target, len(remaining) - 1)
            else:
                line_count = (len(remaining) + max_per_line - 1) // max_per_line
                target = (len(remaining) + line_count - 1) // line_count
                lower = max(1, target - 2)
                upper = min(max_per_line, len(remaining) - 1, target + 2)
            candidates = [
                index
                for index in range(lower, upper + 1)
                if remaining[index - 1] in punctuation
            ]
            cut = (
                min(candidates, key=lambda index: abs(index - target))
                if candidates
                else target
            )
        lines.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        lines.append(remaining)
    return "\\N".join(lines)


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
PlayResX: {play_res_x}
PlayResY: {play_res_y}
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
    align, default_marginv = _ALIGN.get(
        s.get("position", config.SUB_POSITION), _ALIGN["bottom"]
    )
    marginv = int(s.get("marginv", default_marginv))
    play_res_x = int(s.get("play_res_x", 1920))
    play_res_y = int(s.get("play_res_y", 1080))
    wrap_mode = str(s.get("wrap_mode") or "balanced")
    if s.get("max_chars_per_line") is not None:
        max_chars_per_line = max(1, int(s["max_chars_per_line"]))
    elif play_res_y > play_res_x:
        # 竖屏不能沿用横屏的 18 字单行；为左右安全边距和描边留出空间。
        usable_width = max(1, play_res_x - 180)
        max_chars_per_line = max(
            6,
            min(18, int(usable_width / max(1, size * 1.08))),
        )
    else:
        max_chars_per_line = 18
    # PlayRes 固定 1080p，字号为该画布下的绝对值；libass 会随实际分辨率自动缩放
    out = [_ASS_HEADER.format(
        play_res_x=play_res_x,
        play_res_y=play_res_y,
        font=font,
        size=size,
        primary=primary,
        outline=outline,
        bold=bold,
        align=align,
        marginv=marginv,
    )]
    for s in segments:
        zh = _wrap(
            s.get("zh", "").strip(),
            max_chars_per_line,
            mode=wrap_mode,
        )
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
