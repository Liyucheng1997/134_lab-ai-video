"""第 5 步：把（图片或原视频）+ 中文配音 + 中文字幕 合成为最终 mp4。

两种底图模式：
- original：在原视频画面上烧字幕、替换配音（视频在配音结束处截断）。
- image：用一张图片（当前为纯色/渐变 + 标题）循环成视频，配音多长视频多长。
  预留 AI 生图接口：把生成好的图片路径传进来即可。
"""
from __future__ import annotations

from pathlib import Path

from . import config, mux
from .utils import _NO_WINDOW, log, run

_FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
_FONT_REG = r"C:\Windows\Fonts\msyh.ttc"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_cover(title: str, out_png: Path, *, w: int = 1920, h: int = 1080,
               bg: str = "#10131a", bg2: str | None = "#1d2740",
               subtitle: str = "") -> Path:
    """生成纯色/竖直渐变背景 + 居中标题的封面图。"""
    from PIL import Image, ImageDraw, ImageFont

    c1 = _hex_to_rgb(bg)
    if bg2:
        c2 = _hex_to_rgb(bg2)
        img = Image.new("RGB", (w, h))
        px = img.load()
        for y in range(h):
            t = y / max(h - 1, 1)
            row = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))
            for x in range(w):
                px[x, y] = row
    else:
        img = Image.new("RGB", (w, h), c1)

    draw = ImageDraw.Draw(img)

    def _fit_font(path, text, max_w, start):
        size = start
        while size > 24:
            f = ImageFont.truetype(path, size)
            if draw.textlength(text, font=f) <= max_w:
                return f
            size -= 4
        return ImageFont.truetype(path, 24)

    # 标题按宽度折行
    title = (title or "").strip() or "AI 配音视频"
    f = ImageFont.truetype(_FONT_BOLD, 96)
    max_w = int(w * 0.8)
    words, lines, cur = list(title), [], ""
    for ch in words:
        if draw.textlength(cur + ch, font=f) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    lines = lines[:4]

    line_h = 120
    total_h = line_h * len(lines)
    y = (h - total_h) // 2
    for ln in lines:
        x = (w - draw.textlength(ln, font=f)) // 2
        draw.text((x, y), ln, font=f, fill=(255, 255, 255))
        y += line_h

    # 醒目装饰：标题下方一道品牌色横条
    bar_w = min(int(w * 0.18), 360)
    bx = (w - bar_w) // 2
    draw.rectangle([bx, y + 26, bx + bar_w, y + 34], fill=(120, 110, 255))

    if subtitle:
        fs = _fit_font(_FONT_REG, subtitle, max_w, 48)
        x = (w - draw.textlength(subtitle, font=fs)) // 2
        draw.text((x, y + 54), subtitle, font=fs, fill=(190, 198, 220))

    img.save(out_png)
    log("compose", f"封面图已生成：{out_png.name}")
    return out_png


def cover_from_frame(video: Path, title: str, out_png: Path, *, at: float = 3.0) -> Path:
    """从原视频抽一帧做底图，叠暗角渐变 + 醒目标题，生成可直接上传的封面。"""
    from PIL import Image, ImageDraw, ImageFont
    from .utils import _NO_WINDOW, media_duration
    import subprocess as _sp

    dur = media_duration(video)
    ts = min(max(at, 0.5), max(dur * 0.3, 0.5)) if dur else at
    frame = out_png.with_suffix(".frame.png")
    _sp.run([config.FFMPEG, "-y", "-ss", str(ts), "-i", str(video),
             "-frames:v", "1", "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,"
             "crop=1920:1080", str(frame)],
            capture_output=True, creationflags=_NO_WINDOW)
    if not frame.exists():
        return make_cover(title, out_png)

    img = Image.open(frame).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    # 底部暗角渐变，保证标题可读
    for i in range(int(h * 0.45)):
        a = int(210 * (i / (h * 0.45)))
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(8, 10, 18, a))

    title = (title or "").strip()
    if title:
        f = ImageFont.truetype(_FONT_BOLD, 92)
        max_w = int(w * 0.86)
        lines, cur = [], ""
        for ch in title:
            if draw.textlength(cur + ch, font=f) <= max_w:
                cur += ch
            else:
                lines.append(cur); cur = ch
        if cur:
            lines.append(cur)
        lines = lines[:3]
        y = h - 90 - len(lines) * 104
        draw.rectangle([90, y - 20, 90 + min(int(w * 0.16), 320), y - 8], fill=(120, 110, 255))
        for ln in lines:
            draw.text((90, y), ln, font=f, fill=(255, 255, 255),
                      stroke_width=3, stroke_fill=(0, 0, 0))
            y += 104

    img.save(out_png)
    try:
        frame.unlink()
    except OSError:
        pass
    log("compose", f"封面图已生成：{out_png.name}")
    return out_png


def _escape_subs(ass: Path) -> str:
    return str(ass).replace("\\", "/").replace(":", "\\:")


def image_to_video(image: Path, audio: Path, ass: Path | None, out_path: Path) -> Path:
    """静态图片 + 配音 → 视频，时长跟随音频；可烧录字幕。"""
    vf = "scale=1920:1080:force_original_aspect_ratio=decrease,"\
         "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    if ass:
        vf += f",subtitles='{_escape_subs(ass)}'"
    cmd = [
        config.FFMPEG, "-y",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    run(cmd, desc="图片 + 配音 合成视频")
    log("compose", f"输出完成：{out_path}")
    return out_path


def compose(*, mode: str, work_dir: Path, audio: Path, ass: Path | None,
            out_path: Path, title: str = "", bg: str = "#10131a",
            bg2: str | None = "#1d2740", image: Path | None = None) -> Path:
    """统一入口。mode: original | image。合成同时总会输出一张可上传封面 cover.png。"""
    cover_png = work_dir / "cover.png"
    if mode == "image":
        cover = image or make_cover(title, cover_png, bg=bg, bg2=bg2)
        return image_to_video(cover, audio, ass, out_path)
    # original：复用 mux（原视频 + 配音 + 烧字幕，-shortest 截断）
    result = mux.mux(work_dir / "source.mp4", audio, ass, out_path)
    cover_from_frame(work_dir / "source.mp4", title, cover_png)  # 额外产出封面
    return result
