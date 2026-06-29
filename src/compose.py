"""第 5 步：把（图片或原视频）+ 中文配音 + 中文字幕 合成为最终 mp4。

两种底图模式：
- original：在原视频画面上烧字幕、替换配音（视频在配音结束处截断）。
- image：用一张图片（当前为纯色/渐变 + 标题）循环成视频，配音多长视频多长。
  预留 AI 生图接口：把生成好的图片路径传进来即可。
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from . import config, mux
from .utils import _NO_WINDOW, log, run

_FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
_FONT_REG = r"C:\Windows\Fonts\msyh.ttc"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _wrap_cover_title(draw, title: str, font, max_w: int, max_lines: int = 3) -> list[str]:
    title = (title or "").strip().replace("，", " ").replace(",", " ")
    explicit = [x.strip() for x in title.splitlines() if x.strip()]
    if explicit:
        source = explicit
    else:
        source = [title]

    lines: list[str] = []
    for part in source:
        cur = ""
        for ch in part:
            trial = cur + ch
            if cur and draw.textlength(trial, font=font) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur = trial
        if cur:
            lines.append(cur)

    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + ["".join(lines[max_lines - 1:])]
    return [x.strip() for x in lines if x.strip()][:max_lines]


def _fit_cover_lines(draw, title: str, max_w: int, max_h: int,
                     start_size: int = 142, max_lines: int = 3):
    from PIL import ImageFont

    for size in range(start_size, 58, -4):
        font = ImageFont.truetype(_FONT_BOLD, size)
        lines = _wrap_cover_title(draw, title, font, max_w, max_lines=max_lines)
        if not lines:
            continue
        line_h = int(size * 1.05)
        widest = max(draw.textlength(line, font=font) for line in lines)
        total_h = line_h * len(lines)
        if widest <= max_w and total_h <= max_h:
            return font, lines, line_h
    font = ImageFont.truetype(_FONT_BOLD, 58)
    return font, _wrap_cover_title(draw, title, font, max_w, max_lines=max_lines), 64


def _draw_cover_title(draw, title: str, w: int, h: int) -> None:
    """画短视频封面大字：黄/白文字、粗黑描边、轻微阴影。"""
    max_w = int(w * 0.72)
    max_h = int(h * 0.56)
    font, lines, line_h = _fit_cover_lines(draw, title, max_w, max_h)
    if not lines:
        return

    x = int(w * 0.07)
    y = int(h * 0.08)
    colors = [(255, 235, 0), (255, 255, 255), (255, 235, 0)]
    stroke = max(5, int(font.size * 0.075))

    for i, line in enumerate(lines):
        fill = colors[i % len(colors)]
        yy = y + i * line_h
        # 阴影先略偏移，再粗描边，保证任何首帧上都清楚。
        draw.text((x + stroke, yy + stroke), line, font=font, fill=(0, 0, 0, 170))
        draw.text((x, yy), line, font=font, fill=fill,
                  stroke_width=stroke, stroke_fill=(8, 8, 8))


def make_cover(title: str, out_png: Path, *, w: int = 1920, h: int = 1080,
               bg: str = "#10131a", bg2: str | None = "#1d2740",
               subtitle: str = "") -> Path:
    """生成纯色/竖直渐变背景 + 短视频大字标题的封面图。"""
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

    title = (title or "").strip() or "AI 配音视频"
    # 左上暗底，让大字像视频封面而不是海报标题。
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay, "RGBA")
    for x in range(int(w * 0.72)):
        a = int(115 * (1 - x / max(int(w * 0.72), 1)))
        od.line([(x, 0), (x, h)], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img, "RGBA")
    _draw_cover_title(draw, title, w, h)

    if subtitle:
        max_w = int(w * 0.72)
        fs = _fit_font(_FONT_REG, subtitle, max_w, 48)
        x = int(w * 0.07)
        y = int(h * 0.68)
        draw.text((x, y), subtitle, font=fs, fill=(230, 235, 245),
                  stroke_width=2, stroke_fill=(0, 0, 0))

    img.convert("RGB").save(out_png)
    log("compose", f"封面图已生成：{out_png.name}")
    return out_png


def cover_from_frame(video: Path, title: str, out_png: Path, *, at: float = 0.35) -> Path:
    """从原视频首帧附近抽图，叠短视频大字标题，生成可直接上传的封面。"""
    from PIL import Image, ImageDraw
    from .utils import _NO_WINDOW, media_duration
    import subprocess as _sp

    dur = media_duration(video)
    ts = min(max(at, 0.05), max(dur * 0.08, 0.05)) if dur else at
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
    # 左侧暗角渐变，保留人物/主体，同时保证黄白大字可读。
    for x in range(int(w * 0.74)):
        a = int(150 * (1 - x / max(int(w * 0.74), 1)))
        draw.line([(x, 0), (x, h)], fill=(0, 0, 0, a))
    for y in range(int(h * 0.32)):
        a = int(70 * (1 - y / max(int(h * 0.32), 1)))
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, a))

    title = (title or "").strip()
    if title:
        _draw_cover_title(draw, title, w, h)

    img.save(out_png)
    try:
        frame.unlink()
    except OSError:
        pass
    log("compose", f"封面图已生成：{out_png.name}")
    return out_png


def _escape_subs(ass: Path) -> str:
    return str(ass).replace("\\", "/").replace(":", "\\:")


def _nvenc_available() -> bool:
    try:
        out = subprocess.run(
            [config.FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
        )
        return "h264_nvenc" in (out.stdout or "")
    except Exception:
        return False


def image_to_video(image: Path, audio: Path, ass: Path | None, out_path: Path) -> Path:
    """静态图片 + 配音 → 视频，时长跟随音频；可烧录字幕。"""
    vf = "scale=1920:1080:force_original_aspect_ratio=decrease,"\
         "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    if ass:
        vf += f",subtitles='{_escape_subs(ass)}'"

    base_cmd = [
        config.FFMPEG, "-y",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
        "-vf", vf,
    ]
    tail = ["-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path)]
    x264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tune", "stillimage"]

    if _nvenc_available():
        nvenc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-pix_fmt", "yuv420p"]
        try:
            log("compose", "尝试 NVENC 图片合成")
            run(base_cmd + nvenc + tail, desc="图片 + 配音 合成视频（NVENC）")
            log("compose", f"输出完成：{out_path}")
            return out_path
        except RuntimeError as e:
            log("compose", f"NVENC 图片合成不可用（{str(e)[:160]}…），回退 libx264")

    run(base_cmd + x264 + tail, desc="图片 + 配音 合成视频（libx264）")
    log("compose", f"输出完成：{out_path}")
    return out_path


def compose(*, mode: str, work_dir: Path, audio: Path, ass: Path | None,
            out_path: Path, title: str = "", bg: str = "#10131a",
            bg2: str | None = "#1d2740", image: Path | None = None) -> Path:
    """统一入口。mode: original | image。封面生成由第 6 步归档负责。"""
    cover_png = work_dir / "cover.png"
    if mode == "image":
        cover = image or make_cover(title, cover_png, bg=bg, bg2=bg2)
        return image_to_video(cover, audio, ass, out_path)
    # original：复用 mux（原视频 + 配音 + 烧字幕，-shortest 截断）
    return mux.mux(work_dir / "source.mp4", audio, ass, out_path)
