"""第 6 步：生成保存信息并归档。

默认根据中文译文生成 B 站标题/简介/标签/分区建议，连同成片、封面一起放进
output/<日期>_<简短标题>/。同时在 work/<id>/publish/metadata.json
保留一份索引，供网页预览和断点状态判断使用。
"""
from __future__ import annotations

import json
import re
import subprocess
import shutil
import sys
from datetime import datetime
from pathlib import Path

import requests

from . import config
from .utils import load_json, log, save_json

_FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

_SYS = (
    "你是中文短视频运营。根据给定的中文视频文案，生成适合平台保存/发布时使用的信息。"
    "只输出 JSON：{\"title\":\"不超过80字的吸引人标题\","
    "\"project_title\":\"4-12个中文字符的简短项目名，用于文件夹命名\","
    "\"desc\":\"简介，2-4句，可含简单换行\","
    "\"tags\":[\"标签1\",\"标签2\",\"...最多10个...\"],"
    "\"partition\":\"建议分区，如 知识/科技/生活/影视 等\"}。不要多余文字。"
)

_PARTITION_TID = {
    "知识": 36,
    "科技": 188,
    "生活": 160,
    "影视": 181,
    "人文历史": 124,
    "社科": 228,
    "法律": 228,
    "心理": 228,
    "科学科普": 201,
    "财经": 207,
    "商业": 207,
}


def _gen_metadata(text: str) -> dict:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，无法生成保存信息。")
    resp = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": _SYS},
                         {"role": "user", "content": text[:4000]}],
            "temperature": 1.1,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])


def gen_title(text: str) -> str:
    """用 DeepSeek 生成一句醒目的中文封面标题；无 key 时回退到首句。"""
    fallback = (text.strip().replace("\n", " ").split("。")[0] or "话语权丢失")[:16]
    if not config.DEEPSEEK_API_KEY:
        return fallback
    try:
        resp = requests.post(
            f"{config.DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": config.DEEPSEEK_MODEL,
                  "messages": [
                      {"role": "system", "content": "你是短视频封面大字标题策划。"
                       "参考B站/YouTube中文封面：短、狠、强反差，适合放在首帧大字上。"
                       "根据文案生成8-16个中文字符的封面标题，可带一个问号或感叹号；"
                       "优先使用情绪词、反差词、关键结论，不要书名号引号，只输出标题。"},
                      {"role": "user", "content": text[:2000]}],
                  "temperature": 1.1, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        t = resp.json()["choices"][0]["message"]["content"].strip().strip("《》\"'").splitlines()[0]
        return (t or fallback)[:18]
    except Exception:
        return fallback


def _tid_from_partition(partition: str, fallback: int | None = None) -> int:
    text = partition or ""
    for key, tid in _PARTITION_TID.items():
        if key in text:
            return tid
    return int(fallback or config.BILIBILI_TID)


def _theme_from_meta(meta: dict, full_text: str) -> str:
    """从标题/分区/正文里取 4-8 个中文字符，作为归档目录主题。"""
    for cand in (meta.get("title", ""), meta.get("partition", ""), full_text):
        text = "".join(re.findall(r"[\u4e00-\u9fff]", str(cand)))
        if len(text) >= 4:
            return text[:8]
    text = "".join(re.findall(r"[\u4e00-\u9fff]", str(meta.get("title", ""))))
    return (text or "视频主题")[:8]


def _safe_dir_part(text: str, fallback: str = "视频主题") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(text or ""))
    text = re.sub(r"\s+", "", text).strip(". ")
    return (text or fallback)[:20]


def _project_title_from_meta(meta: dict, full_text: str) -> str:
    for cand in (meta.get("project_title", ""), meta.get("title", ""), full_text):
        text = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", str(cand)))
        if len(text) >= 4:
            return _safe_dir_part(text[:12])
    return _safe_dir_part(_theme_from_meta(meta, full_text))


def _next_archive_dir(theme: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    theme = _safe_dir_part(theme)
    base = config.OUTPUT_DIR / f"{stamp}_{theme}"
    archive_dir = base
    suffix = 2
    while archive_dir.exists():
        archive_dir = config.OUTPUT_DIR / f"{base.name}_{suffix:02d}"
        suffix += 1
    archive_dir.mkdir(parents=True)
    return archive_dir


def _rename_archive_dir(archive_dir: Path, project_title: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    target_base = archive_dir.parent / f"{stamp}_{_safe_dir_part(project_title)}"
    target = target_base
    suffix = 2
    while target.exists() and target.resolve() != archive_dir.resolve():
        target = archive_dir.parent / f"{target_base.name}_{suffix:02d}"
        suffix += 1
    if target.resolve() != archive_dir.resolve():
        try:
            archive_dir.rename(target)
        except OSError as e:
            # Windows 上如果资源管理器/播放器正在预览目录，整目录 rename 可能被拒绝。
            # 复制到新项目目录后继续归档，避免第 6 步因目录名调整失败。
            log("archive", f"项目目录被占用，改用复制方式生成新目录：{e}")
            shutil.copytree(archive_dir, target, dirs_exist_ok=True)
            try:
                shutil.rmtree(archive_dir)
            except OSError:
                log("archive", f"旧目录暂时无法删除，稍后可手动清理：{archive_dir}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_archive_files(archive_dir: Path, meta: dict) -> None:
    (archive_dir / "title.txt").write_text(meta.get("title", ""), encoding="utf-8")
    (archive_dir / "description.txt").write_text(meta.get("desc", ""), encoding="utf-8")
    tags = meta.get("tags") or []
    (archive_dir / "tags.txt").write_text("\n".join(map(str, tags)), encoding="utf-8")
    info = [
        f"# {meta.get('title', '')}",
        "",
        "## 简介",
        meta.get("desc", ""),
        "",
        "## 标签",
        "、".join(map(str, tags)),
        "",
        "## 分区",
        f"{meta.get('partition', '')} / TID {meta.get('tid', '')}",
    ]
    (archive_dir / "publish_info.md").write_text("\n".join(info), encoding="utf-8")


def _cover_font(size: int):
    from PIL import ImageFont

    size = max(48, min(260, int(size or 132)))
    try:
        return ImageFont.truetype(_FONT_BOLD, size)
    except OSError:
        return ImageFont.load_default()


def _wrap_title(draw, title: str, font, max_w: int, max_lines: int = 3) -> list[str]:
    title = (title or "").strip().replace("，", " ").replace(",", " ")
    if not title:
        return []
    lines: list[str] = []
    for part in [x.strip() for x in title.splitlines() if x.strip()] or [title]:
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
    return lines[:max_lines]


def make_cover_from_image(src: Path, out_png: Path, title: str, *,
                          x: float = 0.07, y: float = 0.10,
                          font_size: int = 132, box_width: float = 0.62) -> Path:
    """用户上传底图 + 标题叠字，输出 16:9 封面。坐标/宽度为 0~1 归一化值。"""
    from PIL import Image, ImageDraw

    img = Image.open(src).convert("RGB")
    target_w, target_h = 1920, 1080
    scale = max(target_w / img.width, target_h / img.height)
    nw, nh = int(img.width * scale), int(img.height * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = max(0, (nw - target_w) // 2)
    top = max(0, (nh - target_h) // 2)
    img = img.crop((left, top, left + target_w, top + target_h)).convert("RGBA")

    overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay, "RGBA")
    box_w_px = int(target_w * max(0.20, min(0.92, float(box_width or 0.62))))
    x_px = int(target_w * max(0, min(0.95, float(x or 0))))
    y_px = int(target_h * max(0, min(0.88, float(y or 0))))
    pad = max(20, int(font_size * 0.22))
    od.rounded_rectangle(
        [max(0, x_px - pad), max(0, y_px - pad),
         min(target_w, x_px + box_w_px + pad), min(target_h, y_px + int(font_size * 3.7))],
        radius=22,
        fill=(0, 0, 0, 92),
    )
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img, "RGBA")
    font = _cover_font(font_size)
    lines = _wrap_title(draw, title, font, box_w_px)
    line_h = int(font.size * 1.08)
    stroke = max(4, int(font.size * 0.075))
    colors = [(255, 235, 0), (255, 255, 255), (255, 235, 0)]
    for i, line in enumerate(lines):
        yy = y_px + i * line_h
        draw.text((x_px + stroke, yy + stroke), line, font=font, fill=(0, 0, 0, 170))
        draw.text((x_px, yy), line, font=font, fill=colors[i % len(colors)],
                  stroke_width=stroke, stroke_fill=(8, 8, 8))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_png)
    log("archive", f"封面图已生成：{out_png.name}")
    return out_png


def _upload_bilibili(meta: dict, *, final_video: Path, cover: Path | None,
                     tid: int, copyright: int) -> dict:
    cookie_file = Path(config.BILIBILI_COOKIE_FILE)
    if not cookie_file.exists():
        raise RuntimeError(
            f"缺少 B 站登录态：{cookie_file}。请先运行 python bili_login.py 扫码登录。"
        )
    if not final_video.exists():
        raise RuntimeError(f"找不到成片：{final_video}")

    log("publish", f"B站自动投稿：{(meta.get('title') or final_video.stem)[:80]}")
    meta = {**meta, "tid": int(tid), "copyright": int(copyright)}
    lines = list(dict.fromkeys([
        config.BILIBILI_UPLOAD_LINE,
        *getattr(config, "BILIBILI_UPLOAD_FALLBACK_LINES", []),
    ]))
    errors: list[str] = []
    temp_meta = final_video.parent / "upload_meta.json"
    temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    for line in lines:
        out_json = final_video.parent / f"upload_result_{line.lower()}.json"
        out_json.unlink(missing_ok=True)
        cmd = [
            sys.executable, "-u", str(config.BASE_DIR / "bili_upload_once.py"),
            "--meta", str(temp_meta),
            "--video", str(final_video),
            "--line", line,
            "--out", str(out_json),
        ]
        if cover and cover.exists():
            cmd += ["--cover", str(cover)]
        log("publish", f"尝试 B站上传线路：{line}（最多等待 {config.BILIBILI_UPLOAD_TIMEOUT}s）")
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, _ = proc.communicate(timeout=config.BILIBILI_UPLOAD_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                proc.kill()
            msg = f"{line}: 上传超时"
            errors.append(msg)
            log("publish", f"线路超时，换下一条：{line}")
            continue

        for row in (stdout or "").splitlines():
            if row.strip():
                log("publish", row[-500:])
        if proc.returncode == 0 and out_json.exists():
            ret = json.loads(out_json.read_text(encoding="utf-8"))
            log("publish", f"B站投稿提交成功，线路：{line}")
            return ret
        detail = ""
        for row in reversed((stdout or "").splitlines()):
            if "[error]" in row or "失败" in row:
                detail = row.split("] ", 2)[-1].strip()
                break
        msg = f"{line}: {detail or f'退出码 {proc.returncode}'}"
        errors.append(msg)
        if "code=601" in msg or "上传视频过快" in msg:
            raise RuntimeError("B站视频上传被限流：" + msg)
        log("publish", f"线路失败，换下一条：{msg}")

    raise RuntimeError("B站视频上传失败，已尝试所有线路：" + " | ".join(errors))


def prepare(*, work_dir: Path, final_video: Path, platform: str = "bilibili",
            mode: str = "prepare", tid: int | None = None,
            copyright: int | None = None,
            archive_dir: Path | str | None = None,
            cover_image: Path | str | None = None,
            cover_title: str = "",
            cover_x: float = config.DEFAULT_COVER_TITLE_X,
            cover_y: float = config.DEFAULT_COVER_TITLE_Y,
            cover_font_size: int = config.DEFAULT_COVER_TITLE_FONT_SIZE,
            cover_width: float = config.DEFAULT_COVER_TITLE_WIDTH) -> dict:
    """生成保存信息包并归档，返回 metadata dict。"""
    translated = load_json(work_dir / "translated.json") or []
    full_text = "".join(s.get("zh", "") for s in translated)[:4000]
    if not full_text.strip():
        raise RuntimeError("缺少翻译文案，请先完成翻译步骤。")
    if not final_video.exists():
        raise RuntimeError(f"找不到成片：{final_video}")

    log("archive", f"生成保存信息…")
    raw_meta = _gen_metadata(full_text)
    short_cover_title = (cover_title or "").strip() or gen_title(full_text)

    project_title = _project_title_from_meta(raw_meta, full_text)
    theme = _theme_from_meta(raw_meta, full_text)
    fixed_archive_dir = archive_dir is not None
    archive_dir = Path(archive_dir) if archive_dir else _next_archive_dir(project_title)
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        rel_work_dir = work_dir.resolve().relative_to(archive_dir.resolve())
    except ValueError:
        rel_work_dir = None
    try:
        rel_final_video = final_video.resolve().relative_to(archive_dir.resolve())
    except ValueError:
        rel_final_video = None
    if not fixed_archive_dir:
        archive_dir = _rename_archive_dir(archive_dir, project_title)
    if rel_work_dir is not None:
        work_dir = archive_dir / rel_work_dir
    if rel_final_video is not None:
        final_video = archive_dir / rel_final_video

    pub_dir = work_dir / "publish"
    pub_dir.mkdir(exist_ok=True)

    video_dst = archive_dir / "video.mp4"
    shutil.copy(final_video, video_dst)

    uploaded_cover = Path(cover_image) if cover_image else None
    if not (uploaded_cover and uploaded_cover.exists()) and config.DEFAULT_COVER_IMAGE.exists():
        uploaded_cover = config.DEFAULT_COVER_IMAGE
    if uploaded_cover and uploaded_cover.exists():
        cover = make_cover_from_image(
            uploaded_cover,
            work_dir / "cover.png",
            short_cover_title,
            x=float(cover_x if cover_x is not None else config.DEFAULT_COVER_TITLE_X),
            y=float(cover_y if cover_y is not None else config.DEFAULT_COVER_TITLE_Y),
            font_size=int(cover_font_size or config.DEFAULT_COVER_TITLE_FONT_SIZE),
            box_width=float(cover_width if cover_width is not None else config.DEFAULT_COVER_TITLE_WIDTH),
        )
    else:
        cover = work_dir / "cover.png"
    cover_dst = archive_dir / "cover.png"
    if cover.exists():
        shutil.copy(cover, cover_dst)

    selected_tid = int(tid or _tid_from_partition(raw_meta.get("partition", "")))
    selected_copyright = int(copyright or config.BILIBILI_COPYRIGHT)

    meta = {
        "platform": platform,
        "mode": "archive",
        "title": raw_meta.get("title", "")[:80],
        "desc": raw_meta.get("desc", ""),
        "tags": raw_meta.get("tags", [])[:10],
        "partition": raw_meta.get("partition", ""),
        "project_title": project_title,
        "cover_title": short_cover_title,
        "cover_layout": {
            "x": float(cover_x if cover_x is not None else config.DEFAULT_COVER_TITLE_X),
            "y": float(cover_y if cover_y is not None else config.DEFAULT_COVER_TITLE_Y),
            "font_size": int(cover_font_size or config.DEFAULT_COVER_TITLE_FONT_SIZE),
            "width": float(cover_width if cover_width is not None else config.DEFAULT_COVER_TITLE_WIDTH),
        },
        "tid": selected_tid,
        "copyright": selected_copyright,
        "theme": theme,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "archive_dir": str(archive_dir),
        "video": str(video_dst),
        "cover": str(cover_dst) if cover_dst.exists() else "",
        "uploaded": False,
    }

    if mode == "upload":
        log("archive", "已禁用自动上传：本步骤只生成信息并归档。")

    _write_archive_files(archive_dir, meta)
    save_json(archive_dir / "metadata.json", meta)
    save_json(pub_dir / "metadata.json", meta)
    log("archive", f"信息已归档：{archive_dir}")
    return meta
